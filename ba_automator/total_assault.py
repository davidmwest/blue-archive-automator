"""Serial Total Assault visits, qualified by a free mock before ticket use."""

from dataclasses import asdict
import json

from . import assault_state
from .actions import record_action
from .assault_assistant import AssaultAssistantMixin
from .assault_battle import AssaultBattleMixin, observed_team_fingerprint
from .assault_policy import AssaultContext, DIFFICULTIES, next_difficulty, team_fingerprint
from .assault_vision import AssaultVision
from .club import game_day
from .locking import InstanceLock
from .loot_receipts import inspect_receipt
from .shop_runtime import ShopRunner


class TotalAssaultRunner(AssaultAssistantMixin, AssaultBattleMixin, ShopRunner):
    task = "total_assault"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", AssaultVision(startup))
        super().__init__(config, device, startup, **kwargs)
        self.context = None
        self.assistant = None

    def budget(self):
        if self.clock() - self.started > 7200 or self.actions >= 600:
            self.fail("Total Assault reached its time or input limit; inspect the saved trace")

    def important(self, kind, detail, **fields):
        record_action(self.config, kind, detail, task=self.task, **fields)
        self.phase(detail)

    def swipe(self, frame, start, end, detail):
        if (not frame.capture.is_fresh(self.clock())
                or self.device.foreground_package() != self.config.package):
            self.fail("Total Assault scroll observation expired")
        self.budget()
        self.journal.record("intent", operation="swipe", detail=detail, start=start, end=end)
        if not self.device.swipe(start, end, duration_ms=650,
                                 deadline=frame.capture.deadline, monotonic=self.clock):
            self.fail("Total Assault scroll expired before input")
        self.actions += 1
        self.sleep(1)

    def check_context(self, frame):
        screen = frame.screen
        if (screen.boss != self.context.boss or screen.difficulty != self.context.difficulty
                or screen.tickets is None or game_day(self.wall_clock()) != self.context.day_key
                or (screen.event_period is not None and screen.event_period != self.context.event_id)):
            self.fail("Total Assault boss, difficulty, tickets, or game day changed; repeat the mock")
        return frame

    def stage_menu(self):
        self.navigate("home", "campaign", (1200, 641))
        return self.navigate("campaign", "menu", (906, 456))

    def survey(self, frame):
        """Scan with overlap; lack of an Enter control is never an inferred lock."""
        observed = {}
        identity = (frame.screen.boss, frame.screen.event_period, frame.screen.tickets)
        if not all(value is not None for value in identity):
            self.fail("Cannot verify the current Total Assault event and ticket balance")
        # First reach the beginning of the bounded eight-difficulty list.
        for _ in range(8):
            if any(stage.difficulty == "normal" for stage in frame.screen.stages):
                break
            self.swipe(frame, (1050, 240), (1050, 535), "Scroll to easier Total Assault difficulties")
            frame = self.wait("menu")
        else:
            self.fail("Could not find the beginning of Total Assault difficulties")
        previous = None
        for _ in range(12):
            if (frame.screen.boss, frame.screen.event_period, frame.screen.tickets) != identity:
                self.fail("Total Assault event changed while surveying difficulties")
            for stage in frame.screen.stages:
                old = observed.get(stage.difficulty)
                if old is not None and old.locked != stage.locked:
                    self.fail("Total Assault difficulty lock changed during the survey")
                observed[stage.difficulty] = stage
            signature = tuple((stage.difficulty, stage.locked) for stage in frame.screen.stages)
            if "lunatic" in observed or signature == previous:
                break
            previous = signature
            self.swipe(frame, (1050, 530), (1050, 270), "Inspect harder Total Assault difficulties")
            frame = self.wait("menu")
        self.journal.record("difficulty_survey", event=identity[1], boss=identity[0],
                            tickets=identity[2], stages=[asdict(s) for s in observed.values()])
        return observed, frame

    def select_stage(self, frame, difficulty):
        for _ in range(14):
            selected = next((stage for stage in frame.screen.stages
                             if stage.difficulty == difficulty), None)
            if selected is not None:
                if selected.locked is not False or selected.target is None:
                    self.fail(f"{difficulty.replace('_', ' ').title()} is not verified unlocked")
                context = AssaultContext(frame.screen.event_period, selected.boss, difficulty,
                                         game_day(self.wall_clock()))
                self.tap(frame, selected.target, f"Inspect {difficulty} Room Info")
                self.context = context
                return self.check_context(self.wait("detail"))
            positions = [DIFFICULTIES.index(s.difficulty) for s in frame.screen.stages]
            if not positions:
                self.fail("Total Assault difficulty list is unreadable")
            easier = DIFFICULTIES.index(difficulty) < min(positions)
            points = ((1050, 240), (1050, 530)) if easier else ((1050, 530), (1050, 270))
            self.swipe(frame, *points, "Find the selected Total Assault difficulty")
            frame = self.wait("menu")
        self.fail("Selected Total Assault difficulty was not found in the list")

    def auto_team(self, detail):
        self.check_context(detail)
        if detail.screen.mock_target is None:
            self.fail("The seasonal Mock Battle button is unavailable")
        self.tap(detail, detail.screen.mock_target, "Open free seasonal Mock Battle formation")
        formation = self.wait("formation")
        self.tap(formation, formation.screen.quick_target, "Open Quick Formation")
        quick = self.wait("quick")
        self.tap(quick, quick.screen.auto_target, "Auto-create the mock battle team")
        quick = self.verify_owned_quick(self.wait("quick"))
        self.tap(quick, quick.screen.confirm_target, "Confirm Auto formation")
        formation = self.wait("formation", predicate=lambda s: len(s.team) == 6)
        team = formation.screen.team
        self.journal.save_image("auto-formation.png", formation.capture.png)
        self.important("total_assault_team", "Auto-created Total Assault mock team",
                       context=asdict(self.context), team=[asdict(member) for member in team],
                       fingerprint=team_fingerprint(team))
        return formation, team

    def dismiss_result(self, frame):
        if not frame.capture.is_fresh(self.clock()):
            frame = self.wait("result")
        self.tap(frame, frame.screen.confirm_target, "Close Total Assault battle result")
        return self.collect_receipts()

    def collect_receipts(self, frame=None):
        """Record rewards once and wait for each acknowledged screen to leave."""
        kinds = {"menu", "detail", "receipt", "outcome", "season_record"}
        seen_notices = set()
        for index in range(8):
            frame = frame or self.wait(kinds, timeout=90)
            kind = frame.screen.kind
            if kind in {"menu", "detail"}:
                return frame
            if kind in {"outcome", "season_record"}:
                if kind in seen_notices or frame.screen.confirm_target is None:
                    self.fail("Total Assault repeated an outcome notice; inspect before continuing")
                seen_notices.add(kind)
                self.journal.save_image(f"{kind}-{self.actions}.png", frame.capture.png)
                self.journal.record("total_assault_outcome_notice", kind=kind,
                                    context=asdict(self.context))
                self.tap(frame, frame.screen.confirm_target, f"Close Total Assault {kind.replace('_', ' ')}")
                frame = self.wait(kinds - {kind}, timeout=90)
                continue
            if kind != "receipt":
                self.fail("Unexpected Total Assault reward screen")
            name = f"reward-{self.actions}-{index}.png"
            evidence = self.run_dir / name
            self.journal.save_image(name, frame.capture.png)
            frame = inspect_receipt(self, frame, evidence)
            items = list(frame.screen.items)
            # Tooltip inspection is authoritative for sweep layouts whose
            # screen classifier deliberately reads no individual item names.
            sidecar = evidence.with_suffix(".loot.json")
            if sidecar.is_file():
                items = json.loads(sidecar.read_text())["items"]
            label = ", ".join(f'+{item["quantity"]:,} {item["name"]}' for item in items
                              if item.get("name") and type(item.get("quantity")) is int)
            self.important("total_assault_rewards_received",
                           f"Total Assault rewards: {label or 'receipt saved for inspection'}",
                           items=items, evidence=str(evidence), context=asdict(self.context))
            self.tap(frame, frame.screen.target, "Close the inspected Total Assault receipt")
            # A slow/ignored dismissal must not create another loot record for
            # the same rewards. No second input is sent while it remains open.
            frame = self.wait(kinds - {"receipt"}, timeout=90)
        self.fail("Total Assault produced too many reward screens; inspect the trace")

    def room_after_result(self, frame):
        self.check_return_context(frame)
        if frame.screen.kind == "detail":
            return self.check_context(frame)
        return self.select_stage(frame, self.context.difficulty)

    def check_return_context(self, frame):
        """Reconcile spending only against the same event and game day."""
        if frame.screen.kind == "detail":
            return self.check_context(frame)
        if (frame.screen.kind != "menu" or frame.screen.boss != self.context.boss
                or frame.screen.event_period != self.context.event_id
                or game_day(self.wall_clock()) != self.context.day_key
                or type(frame.screen.tickets) is not int):
            self.fail("Total Assault event or game day changed; ticket reconciliation needs inspection")
        return frame

    def qualify(self, detail):
        formation, team = self.auto_team(detail)
        result, frame = self.fight(formation, team, mock=True)
        state = assault_state.record_mock(
            self.config, self.context, team, result, self.run_dir.name,
            min_remaining_seconds=self.config.total_assault_comfort_seconds, now=self.wall_clock())
        detail = self.room_after_result(self.dismiss_result(frame))
        if state["proof"] is not None:
            return detail, team
        self.important("total_assault_assistant_needed",
                       "Auto formation did not meet the mock requirement; checking an assistant striker",
                       result=asdict(result))
        formation, team = self.choose_assistant_team(detail, team, result)
        result, frame = self.fight(formation, team, mock=True)
        state = assault_state.record_mock(
            self.config, self.context, team, result, self.run_dir.name,
            min_remaining_seconds=self.config.total_assault_comfort_seconds, now=self.wall_clock())
        detail = self.room_after_result(self.dismiss_result(frame))
        if state["proof"] is None:
            self.fail("The assistant mock did not win comfortably. Choose a lower difficulty or play manually")
        return detail, team

    def real_clear(self, detail, team):
        self.check_context(detail)
        if detail.screen.enter_target is None or detail.screen.tickets < 1:
            self.fail("No verified real Total Assault entry is available")
        before = detail.screen.tickets
        intent = assault_state.begin_entry(self.config, self.context, team,
                                          self.run_dir.name, before, now=self.wall_clock())
        self.important("total_assault_entry_requested", "Entering Total Assault with the mock-qualified team",
                       context=asdict(self.context), tickets_before=before,
                       team_fingerprint=team_fingerprint(team), intent_id=intent)
        self.tap(detail, detail.screen.enter_target, "Use one Total Assault ticket")
        formation = self.wait("formation", timeout=90)
        if any(member.assistant for member in team):
            formation = self.verify_real_assistant(formation, team)
        else:
            formation = self.verify_real_owned(formation, team)
        if observed_team_fingerprint(formation.screen.team) != observed_team_fingerprint(team):
            self.fail("Real formation differs from the mock. The ticket intent is held for inspection")
        result, frame = self.fight(formation, team, mock=False)
        frame = self.dismiss_result(frame)
        self.check_return_context(frame)
        state = assault_state.complete_entry(self.config, intent, tickets_after=frame.screen.tickets,
                                             won=result.won, now=self.wall_clock())
        self.important("total_assault_clear" if result.won else "total_assault_failed",
                       state["last_summary"], context=asdict(self.context),
                       tickets_before=before, tickets_after=frame.screen.tickets, result=asdict(result))
        if not result.won:
            self.fail(state["last_summary"])
        return frame

    def confirm_sweep(self, frame, intent):
        """A sweep notice must agree with this run's already persisted budget."""
        state = assault_state.read_state(self.config)
        pending, screen = state["pending"], frame.screen
        if (state["blocked_reason"] or pending is None or pending["id"] != intent
                or pending["kind"] != "sweep" or pending["run_id"] != self.run_dir.name
                or pending["context"] != asdict(self.context)
                or game_day(self.wall_clock()) != self.context.day_key
                or screen.kind != "sweep_confirm" or screen.confirm_target is None
                or screen.count != pending["count"]
                or screen.tickets != pending["tickets_before"]
                or screen.after_tickets != screen.tickets - screen.count
                or screen.difficulty != self.context.difficulty):
            self.fail("Total Assault sweep confirmation differs from the persisted ticket budget")
        confirmed = getattr(self, "_assault_sweep_confirmed_intents", set())
        if intent in confirmed:
            self.fail("Total Assault sweep was already confirmed; inspect before retrying")
        self._assault_sweep_confirmed_intents = confirmed | {intent}
        self.journal.save_image(f"sweep-confirmation-{intent}.png", frame.capture.png)
        self.tap(frame, screen.confirm_target, "Confirm the persisted Total Assault sweep")

    def sweep_remaining(self, frame):
        """Use the displayed sweep count and reconcile each ticket decrement."""
        for _ in range(99):
            if frame.screen.tickets == 0:
                return frame
            detail = self.room_after_result(frame)
            self.check_context(detail)
            screen = detail.screen
            if getattr(screen, "sweep_max_target", None) is not None:
                tickets = screen.tickets
                self.tap(detail, screen.sweep_max_target, "Select all remaining Total Assault tickets")
                detail = self.wait("detail", predicate=lambda s: s.count == tickets
                                   and s.tickets == tickets and s.after_tickets == 0)
                self.check_context(detail)
                screen = detail.screen
            if (screen.sweep_target is None or type(screen.count) is not int
                    or not 1 <= screen.count <= screen.tickets
                    or screen.after_tickets != screen.tickets - screen.count):
                self.fail("The game has not enabled a verified sweep at the cleared difficulty")
            intent = assault_state.begin_sweep(self.config, self.context, self.run_dir.name,
                                              screen.tickets, screen.count, now=self.wall_clock())
            self.important("total_assault_sweep_requested", f"Sweep Total Assault {screen.count} time(s)",
                           context=asdict(self.context), tickets_before=screen.tickets,
                           count=screen.count, intent_id=intent)
            self.tap(detail, screen.sweep_target, "Start the displayed Total Assault sweep")
            receipt = self.wait({"receipt", "sweep_confirm"}, timeout=90)
            if receipt.screen.kind == "sweep_confirm":
                self.confirm_sweep(receipt, intent)
                receipt = self.wait("receipt", timeout=90)
            # Seeing the actual receipt plus its corresponding ticket decrement
            # proves completion, even if an item tooltip still needs inspection.
            frame = self.collect_receipts(receipt)
            self.check_return_context(frame)
            state = assault_state.complete_sweep(self.config, intent, tickets_after=frame.screen.tickets,
                                                 rewards_verified=receipt.screen.kind == "receipt",
                                                 now=self.wall_clock())
            self.important("total_assault_swept", state["last_summary"], context=asdict(self.context),
                           tickets_after=frame.screen.tickets, count=screen.count)
        self.fail("Total Assault sweep count exceeded the observed ticket limit")

    def run(self):
        frame = self.stage_menu()
        completed = set()
        for _ in DIFFICULTIES:
            if frame.screen.tickets == 0:
                self.important("total_assault_no_tickets",
                               "No Total Assault tickets remain; waiting for the next game day",
                               target=self.config.total_assault_difficulty,
                               cleared_this_visit=sorted(completed),
                               target_cleared=self.config.total_assault_difficulty in completed)
                break
            stages, frame = self.survey(frame)
            chosen = next_difficulty(self.config.total_assault_difficulty,
                                     {key: value.locked for key, value in stages.items()})
            if chosen in completed:
                self.fail("The prerequisite clear did not unlock the next difficulty; inspect the raid")
            self.important("total_assault_difficulty", f"Preparing {chosen.replace('_', ' ')} Total Assault",
                           target=self.config.total_assault_difficulty, chosen=chosen,
                           prerequisite=chosen != self.config.total_assault_difficulty)
            detail = self.select_stage(frame, chosen)
            detail, team = self.qualify(detail)
            frame = self.real_clear(detail, team)
            completed.add(chosen)
            if chosen == self.config.total_assault_difficulty:
                frame = self.sweep_remaining(frame)
                break
            if frame.screen.kind == "detail":
                self.tap(frame, (1126, 137), "Close Room Info to inspect newly unlocked difficulties")
                frame = self.wait("menu")
        if frame.screen.kind == "detail":
            self.tap(frame, (1126, 137), "Close Total Assault Room Info")
            frame = self.wait("menu")
        self.tap(frame, (1237, 23), "Return home from Total Assault")
        self.home()
        return self.finish()


def run_total_assault(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = TotalAssaultRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            if device.display_size() != (1280, 720):
                runner.fail("Total Assault requires the configured 1280×720 game display")
            state = assault_state.read_state(config)
            if state["pending"] is not None or state["blocked_reason"]:
                runner.fail(state["blocked_reason"] or "A previous Total Assault ticket action needs inspection")
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
