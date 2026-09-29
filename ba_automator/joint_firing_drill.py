"""Joint Firing Drill navigation and free qualification of prepared formations.

Input is driven by fresh screen observations. A practice result is never a ticket
receipt, and an unresolved paid entry blocks unrelated restart operations.
"""
from dataclasses import asdict, replace
import json

from .assault_policy import TeamMember
from .actions import record_action
from .club import game_day
from .display import is_supported_size
from .drill_policy import comfortable, fingerprint, validate_plan
from .drill_vision import DrillVision
from . import drill_state
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .assault_assistant import AssaultAssistantMixin
from .vision import decode_frame, read_game_region
from .assault_assistant_vision import verify_retained_assistant, verify_owned_quick
from .vision import decode_native_frame
from .loot_receipts import inspect_receipt


class DrillRunner(AssaultAssistantMixin, ShopRunner):
    task = 'joint_firing_drill'

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault('vision', DrillVision(startup))
        super().__init__(config, device, startup, **kwargs)
        self.assistant = None

    def budget(self):
        if self.clock() - self.started > 3600 or self.actions >= 400:
            self.fail('Joint Firing Drill reached its bounded visit limit')

    def wait(self, kinds, *, timeout=40, predicate=lambda screen: True):
        # Native OCR must leave room for the foreground and ADB display guards.
        kinds = {kinds} if isinstance(kinds, str) else kinds
        end = self.clock() + timeout
        while self.clock() < end:
            frame = self.capture()
            if (frame.screen.kind in kinds and predicate(frame.screen)
                    and frame.capture.deadline - self.clock() >= 2):
                return frame
            self.sleep(.7)
        self.fail(f'Drill did not reach {", ".join(sorted(kinds))}; inspect the local trace')

    def important(self, kind, detail, **fields):
        record_action(self.config, kind, detail, task=self.task, **fields)
        self.phase(detail)

    def swipe(self, frame, start, end, detail):
        if (not frame.capture.is_fresh(self.clock())
                or self.device.foreground_package() != self.config.package):
            self.fail('Drill scroll observation expired')
        self.budget()
        self.journal.record('intent', operation='swipe', detail=detail, start=start, end=end)
        if not self.device.swipe(start, end, duration_ms=650,
                                 deadline=frame.capture.deadline, monotonic=self.clock):
            self.fail('Drill scroll expired before input')
        self.actions += 1
        self.sleep(1)

    def dismiss_result(self, frame):
        self.tap(frame, frame.screen.target, 'Acknowledge the observed Drill result')
        frame = self.wait({'menu', 'tip', 'receipt', 'mock_settlement', 'settlement'}, timeout=90)
        if frame.screen.kind == 'tip':
            self.tap(frame, frame.screen.target, 'Close the post-battle Drill tip')
            frame = self.wait({'menu', 'receipt', 'mock_settlement', 'settlement'}, timeout=90)
        return frame

    def practice_round(self, formation, stage, context, *, verified_team=None):
        """Persist intent before mobilizing; qualify only the observed setup."""
        if verified_team is None:
            formation, team = self.observe_team(formation)
        else:
            # open_unit has just verified every owned slot and the exact lender.
            # Formation portraits omit provenance; re-reading them as owned
            # discards the assistant binding and invalidates a successful mock.
            team = verified_team
            visible = tuple(replace(m, assistant=False, assistant_id=None) for m in team)
            if formation.screen.team != visible:
                self.fail('Drill formation changed after its ownership verification')
        formation, skills = self.starting_skills(formation)
        if formation.screen.team != tuple(replace(m, assistant=False, assistant_id=None) for m in team):
            self.fail('Drill formation changed while verifying starting skills')
        key = fingerprint(team, stage, skills)
        drill_state.begin_practice(self.config, context, key)
        self.journal.record('drill_practice', fingerprint=key, stage=stage,
                            team=[asdict(m) for m in team], skills=skills)
        result = self.battle(formation, stage, prefix=f'practice-{key[:12]}')
        elapsed = result.screen.elapsed_seconds
        remaining = 180 - elapsed if elapsed is not None else None
        drill_state.record_practice(self.config, key, result.screen.won, remaining,
                                    self.config.drill_comfort_seconds)
        qualified = comfortable(result.screen.won, remaining, self.config.drill_comfort_seconds)
        self.important('drill_practice_completed',
                       f'Drill stage {stage}: ' + ('qualified' if qualified else 'needs a stronger team'),
                       stage=stage, won=result.screen.won, remaining_seconds=remaining,
                       qualified=qualified, fingerprint=key)
        frame = self.dismiss_result(result)
        if frame.screen.kind == 'mock_settlement':
            self.journal.save_image('mock-settlement.png', frame.capture.png)
            self.tap(frame, frame.screen.target, 'Close the completed free Drill practice')
            frame = self.menu()
        return frame, team, key, qualified

    def starting_skills(self, formation):
        self.tap(formation, (1200, 270), 'Inspect Drill starting skills')
        frame = self.wait('skills')
        words = read_game_region(frame.capture.png, self.vision.startup, (190, 405, 710, 525))
        empty = [w for w in words if w.normalized == 'empty' and w.confidence >= .9]
        if len(empty) != 5 or any(not any(abs(w.center[0]-x) < 20 for w in empty)
                                 for x in (93, 178, 264, 349, 435)):
            self.fail('Custom Drill starting skills need support; use five empty starting-skill slots')
        self.journal.save_image('starting-skills.png', frame.capture.png)
        # Regional OCR can outlive the input deadline; observe the modal again.
        frame = self.wait('skills')
        self.tap(frame, frame.screen.target, 'Keep the observed Auto starting skills')
        return self.wait('formation', predicate=lambda s: len(s.team) == 6), 'empty-five-auto'

    def observe_team(self, formation):
        """Bind a retained borrow to its lender instead of guessing from level."""
        team = formation.screen.team
        self.tap(formation, formation.screen.quick_target, 'Inspect Drill student ownership')
        quick = self.wait('quick')
        if verify_owned_quick(decode_frame(quick.capture.png), quick.screen.words,
                              startup=self.vision.startup, native_frame=decode_native_frame(quick.capture.png)):
            quick = self.wait('quick')
            self.tap(quick, quick.screen.confirm_target, 'Keep the owned Drill formation')
        else:
            if quick.screen.assistant_target is None:
                self.fail('Drill assistant provenance is unreadable')
            quick = self.wait('quick')
            self.tap(quick, quick.screen.assistant_target, 'Identify the retained Drill assistant')
            frame = self.wait_assistant()
            selected = [card for card in frame.screen.cards if card.selected]
            if len(selected) != 1:
                self.fail('Keep the selected Drill assistant visible in the list before starting')
            card = selected[0]
            slots = [m.slot for m in team if m.student_id == card.member.student_id
                     and m.level == card.member.level and m.stars == card.member.stars]
            if len(slots) != 1 or not verify_retained_assistant(decode_frame(frame.capture.png), card, slots[0]):
                self.fail('Cannot match the retained Drill assistant to its formation slot')
            team = tuple(replace(card.member, slot=m.slot) if m.slot == slots[0] else m for m in team)
            self.tap(frame, frame.screen.confirm_target, 'Keep the identified Drill assistant')
        formation = self.wait('formation', predicate=lambda s: len(s.team) == 6)
        observed = tuple(replace(m, assistant=False, assistant_id=None) for m in team)
        if formation.screen.team != observed:
            self.fail('Drill formation changed while verifying ownership')
        return formation, team

    def menu(self):
        frame = self.wait({'home', 'campaign', 'lobby', 'menu', 'sweep', 'sweep_empty', 'mock_settlement'})
        if frame.screen.kind in ('sweep', 'sweep_empty'):
            self.tap(frame, (1087, 196), 'Close the Drill sweep panel')
            frame = self.wait('menu')
        if frame.screen.kind == 'home':
            frame = self.navigate('home', 'campaign', (1200, 641))
        if frame.screen.kind == 'campaign':
            self.tap(frame, frame.screen.target, 'Open Joint Firing Drill')
            frame = self.wait({'lobby', 'mock_settlement'})
        if frame.screen.kind == 'mock_settlement':
            if drill_state.read_state(self.config)['pending'] is not None:
                self.fail('A pending paid Drill transaction must be reconciled before dismissing practice')
            self.journal.save_image('expired-mock-settlement.png', frame.capture.png)
            self.tap(frame, frame.screen.target, 'Acknowledge the expired free Drill practice room')
            self.important('drill_mock_expired', 'Closed an expired free Drill practice room; no ticket was spent')
            frame = self.wait({'lobby', 'menu'})
        if frame.screen.kind == 'lobby':
            self.tap(frame, frame.screen.target, 'Inspect the open drill')
            frame = self.wait('menu')
        if frame.screen.period is None or frame.screen.tickets is None:
            self.fail('Drill season or ticket balance is unreadable')
        return frame

    def stage(self, menu, stage):
        self.tap(menu, (1180, (190, 285, 384, 480)[stage-1]), f'Inspect Drill stage {stage}')
        return self.wait('detail', predicate=lambda s: s.stage == stage and s.target is not None)

    def return_home(self, frame):
        if frame.screen.kind != 'menu':
            self.fail('Cannot leave an unrecognized Drill screen')
        self.tap(frame, (1237, 23), 'Return home after Joint Firing Drill')
        self.home()

    def battle(self, formation, stage, *, prefix):
        self.journal.save_image(f'{prefix}-formation.png', formation.capture.png)
        self.tap(formation, formation.screen.target, 'Mobilize the verified Drill formation')
        return self.await_battle(stage, prefix=prefix)

    def await_battle(self, stage, *, prefix):
        deadline = self.clock() + 600
        saved = drill_state.read_state(self.config)['pending']
        auto_verified = bool(saved and saved.get('battle', {}).get('auto_verified'))
        while self.clock() < deadline:
            frame = self.capture()
            screen = frame.screen
            if screen.kind == 'battle':
                if screen.stage != stage:
                    self.fail('Unexpected Drill stage in battle')
                if screen.auto_on is False:
                    self.tap(frame, (1214, 677), 'Enable Auto skills')
                elif screen.auto_on is True:
                    auto_verified = True
                    drill_state.observe_auto(self.config)
            elif screen.kind == 'assistant_confirm':
                self.confirm_assistant(frame)
                self.sleep(3)
            elif screen.kind == 'result':
                self.sleep(2)
                frame = self.wait('result')
                self.journal.save_image(f'{prefix}-result.png', frame.capture.png)
                if not auto_verified:
                    self.fail('No confirmed Auto observation during the Drill battle')
                return frame
            self.sleep(2)
        self.fail('Drill battle did not produce a readable result; inspect the trace')

    def inspect_plan(self, formation):
        """Read all units before practice; reject overlap and unreadable slots."""
        teams = []
        for index in range(3):
            self.tap(formation, (70, (188, 264, 345)[index]), f'Inspect prepared Drill unit {index+1}')
            self.sleep(2)
            formation = self.wait('formation', predicate=lambda s: len(s.team) == 6)
            # Owned provenance must be established separately from name/level.
            formation, team = self.observe_team(formation)
            teams.append(team)
            self.journal.save_image(f'unit-{index+1}.png', formation.capture.png)
        stages = [getattr(self.config, f'drill_stage_{i}') for i in (1, 2, 3)]
        validate_plan(teams, stages)
        self.journal.record('drill_plan', teams=[[asdict(m) for m in team] for team in teams], stages=stages)
        return formation, teams, stages

    def open_unit(self, menu, index, stage, expected=None, *, paid=False):
        detail = self.stage(menu, stage)
        if paid and detail.screen.remaining_rounds != 3 - index:
            self.fail('The observed Drill rounds disagree with saved progress; entry held')
        self.tap(detail, detail.screen.target, f'Open Drill round {index + 1}')
        formation = self.wait('formation')
        self.tap(formation, (70, (188, 264, 345)[index]), f'Select prepared unit {index + 1}')
        self.sleep(2)
        formation = self.wait('formation')
        if expected is not None:
            if any(m.assistant for m in expected):
                formation = self.verify_real_assistant(formation, expected)
            else:
                formation = self.verify_real_owned(formation, expected)
        return formation

    def open_mock(self, menu):
        if menu.screen.active:
            if not menu.screen.mock:
                self.fail('An untracked paid Drill entry is active; reconcile it before practice')
            return menu
        self.tap(menu, (975, 653), 'Create a free Drill practice room')
        confirm = self.wait('mock_confirm')
        self.tap(confirm, confirm.screen.target, 'Confirm free Drill practice')
        return self.wait('menu', predicate=lambda s: s.active and s.mock)

    def qualify_plan(self, menu, context):
        period = menu.screen.period
        stages = [getattr(self.config, f'drill_stage_{i}') for i in (1, 2, 3)]
        plan = drill_state.read_state(self.config)['plan']
        if plan is not None and plan['period'] != period:
            self.fail('The Drill season changed; review the three prepared teams for the new rules')
        if (plan is not None and plan['stages'] == stages and not menu.screen.active
                and drill_state.qualified(self.config, plan['fingerprints'],
                                          self.config.drill_comfort_seconds)):
            teams = [tuple(TeamMember(**m) for m in team) for team in plan['teams']]
            return menu, teams, stages, plan['fingerprints']
        menu = self.open_mock(menu)
        if plan is None:
            formation = self.open_unit(menu, 0, stages[0])
            formation, teams, _ = self.inspect_plan(formation)
            # Back leaves formation without mobilizing or paying a ticket.
            self.tap(formation, (38, 23), 'Return to the free Drill room after team inspection')
            menu = self.wait('menu')
        else:
            teams = [tuple(TeamMember(**m) for m in team) for team in plan['teams']]
        validate_plan(teams, stages)
        keys = []
        for index, (team, stage) in enumerate(zip(teams, stages)):
            formation = self.open_unit(menu, index, stage, team)
            menu, observed, key, ok = self.practice_round(formation, stage, context, verified_team=team)
            if observed != team or not ok:
                self.fail(f'Drill unit {index + 1} did not qualify; improve its formation or lower its stage')
            keys.append(key)
        drill_state.save_plan(self.config, period, teams, stages, ['empty-five-auto'] * 3)
        return menu, teams, stages, keys

    def enter_paid(self, menu, context, keys):
        if menu.screen.active or menu.screen.mock:
            self.fail('Finish the free practice room before starting a real Drill')
        if not drill_state.qualified(self.config, keys, self.config.drill_comfort_seconds):
            self.fail('All three current Drill teams need comfortable mock victories')
        before = menu.screen.tickets
        if before <= self.config.drill_preserve_tickets:
            self.fail('The Drill ticket reserve prevents a real entry')
        self.tap(menu, (1167, 653), 'Inspect the one-ticket Drill entry confirmation')
        frame = self.wait('entry_confirm', predicate=lambda s: s.tickets == before)
        drill_state.begin_spend(self.config, context, before, 1, kind='entry', fingerprints=keys)
        self.journal.save_image('entry-confirm.png', frame.capture.png)
        self.tap(frame, frame.screen.target, 'Confirm one qualified Joint Firing Drill entry')
        menu = self.wait('menu', predicate=lambda s: s.active and not s.mock and s.tickets == before - 1)
        self.important('drill_ticket_used', 'Started a qualified three-round Drill entry',
                       tickets_before=before, tickets_after=menu.screen.tickets)
        return menu

    def confirm_assistant(self, frame):
        pending = drill_state.read_state(self.config)['pending']
        member = self.assistant
        screen = frame.screen
        if (pending is None or pending['kind'] != 'entry' or member is None
                or screen.credit_fee != 40000 or screen.credit_fee > self.config.drill_assistant_max_credits
                or screen.confirm_target is None
                or screen.assistant_level != member.level or screen.assistant_stars != member.stars):
            self.fail('Drill assistant fee or exact qualified offering is unverified')
        if pending.get('assistant_fee_sent'):
            self.fail('The Drill assistant confirmation is unresolved; no duplicate payment will be sent')
        state = drill_state.read_state(self.config)
        state['pending']['assistant_fee_sent'] = True
        drill_state.write_state(self.config, state)
        self.journal.save_image('assistant-fee.png', frame.capture.png)
        self.tap(frame, screen.confirm_target, 'Confirm the qualified assistant for 40,000 credits')
        self.important('drill_assistant_fee', 'Confirmed the Drill assistant fee: 40,000 credits',
                       credit_cost=40000, student=member.student_id)

    def paid_rounds(self, menu, teams, stages):
        self.assistant = next((m for team in teams for m in team if m.assistant), None)
        pending = drill_state.read_state(self.config)['pending']
        if pending is None or pending['kind'] != 'entry':
            self.fail('Missing paid Drill entry intent')
        for index in range(pending['round'], 3):
            if not menu.screen.active or menu.screen.mock:
                self.fail('The paid Drill room is no longer active')
            formation = self.open_unit(menu, index, stages[index], teams[index], paid=True)
            formation, skills = self.starting_skills(formation)
            key = fingerprint(teams[index], stages[index], skills)
            if key != pending['fingerprints'][index]:
                self.fail('The real Drill setup differs from its qualified mock')
            drill_state.begin_round(self.config, index, stages[index], key)
            result = self.battle(formation, stages[index], prefix=f'real-round-{index + 1}')
            if not result.screen.won or not result.screen.score:
                self.fail('The qualified Drill team lost; the active entry needs review')
            evidence = self.run_dir / f'real-round-{index + 1}-result.png'
            drill_state.record_round(self.config, completed_rounds=index + 1,
                                     score=result.screen.score, evidence=str(evidence))
            self.important('drill_round_completed',
                           f'Drill round {index + 1}: stage {stages[index]}, {result.screen.score:,} points',
                           stage=stages[index], score=result.screen.score, evidence=str(evidence))
            menu = self.dismiss_result(result)
            drill_state.acknowledge_round(self.config)
        return menu

    def collect_receipt(self, frame):
        """Inspect the received items before dismissing a paid reward screen."""
        if frame.screen.kind != 'receipt':
            self.fail('The Drill reward receipt has not been observed')
        frame = self.wait('receipt', timeout=90, predicate=lambda s: s.target is not None)
        evidence = self.run_dir / 'drill-rewards.png'
        self.journal.save_image(evidence.name, frame.capture.png)
        frame = inspect_receipt(self, frame, evidence)
        sidecar = evidence.with_suffix('.loot.json')
        items = (json.loads(sidecar.read_text())['items'] if sidecar.is_file()
                 else list(frame.screen.items))
        self.important('drill_rewards_received', 'Joint Firing Drill rewards received',
                       items=items, evidence=str(evidence))
        drill_state.record_receipt(self.config, evidence)
        frame = self.wait('receipt', predicate=lambda s: s.target is not None)
        self.tap(frame, frame.screen.target, 'Close the inspected Drill rewards')
        return self.menu()

    def settle_entry(self, frame):
        pending = drill_state.read_state(self.config)['pending']
        if pending is None or pending['kind'] != 'entry' or pending['round'] != 3:
            self.fail('All three Drill results are required before settlement')
        if frame.screen.kind == 'settlement':
            expected = sum(r['score'] for r in pending['results'])
            if frame.screen.score != expected:
                self.fail('Drill settlement score disagrees with the three saved results')
            self.journal.save_image('settlement.png', frame.capture.png)
            self.important('drill_completed', f'Joint Firing Drill complete: {expected:,} points',
                           score=expected)
            self.tap(frame, frame.screen.target, 'Collect the completed three-round Drill')
            frame = self.wait('receipt', timeout=90)
        menu = self.collect_receipt(frame)
        drill_state.finish_spend(self.config, menu.screen.tickets, receipt_verified=True)
        return menu

    def resume_entry(self, state):
        """Reconcile only saved entry progress; never replay a ticket dialog."""
        pending, plan = state['pending'], state['plan']
        if plan is None or pending['fingerprints'] != plan['fingerprints']:
            self.fail('The active Drill entry has no matching saved team plan')
        teams = [tuple(TeamMember(**m) for m in team) for team in plan['teams']]
        self.assistant = next((m for team in teams for m in team if m.assistant), None)
        frame = self.wait({'menu', 'battle', 'result', 'receipt', 'settlement',
                           'assistant_confirm'}, timeout=90)
        battle = pending.get('battle')
        if frame.screen.kind == 'assistant_confirm':
            if battle is None:
                self.fail('Assistant confirmation has no saved round intent')
            self.confirm_assistant(frame)
            frame = self.await_battle(battle['stage'], prefix=f"real-round-{battle['index']+1}")
        elif frame.screen.kind == 'battle':
            if battle is None or frame.screen.stage != battle['stage']:
                self.fail('Live Drill battle does not match the saved round intent')
            frame = self.await_battle(battle['stage'], prefix=f"real-round-{battle['index']+1}")
        if frame.screen.kind == 'result':
            if pending.get('result_unacknowledged'):
                if not frame.screen.won or frame.screen.score != pending['results'][-1]['score']:
                    self.fail('The open Drill result disagrees with saved progress')
            else:
                if (battle is None or not frame.screen.won or not frame.screen.score
                        or not drill_state.read_state(self.config)['pending']['battle']['auto_verified']):
                    self.fail('The open Drill result cannot be verified against its saved battle')
                evidence = self.run_dir / f"real-round-{battle['index']+1}-result.png"
                self.journal.save_image(evidence.name, frame.capture.png)
                drill_state.record_round(self.config, completed_rounds=battle['index']+1,
                                         score=frame.screen.score, evidence=str(evidence))
            frame = self.dismiss_result(frame)
            drill_state.acknowledge_round(self.config)
        pending = drill_state.read_state(self.config)['pending']
        if frame.screen.kind == 'menu' and pending.get('receipt'):
            drill_state.finish_spend(self.config, frame.screen.tickets, receipt_verified=True)
            return frame
        if frame.screen.kind == 'menu':
            if (not frame.screen.active or frame.screen.mock
                    or frame.screen.period != plan['period']
                    or frame.screen.tickets != pending['before'] - 1):
                self.fail('The current Drill room disagrees with the saved entry')
            if pending.get('battle'):
                self.fail('A mobilized Drill round has no result; review it before retrying')
            if pending.get('result_unacknowledged'):
                drill_state.acknowledge_round(self.config)
            frame = self.paid_rounds(frame, teams, plan['stages'])
        return self.settle_entry(frame)

    def resume_sweep(self, state):
        pending = state['pending']
        frame = self.wait({'receipt', 'menu', 'sweep', 'sweep_empty', 'sweep_confirm'}, timeout=90)
        if frame.screen.kind in ('sweep', 'sweep_empty') and pending.get('receipt'):
            frame = self.menu()
        if frame.screen.kind == 'sweep_confirm':
            if pending.get('confirmation_sent') or frame.screen.count != pending['count']:
                self.fail('Drill sweep confirmation is uncertain or changed; it will not be repeated')
            state['pending']['confirmation_sent'] = True
            drill_state.write_state(self.config, state)
            self.tap(frame, frame.screen.target, 'Confirm the verified Drill ticket sweep')
            frame = self.wait('receipt', timeout=90)
        if frame.screen.kind == 'receipt':
            frame = self.collect_receipt(frame)
        elif frame.screen.kind != 'menu' or not pending.get('receipt'):
            self.fail('Drill sweep confirmation is unresolved; review the receipt before retrying')
        drill_state.finish_spend(self.config, frame.screen.tickets, receipt_verified=True)
        self.important('drill_sweep_completed', f"Swept Joint Firing Drill {pending['count']} time(s)",
                       count=pending['count'], tickets_remaining=frame.screen.tickets)
        return frame

    def sweep_remaining(self, menu, context):
        if menu.screen.active or not menu.screen.score:
            self.fail('A finished scored Drill is required before sweeping')
        count = menu.screen.tickets - self.config.drill_preserve_tickets
        if count <= 0:
            return menu
        self.tap(menu, (821, 650), 'Inspect available Drill sweeps')
        frame = self.wait('sweep')
        # Adjust using observed counts; never assume Max honors the reserve.
        for _ in range(menu.screen.tickets + 1):
            if frame.screen.count == count:
                break
            self.tap(frame, (977 if frame.screen.count < count else 818, 360),
                     'Set Drill sweep count while preserving reserved tickets')
            self.sleep(.7)
            frame = self.wait('sweep')
        if (frame.screen.count != count or frame.screen.tickets != menu.screen.tickets
                or frame.screen.score != menu.screen.score
                or frame.screen.tickets_after != self.config.drill_preserve_tickets):
            self.fail('Drill sweep count, score, or ticket balance disagrees with the plan')
        self.journal.save_image('sweep-confirmation.png', frame.capture.png)
        drill_state.begin_spend(self.config, context, menu.screen.tickets, count, kind='sweep')
        self.tap(frame, frame.screen.target, f'Sweep Joint Firing Drill {count} times')
        return self.resume_sweep(drill_state.read_state(self.config))

    def run(self):
        state = drill_state.read_state(self.config)
        if state['pending'] is not None:
            if state['pending']['kind'] == 'entry':
                menu = self.resume_entry(state)
            else:
                menu = self.resume_sweep(state)
        else:
            menu = self.menu()
        context = f'{game_day(self.wall_clock())}|{menu.screen.period}'
        drill_state.for_context(self.config, context)
        self.important('drill_inspected',
                       f'Joint Firing Drill: {menu.screen.tickets} tickets; today’s score {menu.screen.score}',
                       tickets=menu.screen.tickets, score=menu.screen.score, period=menu.screen.period)
        if self.config.drill_policy != 'inspect' and menu.screen.tickets > self.config.drill_preserve_tickets:
            if menu.screen.score == 0 and self.config.drill_policy == 'clear_and_sweep':
                menu, teams, stages, keys = self.qualify_plan(menu, context)
                menu = self.enter_paid(menu, context, keys)
                menu = self.paid_rounds(menu, teams, stages)
                menu = self.settle_entry(menu)
            if menu.screen.score is not None and menu.screen.score > 0:
                menu = self.sweep_remaining(menu, context)
            else:
                self.important('drill_setup_needed', 'A complete Drill clear today is required before sweeping')
        self.return_home(menu)
        return self.finish()


def run_joint_firing_drill(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = DrillRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            if not is_supported_size(device.display_size()):
                runner.fail('Joint Firing Drill requires a supported 16:9 landscape display')
            return runner.run()
        except BaseException as exc:
            runner.journal.record('finished', status='failed', detail=str(exc))
            raise
        finally:
            runner.journal.close()
