"""Deterministic, opt-in event clears and priority sweeps.

Quest first-clear requests are separate jobs. Enabled event farming completes
the reviewed Stories first, and never falls back after uncertain event progress.
"""
import re
import numpy as np
from . import event_state
from .event_priority import available_event
from .event_inspection import enter_event
from .event_vision import QuestVision
from .vision import decode_frame, read_game_words
from .shop_vision import text_in
from .crafting_vision import within
from .spend_ap import APRunner, SweepDeferred
from .ap_policy import sweep_count
from .locking import InstanceLock
from .loot_receipts import inspect_receipt
from .runtime import TaskError

LISTS = {'event_page', 'event_list'}
TABS = {'quest': (937, 110), 'challenge': (1120, 110)}
TAB_MARKS = {'quest': (990, 95, 1015, 125), 'challenge': (1182, 95, 1204, 125)}


def tab_selected(png, tab):
    """The open tab is dark with yellow text; the other tabs are pale."""
    x1, y1, x2, y2 = TAB_MARKS[tab]
    return float(np.median(decode_frame(png)[y1:y2, x1:x2])) < 120


def quest_target(words, stage):
    """Pair the numbered quest row with its Enter button; never guess a row."""
    rows = [w for w in within(words, (700,150,772,690))
            if w.confidence >= .9 and re.fullmatch(r'0?[1-9]|1[0-2]', w.text.strip())
            and int(w.text.strip()) == stage]
    if len(rows) != 1:
        return None
    entries = [w for w in within(words, (1060,150,1200,690))
               if w.confidence >= .9 and w.normalized == 'enter'
               and abs(w.center[1] - rows[0].center[1]) < 35]
    return entries[0].center if len(entries) == 1 else None


def quest_detail(runner, stage, *, allow_unavailable=False, tab='quest'):
    name = tab.title()
    frame = runner.wait(LISTS)
    runner.tap(frame, TABS[tab], f'Open event {name} tab')
    last_rows, unchanged, reopened = None, 0, 0
    for attempt in range(14):
        frame = runner.wait(LISTS)
        # Every tab numbers its rows from 01. Offline doubles carry no pixels.
        if getattr(runner, 'startup', None) is not None and not tab_selected(frame.capture.png, tab):
            reopened += 1
            if reopened > 3:
                runner.fail(f'Event {name} tab did not open; no AP spent')
            runner.tap(frame, TABS[tab], f'Open event {name} tab')
            continue
        words = read_game_words(frame.capture.png, runner.startup)
        target = quest_target(words, stage)
        if target is not None:
            runner.tap(frame, target, f'Inspect event {name} {stage}')
            return runner.wait('detail', predicate=lambda s: s.strategy == 'event' and s.stage == str(stage))
        visible = [int(w.text.strip()) for w in within(words,(700,150,772,690))
                   if w.confidence >= .9 and re.fullmatch(r'0?[1-9]|1[0-2]',w.text.strip())]
        if not visible:
            runner.fail('Event quest numbers are unreadable; no AP spent')
        # Locked rows may have a number but no Enter button. A farm visit
        # can inspect an earlier stage; a first-clear job must not guess.
        if allow_unavailable and stage in visible:
            return None
        rows = tuple(visible)
        unchanged = unchanged + 1 if rows == last_rows else 0
        last_rows = rows
        if allow_unavailable and unchanged >= 2:
            return None
        points = ((945,295),(945,535)) if stage < min(visible) else ((945,535),(945,295))
        if (not frame.capture.is_fresh(runner.clock())
                or runner.device.foreground_package() != runner.config.package):
            runner.fail('Event quest list changed before scrolling')
        runner.journal.record('intent',operation='swipe',points=points,detail=f'Find event Quest {stage}')
        if not runner.device.swipe(*points,duration_ms=650,deadline=frame.capture.deadline,monotonic=runner.clock):
            runner.fail('Event quest scroll expired')
        runner.actions += 1
        runner.sleep(1)
    runner.fail(f'Event Quest {stage} was not found; stopped without another battle')


