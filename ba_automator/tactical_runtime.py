"""Bounded ladder scouting and serial battles with durable ticket accounting."""

from dataclasses import asdict, replace

from .actions import record_action
from .club import game_day
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .tactical_battles import (
    Opponent, TacticalPlanningError, choose_opponent, eligible_opponents,
    estimated_team_levels, opponent_score, rank_opponents, ticket_budget,
)
from . import tactical_state as state
from . import tactical_survey as survey
from .tactical_formation import TacticalFormationMixin
from .tactical_refresh import RefreshPlanner
from .tactical_vision import (
    ObservedOpponent, TacticalBattleVision, same_opponent, same_opponent_identity,
)


class TacticalBattleRunner(TacticalFormationMixin, ShopRunner):
    task = "tactical_battles"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", TacticalBattleVision(startup))
        super().__init__(config, device, startup, **kwargs)
        self.reserve = config.tactical_battles_preserve_tickets
        self.refresh_limit = config.tactical_battles_refresh_limit
        self.confidence = round(config.tactical_battles_confidence_percent / 100, 12)
        self.observed = {}
        self.refresh_count = 0
        self.identity_capacity_reached = False
        self.survey_resume = None
        self.survey_candidates = {}
        self.lookup_progress = None
        self.defer_survey = False
        self.survey_created_at = None
        self.survey_expired = False

    def restore_identities(self, persisted):
        for key, value in persisted.get("identities", {}).items():
            self.observed[key] = ObservedOpponent(
                Opponent(**value["choice"]), value["name"],
                tuple(value["target"]), value["signature"])

    def save_identities(self, day):
        state.save_identities(self.config, day,
                              {key: asdict(value) for key, value in self.observed.items()},
                              now=self.wall_clock())

    def budget(self):
        # Four battles, their cooldowns, and bounded surveys can exceed the
        # short collectors' ten-minute budget. Never hold the queue forever.
        if self.clock() - self.started > 1800 or self.actions >= 1100:
            self.fail("Tactical Challenge reached its 30-minute or input limit")

    def survey_time_available(self):
        # Save long searches in five-minute / 100-refresh chunks so AP and
        # other queued work get their turn. A battle keeps its own result budget.
        return (self.clock() - self.started < 300 and self.actions < 250
                and self.refresh_count < 100)

    def battle_time_available(self):
        # Reserve the full result wait plus navigation and reconciliation,
        # including when Skip is enabled but the game ignores that setting.
        return self.clock() - self.started < 1080 and self.actions < 1020

    def survey_evidence_expired(self):
        """A resumed certificate must still be fresh when it spends a ticket."""
        created = getattr(self, "survey_created_at", None)
        if created is None or self.wall_clock().timestamp() - created < survey.MAX_AGE_SECONDS:
            return False
        self.survey_expired = True
        self.defer_survey = False
        self.refresh_planner = None
        self.survey_resume = None
        survey.clear_survey(self.config)
        self.phase("Saved opponent survey expired; preserving tickets for a fresh visit")
        return True

    def remember(self, candidate):
        """Reuse a verified portrait identity across temporary list positions."""
        previous = next((old for old in self.observed.values()
                         if same_opponent_identity(old, candidate)), None)
        if previous is not None:
            candidate = replace(candidate, choice=replace(
                candidate.choice, opponent_id=previous.choice.opponent_id))
        elif len(self.observed) >= state.MAX_IDENTITIES:
            self.identity_capacity_reached = True
            return candidate
        self.observed[candidate.choice.opponent_id] = candidate
        return candidate

    def menu(self):
        for _ in range(3):
            frame = self.wait({"tactical", "timeout_notice", "battle_tip"})
            if frame.screen.kind == "tactical":
                return frame
            self.tap(frame, frame.screen.target, "Dismiss Tactical Challenge notice")
        self.fail("Repeated Tactical Challenge notices prevented returning to the menu")

    def refresh(self, frame):
        self.last_refresh_acknowledged = False
        if self.refresh_count >= 1000:
            self.phase("Visit refresh limit reached; preserving the remaining tickets")
            return frame
        # The timer resets even when the draw contains exactly the same ranks.
        # Counting only changed lists would bias the pool estimate toward new
        # opponents; counting taps would include inputs the game ignored.
        before_seconds = frame.screen.refresh_seconds
        if type(before_seconds) is not int or not 0 <= before_seconds <= 120:
            self.phase("Refresh timer unreadable; deferring the opponent survey")
            return frame
        before_capture = frame.capture.captured_at
        before = frame.screen.sampled_ranks
        self.tap(frame, frame.screen.refresh_target, "Refresh Tactical Challenge opponents")
        self.refresh_count += 1
        self.sleep(1.3)
        frame = self.menu()
        after_seconds = frame.screen.refresh_seconds
        elapsed = frame.capture.captured_at - before_capture
        # Compare against the countdown at capture time, before CPU OCR.
        # Two successive fresh lists can both show 1:59. An ignored tap would
        # count down over the elapsed interval; a reset adds several seconds.
        # The three-second margin exceeds timer rounding and capture jitter.
        self.last_refresh_acknowledged = (type(after_seconds) is int
                                           and 116 <= after_seconds <= 120
                                           and 0 < elapsed < 20
                                           and after_seconds - before_seconds + elapsed >= 3)
        self.journal.record("opponents_refreshed",
                            acknowledged=self.last_refresh_acknowledged,
                            changed=before != frame.screen.sampled_ranks,
                            timer_before=before_seconds, timer_after=after_seconds,
                            capture_elapsed=round(elapsed, 3))
        return frame

    def scout(self, frame):
        rank = frame.screen.rank
        resumed = getattr(self, "survey_resume", None)
        self.survey_resume = None
        if resumed is not None:
            planner = resumed["planner"]
            self.survey_created_at = resumed["created_at"]
            candidates = {p["opponent_id"]: Opponent(**p) for p in resumed["candidates"]}
            self.lookup_progress = resumed["lookup"]
            self.phase(f"Continuing saved opponent survey after {planner.valid_draws} verified refreshes")
        else:
            survey.clear_survey(self.config)
            candidates = {}
            self.lookup_progress = None
            self.survey_created_at = None
            planner = RefreshPlanner(pilot=max(2, self.refresh_limit), max_refreshes=1000,
                                     confidence=self.confidence)
        self.survey_candidates = candidates
        self.refresh_planner = planner
        self.survey_own_rank = rank
        while True:
            if self.survey_evidence_expired():
                return frame, ()
            if frame.screen.rank != rank:
                self.phase("Player rank changed during scouting; discarding the old survey")
                self.refresh_planner = None
                return frame, ()
            observed = tuple(self.remember(p) for p in frame.screen.opponents)
            if self.identity_capacity_reached:
                self.phase("Opponent identity limit reached; deferring this survey")
                self.refresh_planner = None
                return frame, ()
            if getattr(frame.screen, "all_ahead", False):
                # Rank labels are independent of team OCR. A fully read team
                # remains usable when another team's visible levels are fuzzy.
                eligible = tuple(p.choice for p in observed if p.choice.rank < rank)
            else:
                try:
                    eligible = eligible_opponents((p.choice for p in observed), rank)
                except TacticalPlanningError:
                    eligible = ()
            for opponent in eligible:
                candidates[opponent.opponent_id] = opponent
            plan = planner.plan()
            if plan.physical_refreshes % 10 == 0 or plan.status in {"complete", "deferred"}:
                target = str(plan.target_valid_draws) if plan.target_valid_draws is not None else "estimating"
                self.phase(f"Scouting opponents: {plan.physical_refreshes} refreshes, "
                           f"{len(candidates)} candidates; model-based {self.confidence:.1%} target: {target}")
            if plan.status in {"complete", "deferred"} or not self.survey_time_available():
                self.defer_survey = plan.status not in {"complete", "deferred"}
                break
            frame = self.refresh(frame)
            if not self.last_refresh_acknowledged:
                self.phase("Refresh was not acknowledged; no confidence claim or battle entry")
                break
            ranks = getattr(frame.screen, "sampled_ranks", ())
            if len(ranks) != 3 or len(set(ranks)) != 3:
                # Discarding unreadable draws selectively could underestimate
                # the pool when some rank labels are harder to recognize.
                self.phase("Incomplete rank labels; deferring the opponent survey")
                break
            planner.observe(ranks)
        ranked = rank_opponents(candidates.values())
        self.journal.record("opponent_survey", own_rank=rank,
                            coverage=asdict(plan),
                            candidates=[dict(asdict(p), estimated_levels=estimated_team_levels(p),
                                             strength_score=opponent_score(p))
                                        for p in ranked])
        if plan.status != "complete":
            self.phase(f"Survey deferred before the model-based {self.confidence:.1%} target; no battle entered")
            return frame, ()
        return frame, ranked

    def locate(self, frame, opponent):
        if self.survey_evidence_expired():
            return frame, None
        expected = self.observed[opponent.opponent_id]
        planner = getattr(self, "refresh_planner", None)
        if planner is None:
            self.phase("No completed coverage estimate is available; a fresh survey is required")
            return frame, None
        if frame.screen.rank != self.survey_own_rank:
            self.phase("Player rank changed since scouting; a fresh survey is required")
            self.refresh_planner = None
            return frame, None
        lookup = planner.lookup_plan(opponent.rank)
        self.journal.record("opponent_lookup", opponent_id=opponent.opponent_id,
                            estimate=asdict(lookup))
        if not lookup.within_limit:
            self.phase(lookup.reason)
            return frame, None
        rank = frame.screen.rank
        progress = getattr(self, "lookup_progress", None)
        valid = (progress["valid_draws"] if progress is not None
                 and progress["opponent_id"] == opponent.opponent_id else 0)
        self.lookup_progress = {"opponent_id": opponent.opponent_id, "valid_draws": valid}
        for index in range(1001):
            if self.survey_evidence_expired():
                return frame, None
            if frame.screen.rank != rank:
                return frame, None
            candidate = next((p for p in frame.screen.opponents
                              if same_opponent(expected, p)), None)
            if candidate is not None and candidate.choice.rank < frame.screen.rank:
                # A changed level estimate requires a new survey rather than
                # fighting a stale choice with different public team data.
                if opponent_score(candidate.choice) != opponent_score(opponent):
                    return frame, None
                return frame, self.remember(candidate)
            if valid >= lookup.required_refreshes:
                self.phase(f"Opponent absent at the model-based {self.confidence:.1%} lookup limit; assuming the ladder changed")
                break
            if index >= 1000 or not self.survey_time_available():
                self.defer_survey = True
                self.phase("Saving opponent lookup progress so other queued jobs can run")
                break
            frame = self.refresh(frame)
            if not self.last_refresh_acknowledged:
                self.phase("Refresh was not acknowledged; deferring target lookup")
                break
            ranks = frame.screen.sampled_ranks
            # Avoid filtering samples by team OCR quality. Independently read
            # rank labels prove absence, unless the chosen rank is present but
            # its team is unreadable; then defer instead of declaring movement.
            if len(ranks) != 3 or len(set(ranks)) != 3:
                self.phase("Incomplete rank labels; deferring target lookup")
                break
            if opponent.rank in ranks and not any(
                    same_opponent(expected, p) for p in frame.screen.opponents):
                self.phase("Selected rank is present but its identity is unverified; a fresh survey is required")
                break
            valid += 1
            self.lookup_progress["valid_draws"] = valid
        return frame, None

    def battle(self, frame, candidate):
        if self.survey_evidence_expired():
            return frame, None
        if not self.battle_time_available():
            self.phase("Not enough time remains to finish a battle safely; preserving tickets")
            return frame, None
        day = game_day(self.wall_clock())
        if getattr(self, "run_day", day) != day:
            self.fail("The game day changed during scouting; no battle entered")
        before, rank_before = frame.screen.tickets, frame.screen.rank
        if ticket_budget(before, self.reserve) == 0:
            self.fail("The manual-play ticket reserve has been reached")
        self.journal.save_image(f"opponent-{candidate.choice.opponent_id}-list.png", frame.capture.png)
        self.tap(frame, candidate.target, "Inspect selected Tactical Challenge opponent")
        detail = self.wait({"opponent", "timeout_notice"})
        if detail.screen.kind == "timeout_notice":
            self.tap(detail, detail.screen.target, "Dismiss expired opponent")
            return self.menu(), None
        if (len(detail.screen.opponents) != 1
                or not same_opponent(candidate, detail.screen.opponents[0])
                or detail.screen.rank != rank_before
                or detail.screen.opponents[0].choice.rank >= detail.screen.rank
                or detail.screen.opponents[0].choice.visible_levels != candidate.choice.visible_levels
                or detail.screen.tickets != before
                or detail.screen.after_tickets != before - 1
                or detail.screen.after_tickets < self.reserve):
            self.fail("Opponent or projected ticket count changed; no battle entered")
        self.journal.save_image(f"opponent-{candidate.choice.opponent_id}-detail.png", detail.capture.png)
        self.tap(detail, detail.screen.target, "Open the saved attack formation")
        formation = self.wait({"formation", "timeout_notice"})
        if formation.screen.kind == "timeout_notice":
            self.tap(formation, formation.screen.target, "Dismiss expired opponent")
            return self.menu(), None
        formation = self.fill_attack_formation(formation)
        skip = self.config.tactical_battles_skip_battles
        if type(formation.screen.skip_selected) is not bool:
            self.fail("The battle skip setting could not be verified")
        if formation.screen.skip_selected != skip:
            self.tap(formation, (1115, 604),
                     "Enable battle skip" if skip else "Watch Tactical Challenge battle")
            formation = self.wait("formation", predicate=lambda s: s.skip_selected == skip)
        if game_day(self.wall_clock()) != day:
            self.fail("The game day changed during formation; no battle entered")
        if formation.screen.formation_seconds is None:
            formation = self.wait("formation", predicate=lambda s: s.formation_seconds is not None)
        if formation.screen.formation_seconds < 10:
            self.tap(formation, (54, 33), "Leave expiring opponent formation")
            return self.menu(), None
        if game_day(self.wall_clock()) != day:
            self.fail("The game day changed during formation; no battle entered")
        if not self.battle_time_available():
            self.tap(formation, (54, 33), "Leave formation before the battle time budget expires")
            return self.menu(), None
        if self.survey_evidence_expired():
            self.tap(formation, (54, 33), "Leave formation after the opponent survey expired")
            return self.menu(), None
        intent = state.begin_battle(
            self.config, day, candidate.choice.opponent_id, before, rank_before,
            preserve=self.reserve, now=self.wall_clock())
        self.journal.record("battle_reserved", intent_id=intent,
                            opponent=asdict(candidate.choice), tickets_before=before,
                            estimated_team_levels=estimated_team_levels(candidate.choice),
                            strength_score=opponent_score(candidate.choice))
        self.tap(formation, formation.screen.target, "Mobilize saved Tactical Challenge team")
        # Skill cinematics pause the in-game timer. The live unskipped match
        # took more than four minutes despite only 1:28 of combat time.
        result = self.wait("result", timeout=600)
        won = result.screen.won
        self.journal.save_image(f"battle-{intent}-result.png", result.capture.png)
        state.record_outcome(self.config, intent, won=won,
                             evidence=str(self.run_dir / f"battle-{intent}-result.png"),
                             now=self.wall_clock())
        self.journal.record("battle_outcome", intent_id=intent, won=won)
        self.tap(result, result.screen.target, "Close Tactical Challenge battle result")
        frame = self.menu()
        state.complete_battle(self.config, intent, tickets_after=frame.screen.tickets,
                              won=won, rank_after=frame.screen.rank,
                              preserve=self.reserve, now=self.wall_clock())
        record_action(
            self.config, "tactical_battle_completed",
            f'Tactical Challenge {"victory" if won else "defeat"}: '
            f'rank {rank_before} → {frame.screen.rank}; {frame.screen.tickets} tickets remain',
            task=self.task, won=won, opponent_rank=candidate.choice.rank,
            opponent_level=candidate.choice.level,
            estimated_team_level=estimated_team_levels(candidate.choice),
            strength_score=opponent_score(candidate.choice),
            visible_levels=list(candidate.choice.visible_levels),
            tickets_before=before, tickets_after=frame.screen.tickets,
            rank_before=rank_before, rank_after=frame.screen.rank,
            evidence=str(self.run_dir / f"battle-{intent}-result.png"), skip_battle=skip)
        return frame, won

    def run_menu(self, frame):
        day = game_day(self.wall_clock())
        self.run_day = day
        self.defer_survey = False
        self.survey_expired = False
        pending = state.read_state(self.config).get("pending")
        if pending:
            state.reconcile_pending(self.config, day, tickets=frame.screen.tickets,
                                    rank=frame.screen.rank, preserve=self.reserve,
                                    now=self.wall_clock())
            record_action(self.config, "tactical_battle_recovered",
                          "Recovered a verified Tactical Challenge result after interruption",
                          task=self.task, opponent_id=pending["opponent_id"],
                          won=pending["outcome"]["won"],
                          evidence=pending["outcome"]["evidence"],
                          tickets_before=pending["tickets_before"],
                          tickets_after=frame.screen.tickets, rank_after=frame.screen.rank)
        persisted = state.observe_ladder(self.config, day, tickets=frame.screen.tickets,
                                         rank=frame.screen.rank, preserve=self.reserve,
                                         now=self.wall_clock())
        if persisted.get("pending") or persisted.get("blocked_reason"):
            self.fail("A previous Tactical Challenge battle needs its result checked before another entry")
        self.survey_resume = survey.load_survey(
            self.config, day_key=day, own_rank=frame.screen.rank,
            confidence=self.confidence, pilot=max(2, self.refresh_limit),
            now=self.wall_clock().timestamp())
        if self.survey_resume is not None:
            self.restore_identities(self.survey_resume)
        else:
            survey.clear_survey(self.config)
        self.restore_identities(persisted)
        shortlist = ()
        restarts = 0
        battles = 0
        while ticket_budget(frame.screen.tickets, self.reserve) and frame.screen.rank > 1:
            if game_day(self.wall_clock()) != day:
                self.phase("The game day changed; the next daily visit will use the refreshed tickets")
                break
            history = state.battle_history(state.read_state(self.config))
            if not shortlist:
                frame, shortlist = self.scout(frame)
                self.save_identities(day)
            # When a missing opponent exhausted both lookup and a fresh survey,
            # release the queue. Preserve attempts; don't loop endlessly.
            try:
                choice = choose_opponent(shortlist, frame.screen.rank, history,
                                         frame.screen.tickets, self.reserve)
            except TacticalPlanningError:
                choice = None
            if choice is None:
                if not self.defer_survey:
                    self.phase("No eligible opponent remains in the verified survey")
                break
            progress = getattr(self, "lookup_progress", None)
            if progress is not None:
                # Keep the original coin flip between equal scores across
                # visits, so a tied candidate's lookup does not start over.
                previous = next((p for p in shortlist
                                 if p.opponent_id == progress["opponent_id"]
                                 and p.opponent_id not in history.attempts
                                 and p.rank < frame.screen.rank
                                 and opponent_score(p) == opponent_score(choice)), None)
                if previous is not None:
                    choice = previous
            frame, candidate = self.locate(frame, choice)
            if candidate is None:
                if self.defer_survey or self.survey_expired:
                    break
                restarts += 1
                if restarts >= 2:
                    self.phase("Selected opponents disappeared; stopping after two bounded surveys")
                    break
                # A new survey never erases today's completed opponents.
                shortlist = ()
                continue
            if frame.screen.cooldown:
                self.phase(f"Waiting {frame.screen.cooldown}s for Tactical Challenge standby")
                self.sleep(min(frame.screen.cooldown + 1, 35))
                frame = self.menu()
                continue
            self.save_identities(day)
            if not self.battle_time_available():
                self.phase("Battle time budget reserved for a future visit; no ticket spent")
                self.defer_survey = True
                break
            frame, won = self.battle(frame, candidate)
            if won is None:
                if self.survey_expired:
                    break
                shortlist = ()
                restarts += 1
                if restarts >= 2:
                    break
                continue
            battles += 1
            self.lookup_progress = None
            if won:
                shortlist = ()
            if battles >= 5:
                break
        self.phase(f"Tactical Challenge finished: {battles} battles; "
                   f"{frame.screen.tickets} tickets left, reserve {self.reserve}")
        if (self.defer_survey and getattr(self, "refresh_planner", None) is not None
                and game_day(self.wall_clock()) == day
                and frame.screen.rank == self.survey_own_rank
                and ticket_budget(frame.screen.tickets, self.reserve)):
            survey.save_survey(
                self.config, day_key=day, own_rank=self.survey_own_rank,
                confidence=self.confidence, pilot=max(2, self.refresh_limit),
                planner=self.refresh_planner,
                candidates=[asdict(p) for p in self.survey_candidates.values()],
                identities={key: asdict(value) for key, value in self.observed.items()},
                lookup=getattr(self, "lookup_progress", None),
                now=self.wall_clock().timestamp())
            self.phase("Opponent search saved; the queue will continue it after other work")
        else:
            survey.clear_survey(self.config)
        self.tap(frame, (1237, 23), "Return home from Tactical Challenge battles")
        self.home()
        return self.finish()

    def run(self):
        self.navigate("home", "campaign", (1200, 641))
        return self.run_menu(self.navigate("campaign", "tactical", (868, 581)))


def run_tactical_battles(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = TacticalBattleRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