def close_detail(runner, frame):
    runner.tap(frame,(1127,139),'Close event mission info')
    return runner.wait(LISTS)


def event_home(runner, frame):
    if frame.screen.kind == 'detail':
        frame = close_detail(runner,frame)
    if frame.screen.kind not in LISTS | {'event_board'}:
        runner.fail('Cannot leave an unrecognized event screen')
    runner.tap(frame,(1237,24),'Return home after event work')
    runner.home()


def itemize_or_keep(runner, frame, evidence, label):
    """Record a battle's drops; an unreadable list must not strand the result.

    A first clear can award a long scrolling row of cards whose pages the loot
    reader cannot prove overlap. That is a gap in the record only: the battle is
    over, and its stars and AP are verified from the stage afterwards. Leaving
    the paid entry pending instead would hold all event spending for review.
    """
    try:
        return inspect_receipt(runner, frame, evidence)
    except TaskError as error:
        runner.journal.record('loot_receipt_incomplete', label=label, detail=str(error)[:300])
        runner.important('event_loot_incomplete',
                         f'{label}: rewards could not be fully itemized; the receipt is saved for review',
                         evidence=str(evidence))
        return runner.wait('receipt', timeout=90)


def battle_cost(frame, startup):
    words = read_game_words(frame.capture.png,startup)
    value = text_in(words,(970,460,1130,512)).replace(' ','')
    match = re.fullmatch(r'(\d+)[→➜](\d+)', value)
    if not match or int(match[1]) != frame.screen.ap:
        return None
    cost = int(match[1])-int(match[2])
    return cost if 0 < cost <= 30 else None


class EventClearRunner(APRunner):
    task = 'clear_event'

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.vision = QuestVision(self.startup,self.vision)

    def budget(self):
        if self.clock()-self.started > 14400 or self.actions >= 3500:
            self.fail('Event clear reached its time or input limit')

    def stage_detail(self, stage, tab):
        return quest_detail(self,stage) if tab == 'quest' else quest_detail(self,stage,tab=tab)

    def finish_battle(self, stage, tab='quest'):
        name = tab.title()
        deadline = self.clock()+360
        while self.clock() < deadline:
            frame = self.capture()
            if (frame.screen.kind in {'event_complete','event_defeat'}
                    and frame.capture.deadline - self.clock() < 1):
                # Live, writing the native result image first used up a frame's
                # input window and failed a won battle. Read the page again.
                continue
            if frame.screen.kind == 'event_complete':
                self.tap(frame,frame.screen.target,'Confirm completed event battle')
                self.journal.save_image(f'{tab}-{stage}-result.png',frame.capture.png)
                break
            if frame.screen.kind == 'event_defeat':
                # A loss is a known result, not an unresolved one: it awards
                # nothing, so the stage's own stars are read back as usual.
                self.tap(frame,frame.screen.target,'Confirm lost event battle')
                self.journal.save_image(f'{tab}-{stage}-defeat.png',frame.capture.png)
                self.important('event_battle_lost',f'Event {name} {stage}: lost with default Auto Formation',stage=str(stage))
                return self.stage_detail(stage,tab)
            if frame.screen.kind == 'event_battle' and frame.screen.auto is False:
                self.tap(frame,(1213,677),'Enable Auto for event battle')
            self.sleep(2)
        else:
            self.fail('Event battle did not reach a verified victory; stopped without replaying it')
        frame = self.wait({'event_bonus','receipt'},timeout=90)
        if frame.screen.kind == 'event_bonus':
            self.tap(frame,frame.screen.target,'Confirm event currency bonuses')
            frame = self.wait('receipt',timeout=90)
        path = self.run_dir / f'{tab}-{stage}-receipt.png'
        self.journal.save_image(path.name,frame.capture.png)
        frame = itemize_or_keep(self, frame, path, f'Event {name} {stage}')
        self.tap(frame,frame.screen.target,'Return to event after recording drops')
        frame = self.wait(LISTS | {'receipt'})
        if frame.screen.kind == 'receipt':
            # The first tap can be spent closing an item tooltip left open.
            self.tap(frame,frame.screen.target,'Return to event after recording drops')
            self.wait(LISTS)
        return self.stage_detail(stage,tab)

    def fight(self, state, frame, stage, cost, tab='quest'):
        """Default Auto Formation, a durable intent, then exactly one battle."""
        name = tab.title()
        before = frame.screen.ap
        self.tap(frame,(936,534),f'Set up event {name} {stage}')
        frame = self.wait('event_formation')
        self.tap(frame,(1200,205),f'Open Quick Formation for this {tab}')
        frame = self.wait('event_quick')
        self.tap(frame,(623,593),'Use default Auto Formation')
        frame = self.wait('event_quick')
        self.tap(frame,(1168,593),'Confirm Auto Formation')
        frame = self.wait('event_formation')
        if frame.screen.ap is None or frame.screen.ap < before or frame.screen.ap-cost < self.config.ap_floor:
            self.fail('AP changed during event formation; no battle started')
        self.journal.save_image(f'{tab}-{stage}-auto-formation.png',frame.capture.png)
        state['pending'] = dict(stage=str(stage),ap_before=frame.screen.ap,cost=cost,run_dir=str(self.run_dir))
        if tab != 'quest':
            state['pending']['mode'] = tab
        event_state.write_state(self.config,state)
        self.important('event_battle_requested',f'Event {name} {stage}: default Auto Formation; {cost} AP',stage=str(stage),ap_cost=cost)
        self.tap(frame,(1180,660),f'Mobilize event {name} {stage}')
        frame = self.finish_battle(stage) if tab == 'quest' else self.finish_battle(stage,tab)
        if frame.screen.ap is None or frame.screen.ap < before-cost:
            self.fail('Event result AP did not verify; inspect the receipt')
        self.journal.save_image(f'{tab}-{stage}-stars.png', frame.capture.png)
        return frame

    def clear_challenges(self, profile, state):
        """One Auto Formation attempt per unfinished Challenge; stop at the first shortfall.

        Challenges award their clear rewards once and cannot be swept. A stage
        that default Auto Formation cannot three-star is left for the player
        rather than retried, and later, harder stages are not attempted.
        """
        results = state.setdefault('challenges', {})
        frame, summary = self.wait(LISTS), None
        for stage in range(1,profile.get('challenge_count',0)+1):
            frame = quest_detail(self,stage,allow_unavailable=True,tab='challenge')
            if frame is None:
                frame, summary = self.wait(LISTS), f'Event Challenge {stage} is not open yet'
                break
            if frame.screen.stars != 3:
                if str(stage) in results:
                    summary = f'Event Challenge {stage} stays at {results[str(stage)]}/3 stars; play it by hand'
                    break
                cost = battle_cost(frame,self.startup)
                if cost is None:
                    self.fail('Could not verify event Challenge AP cost; no battle started')
                if frame.screen.ap-cost < self.config.ap_floor:
                    summary = f'Event clear stopped before Challenge {stage}: keeping {self.config.ap_floor} AP'
                    break
                frame = self.fight(state,frame,stage,cost,'challenge')
                state['pending'] = None
            stars = frame.screen.stars
            results[str(stage)] = stars
            event_state.write_state(self.config,state)
            if stars != 3:
                self.important('event_challenge_attempted',f'Event Challenge {stage}: {stars}/3 stars',stage=str(stage),stars=stars)
                summary = f'Stopped at event Challenge {stage}: {stars}/3 stars'
                break
            frame = close_detail(self,frame)
        return frame, summary

    def run(self):
        event_state.ensure_safe(self.config)
        from .ap_state import read_state
        if read_state(self.config)['pending']:
            self.fail('An AP sweep is unresolved; clear its receipt before event battles')
        profile = available_event(self.wall_clock())
        if profile is None:
            self.fail('No supported event is currently playable')
        enter_event(self,self.startup)
        state = event_state.observe(self.config,profile)
        summary = 'All event quests have three-star clears'
        for stage in range(1,profile['quest_count']+1):
            frame = quest_detail(self,stage)
            if frame.screen.stars == 3:
                state['clears'][str(stage)] = 3
                event_state.write_state(self.config,state)
                close_detail(self,frame)
                continue
            cost = battle_cost(frame,self.startup)
            if cost is None:
                self.fail('Could not verify event battle AP cost; no battle started')
            if frame.screen.ap-cost < self.config.ap_floor:
                summary = f'Event clear stopped before Quest {stage}: keeping {self.config.ap_floor} AP'
                break
            frame = self.fight(state,frame,stage,cost)
            stars = frame.screen.stars
            state['clears'][str(stage)] = stars
            state['pending'] = None
            event_state.write_state(self.config,state)
            self.important('event_quest_cleared',f'Event Quest {stage}: {stars}/3 stars; rewards recorded',stage=str(stage),stars=stars)
            if stars != 3:
                summary = f'Stopped at event Quest {stage}: {stars}/3 stars'
                break
            close_detail(self,frame)
        else:
            frame = self.wait(LISTS)
            if profile.get('challenge_count'):
                frame, stopped = self.clear_challenges(profile,state)
                summary = stopped or 'All event quests and challenges have three-star clears'
        state['summary'] = summary
        state['declined'] = True  # one prompt per detected event; manual run can continue
        event_state.write_state(self.config,state)
        self.important('event_clear_finished',summary)
        event_home(self,frame if frame.screen.kind == 'detail' else self.wait(LISTS))
        return self.finish()


def run_clear_event(config,device,startup,**kwargs):
    with InstanceLock(config):
        runner = EventClearRunner(config,device,startup,**kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record('finished',status='failed',detail=str(exc))
            raise
        finally:
            runner.journal.close()


def farm_event(runner, profile):
    """Return None only on verified goal completion; otherwise reserve this AP."""
    event_state.ensure_safe(runner.config)
    previous = runner.vision
    runner.vision = QuestVision(runner.startup,previous)
    try:
        frame = enter_event(runner,runner.startup)
        from .event_stories import complete_stories
        story_summary = complete_stories(runner, profile, frame)
        frame = runner.wait(LISTS)
        if story_summary is not None:
            event_home(runner, frame)
            return story_summary
        if profile.get('farm_goal', 'treasure_round') == 'treasure_round':
            runner.tap(frame,(515,663),'Check event farming goal')
            board = runner.wait('event_board')
            if board.screen.round > profile['target_round']:
                event_home(runner,board)
                return None
            runner.tap(board,(55,25),'Return to event quests')
            runner.wait(LISTS)
        # Otherwise the season has no in-game goal to observe: keep farming its
        # quests until the play period ends or event priority is turned off.
        for stage in profile['farm_order']:
            frame = quest_detail(runner,stage,allow_unavailable=True)
            if frame is None:
                continue
            if frame.screen.stars == 3:
                if frame.screen.ap is None or not frame.screen.cost:
                    # Unreadable quantity/cost is not evidence that AP is low.
                    # Keep the event reservation and retry this same detail.
                    frame = runner.wait('detail', predicate=lambda s: (
                        s.strategy == 'event' and str(s.stage) == str(stage)
                        and s.stars == 3 and s.ap is not None and bool(s.cost)))
                count = sweep_count(frame.screen.ap,runner.config.ap_floor,frame.screen.cost)
                if count:
                    try:
                        frame = runner.sweep(frame,count)
                    except SweepDeferred as deferred:
                        frame = deferred.frame
                        summary = 'Event sweep deferred to the next AP visit; normal farming remains on hold'
                    else:
                        summary = f'Farmed event Quest {stage} ×{count}; keeping at least {runner.config.ap_floor} AP'
                else:
                    summary = 'Event farming: remaining AP cannot cover a sweep above the floor'
                event_home(runner,frame)
                return summary
            close_detail(runner,frame)
        event_home(runner,runner.wait(LISTS))
        return 'Event needs three-star quest clears; use the clear-event prompt. Normal AP farming remains on hold.'
    finally:
        runner.vision = previous
