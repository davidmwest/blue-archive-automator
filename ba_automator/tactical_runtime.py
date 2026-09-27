"""Bounded ladder scouting and serial battles with durable ticket accounting."""

from dataclasses import asdict, replace

from .actions import record_action
from .club import game_day
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .tactical_battles import (
    Opponent, TacticalPlanningError, choose_opponent, eligible_opponents,
    estimated_team_levels, opponent_score, ticket_budget,
)
from . import tactical_state as state
from . import tactical_survey as survey
from . import tactical_retry as retry
from .tactical_formation import TacticalFormationMixin
from .tactical_search import SearchPolicy, SearchState
from .tactical_vision import (
    ObservedOpponent, TacticalBattleVision, same_opponent, same_opponent_identity,
)
from .vision import VisionError, decode_frame


def unchanged_opponent_rows(before_png, after_png):
    """Exact visual no-op evidence, independent of optional unit-level OCR.

    This cannot authorize a battle or supply a missing level. Rank, tickets,
    three rank labels and the natural countdown are checked by the caller.
    """
    try:
        before = decode_frame(before_png)[181:646, 425:1240]
        after = decode_frame(after_png)[181:646, 425:1240]
    except (VisionError, TypeError, ValueError):
        return False
    return before.tobytes() == after.tobytes()


class TacticalBattleRunner(TacticalFormationMixin, ShopRunner):
    task = "tactical_battles"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", TacticalBattleVision(startup))
        super().__init__(config, device, startup, **kwargs)
        self.reserve = config.tactical_battles_preserve_tickets
        self.search_policy = SearchPolicy(time_budget_seconds=config.tactical_battles_search_minutes * 60)
        self.search_state = None
        self.search_id = None
        self.search_clock = None
        self.observed = {}
        self.refresh_count = 0
        self.identity_capacity_reached = False
        self.survey_resume = None
        self.defer_survey = False
        self.survey_created_at = None
        self.survey_expired = False
        self.survey_failure_reason = None

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
        """Never use stale observations to authorize a battle, including overtime."""
        created = getattr(self, "survey_created_at", None)
        if created is None or self.wall_clock().timestamp() - created < survey.MAX_AGE_SECONDS:
            return False
        self.survey_expired = True
        self.defer_survey = False
        self.survey_resume = None
        self.save_search(status="blocked")
        self.survey_failure_reason = "Saved opponent observations expired; preserving tickets and consumed search time"
        self.phase(self.survey_failure_reason)
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

    def prepare_refresh(self, frame):
        """Leave enough countdown headroom to verify a reset independently."""
        for _ in range(2):
            seconds = frame.screen.refresh_seconds
            if type(seconds) is not int or not 0 <= seconds <= 121 or seconds <= 114:
                break
            self.advance_search_clock()
            self.sleep(seconds - 114)
            frame = self.menu()
        return frame

    def fail_survey_verification(self, detail):
        """Reject an uncertain draw and retain time already spent searching."""
        self.advance_search_clock()
        self.save_search(status="blocked")
        scheduled = False
        try:
            now = self.wall_clock()
            if getattr(self, "run_day", game_day(now)) == game_day(now):
                scheduled = retry.schedule_retry(self.config, now=now.timestamp())
        except (RuntimeError, OSError) as exc:
            self.journal.record("tactical_survey_retry_unavailable", detail=str(exc))
        self.journal.record("tactical_survey_retry", scheduled=scheduled,
                            delay_seconds=retry.RETRY_DELAY_SECONDS if scheduled else None,
                            daily_limit=retry.MAX_DAILY_RETRIES, reason=detail)
        if scheduled:
            self.phase("Opponent search retry scheduled in 15 minutes with its remaining time; other jobs can continue")
        self.fail(detail)

    def refresh(self, frame):
        self.last_refresh_acknowledged = False
        self.last_refresh_sent = False
        if self.refresh_count >= 1000:
            self.phase("Visit refresh limit reached; preserving the remaining tickets")
            return frame
        for attempt in range(2):
            previous = frame
            frame, ignored = self.refresh_once(frame)
            self.advance_search_clock()
            if self.last_refresh_acknowledged or not self.last_refresh_sent:
                return frame
            if not ignored or attempt:
                self.fail_survey_verification(
                    "Opponent refresh was sent but its timer reset could not be verified; "
                    "search stopped without spending tickets")
            self.journal.record("opponent_refresh_ignored", attempt=attempt + 1,
                                own_rank=previous.screen.rank, tickets=previous.screen.tickets)
            # A verified ignored input is not a new draw. Its observation and
            # waiting still consume the existing allowance, including at the
            # deadline. Never use the local retry to exceed a chunk or day.
            if (not self.survey_time_available()
                    or self.refresh_count >= 1000
                    or game_day(self.wall_clock()) != getattr(self, "run_day", game_day(self.wall_clock()))):
                return frame
        return frame

    def refresh_once(self, frame):
        """Send one refresh and recognize either its reset or a verified no-op."""
        self.last_refresh_sent = False
        # The timer resets even when the draw contains exactly the same ranks.
        # Counting taps would include inputs the game ignored; repeated lists
        # still contribute to the measured refresh rate.
        frame = self.prepare_refresh(frame)
        self.advance_search_clock()
        before_seconds = frame.screen.refresh_seconds
        ranks = frame.screen.sampled_ranks
        if (type(before_seconds) is not int or not 0 <= before_seconds <= 114
                or len(ranks) != 3 or len(set(ranks)) != 3):
            self.phase("Refresh preflight unreadable or not ready; saving progress without sending a refresh")
            return frame, False
        previous = frame
        before_capture = frame.capture.captured_at
        before = frame.screen.sampled_ranks
        try:
            self.tap(frame, frame.screen.refresh_target, "Refresh Tactical Challenge opponents")
            self.last_refresh_sent = True
            self.refresh_count += 1
            self.sleep(1.3)
            frame = self.menu()
            for _ in range(2):
                ranks = frame.screen.sampled_ranks
                seconds = frame.screen.refresh_seconds
                if (len(ranks) == len(set(ranks)) == 3
                        and type(seconds) is int and 0 <= seconds <= 121):
                    break
                # Re-read this draw without sending another refresh. Omitting
                # unreadable ranks or timers would selectively filter samples.
                self.sleep(.7)
                frame = self.menu()
        except Exception:
            # A refresh may already have happened. Preserve consumed time but
            # block automatic continuation after a transport failure.
            self.advance_search_clock()
            self.save_search(status="blocked")
            raise
        after_seconds = frame.screen.refresh_seconds
        elapsed = frame.capture.captured_at - before_capture
        # Compare against the countdown at capture time, before CPU OCR.
        # Two successive fresh lists can both show 1:59. An ignored tap would
        # count down over the elapsed interval; a reset adds several seconds.
        # The three-second margin exceeds timer rounding and capture jitter.
        # A loading overlay may keep the old list visible for most of the
        # menu's 40-second wait. Accept that delayed response only before the
        # original timer could expire, so expiry cannot masquerade as a reset.
        timely_response = 0 < elapsed < min(60, before_seconds)
        timer_reset = (type(after_seconds) is int
                       and 0 <= after_seconds <= 121
                       and timely_response
                       and after_seconds - before_seconds + elapsed >= 3)
        # Changed ranks are useful diagnostic evidence, but cannot bypass the
        # independent timer check: that would favor changed over repeated draws.
        self.last_refresh_acknowledged = timer_reset
        self.journal.record("opponents_refreshed",
                            acknowledged=self.last_refresh_acknowledged,
                            changed=before != frame.screen.sampled_ranks,
                            timer_before=before_seconds, timer_after=after_seconds,
                            capture_elapsed=round(elapsed, 3))
        before_opponents = getattr(previous.screen, "opponents", ())
        after_opponents = getattr(frame.screen, "opponents", ())
        own_rank = getattr(previous.screen, "rank", None)
        tickets = getattr(previous.screen, "tickets", None)
        same_list = (len(before_opponents) == len(after_opponents) == 3
                     and all(same_opponent(left, right)
                             for left, right in zip(before_opponents, after_opponents)))
        if not timer_reset and not same_list:
            # Refreshing does not require battle-level student recognition.
            # The actual ignored-input capture has byte-identical list rows
            # even though contradictory tiny level glyphs exclude every team.
            same_list = unchanged_opponent_rows(previous.capture.png, frame.capture.png)
        ignored = (not timer_reset and type(after_seconds) is int
                   and 0 <= after_seconds < before_seconds and 0 < elapsed < 20
                   and abs(after_seconds - before_seconds + elapsed) < 3
                   and previous.screen.kind == frame.screen.kind == "tactical"
                   and type(own_rank) is int and own_rank > 1
                   and getattr(frame.screen, "rank", None) == own_rank
                   and type(tickets) is int and tickets > self.reserve
                   and getattr(frame.screen, "tickets", None) == tickets
                   and before == frame.screen.sampled_ranks
                   and same_list)
        return frame, ignored

    def advance_search_clock(self):
        """Count active scouting, never queue waiting or the battle itself."""
        clock = getattr(self, "search_clock", None)
        search = getattr(self, "search_state", None)
        if clock is not None and search is not None:
            now = self.clock()
            search.advance(max(0, now - clock))
            self.search_clock = now

    def save_search(self, *, status="active"):
        search = getattr(self, "search_state", None)
        day = getattr(self, "run_day", game_day(self.wall_clock()))
        if search is None or day != game_day(self.wall_clock()):
            return
        self.search_id = survey.save_survey(
            self.config, day_key=day, own_rank=self.survey_own_rank,
            search=search, identities={key: asdict(value) for key, value in self.observed.items()},
            status=status, now=self.wall_clock().timestamp(),
            search_id=getattr(self, "search_id", None))

    def clear_completed_search(self):
        """Clear a resolved battle or rejected preview's disposable search."""
        self.search_state = self.search_clock = self.survey_resume = None
        self.search_id = None
        self.survey_created_at = None
        survey.clear_survey(self.config)

    def scout(self, frame):
        """Select from the visible list using this ticket's time budget."""
        rank = frame.screen.rank
        resumed = getattr(self, "survey_resume", None)
        self.survey_resume = None
        if resumed is not None:
            self.search_state = resumed["search"]
            self.search_id = resumed["search_id"]
            self.survey_created_at = resumed["created_at"]
            self.phase("Continuing saved opponent search with "
                       f"{self.search_state.remaining_seconds:.0f}s remaining")
        if self.search_state is None:
            self.search_state = SearchState(self.search_policy)
            self.search_id = None
            self.survey_created_at = None
        self.survey_own_rank = rank
        self.search_clock = self.clock()
        refreshed = False
        try:
            while True:
                self.advance_search_clock()
                if self.survey_evidence_expired():
                    return frame, ()
                if frame.screen.rank != rank:
                    self.phase("Player rank changed during scouting; keeping the remaining search time")
                    rank = self.survey_own_rank = frame.screen.rank
                    # Defensive battles can move rank without spending this
                    # ticket. Keep the benchmark and allowance; only freshly
                    # observed opponents ahead of the new rank may qualify.
                    if rank == 1:
                        return frame, ()
                observed = tuple(self.remember(p) for p in frame.screen.opponents)
                if self.identity_capacity_reached:
                    self.survey_failure_reason = "Opponent identity limit reached; preserving the remaining tickets"
                    self.phase(self.survey_failure_reason)
                    return frame, ()
                if getattr(frame.screen, "all_ahead", False):
                    eligible = tuple(p.choice for p in observed if p.choice.rank < rank)
                else:
                    try:
                        eligible = eligible_opponents((p.choice for p in observed), rank)
                    except TacticalPlanningError:
                        eligible = ()
                history = state.battle_history(state.read_state(self.config))
                eligible = tuple(p for p in eligible if p.opponent_id not in history.attempts)
                if refreshed or not self.search_state.initial_observed:
                    self.search_state.observe(eligible, refreshed=refreshed)
                decision = self.search_state.decide(eligible)
                self.journal.record("opponent_search", own_rank=rank,
                                    decision=asdict(decision),
                                    candidates=[dict(asdict(p), strength_score=opponent_score(p))
                                                for p in eligible])
                estimate = (f"estimated {decision.estimated_refreshes} refreshes in the time budget"
                            if decision.estimated_refreshes is not None else "measuring refresh speed")
                if decision.phase == "overtime":
                    self.phase("Opponent search in overtime: widening the allowed score "
                               f"after each refresh; current threshold {decision.threshold}")
                else:
                    self.phase(f"Opponent search: {decision.remaining_seconds:.0f}s left; "
                               f"{decision.phase}; {estimate}")
                if decision.opponent_id is not None:
                    selected = next(p for p in eligible if p.opponent_id == decision.opponent_id)
                    self.journal.record("opponent_selected", opponent=asdict(selected),
                                        strength_score=opponent_score(selected),
                                        threshold=decision.threshold, percentile=decision.percentile)
                    return frame, (selected,)
                if not self.survey_time_available():
                    self.defer_survey = True
                    break
                # Persist the budget before input; interruptions cannot grant
                # another full search or erase same-day opponent exclusions.
                self.save_search()
                frame = self.refresh(frame)
                self.advance_search_clock()
                refreshed = self.last_refresh_acknowledged
                if not refreshed:
                    self.defer_survey = True
                    self.phase("No verified refresh; saving progress for another visit")
                    break
                ranks = getattr(frame.screen, "sampled_ranks", ())
                if len(ranks) != 3 or len(set(ranks)) != 3:
                    self.fail_survey_verification(
                        "Incomplete opponent rank labels; search stopped without spending tickets")
            return frame, ()
        finally:
            self.advance_search_clock()
            self.search_clock = None

    def restart_rejected_search(self, frame, *, tickets_before, reason):
        """Recover a rejected pre-entry preview with a fresh search and timer."""
        if frame.screen.tickets != tickets_before:
            self.fail("Ticket count changed while rejecting an opponent; no battle entered")
        if game_day(self.wall_clock()) != getattr(self, "run_day", game_day(self.wall_clock())):
            self.fail("The game day changed while rejecting an opponent; no battle entered")
        previous_search_id = getattr(self, "search_id", None)
        self.clear_completed_search()
        # Keep durable fought-opponent identities, but discard this search's
        # potentially bad readings and benchmark. No battle intent exists yet.
        self.observed = {}
        self.restore_identities(state.read_state(self.config))
        self.search_state = SearchState(self.search_policy)
        self.run_day = game_day(self.wall_clock())
        self.survey_own_rank = frame.screen.rank
        self.survey_created_at = self.wall_clock().timestamp()
        self.save_search()
        self.journal.record("opponent_rejected", reason=reason,
                            previous_search_id=previous_search_id,
                            tickets=tickets_before, search_timer_reset=True)
        self.search_restarted_after_rejection = True
        self.phase("Opponent rejected before entry; restarting the search with a fresh timer")
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
            return self.restart_rejected_search(
                self.menu(), tickets_before=before, reason="Opponent selection expired")
        self.journal.save_image(f"opponent-{candidate.choice.opponent_id}-detail.png", detail.capture.png)
        if (len(detail.screen.opponents) != 1
                or not same_opponent(candidate, detail.screen.opponents[0])
                or detail.screen.rank != rank_before
                or detail.screen.opponents[0].choice.rank >= detail.screen.rank
                or detail.screen.opponents[0].choice.visible_levels != candidate.choice.visible_levels
                or detail.screen.tickets != before
                or detail.screen.after_tickets != before - 1
                or detail.screen.after_tickets < self.reserve):
            self.journal.record("opponent_preflight_rejected",
                                expected=asdict(candidate.choice),
                                observed=[asdict(p.choice) for p in detail.screen.opponents],
                                tickets_before=before, projected_tickets=detail.screen.after_tickets)
            self.tap(detail, (1014, 97), "Close rejected opponent detail")
            return self.restart_rejected_search(
                self.menu(), tickets_before=before,
                reason="Opponent or projected ticket count changed")
        self.tap(detail, detail.screen.target, "Open the saved attack formation")
        formation = self.wait({"formation", "timeout_notice"})
        if formation.screen.kind == "timeout_notice":
            self.tap(formation, formation.screen.target, "Dismiss expired opponent")
            return self.restart_rejected_search(
                self.menu(), tickets_before=before, reason="Opponent formation expired")
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
            return self.restart_rejected_search(
                self.menu(), tickets_before=before, reason="Opponent formation expired")
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
        self.phase("Waiting for the Tactical Challenge battle result")
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
        # Remove disposable search data while the proven pending result still
        # prevents replay. A crash on either side of completion cannot reuse
        # this match's benchmark for the next ticket.
        self.clear_completed_search()
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
        self.survey_failure_reason = None
        pending = state.read_state(self.config).get("pending")
        if pending:
            # Leave an unproven entry's search untouched. Once its outcome is
            # proven, the pending intent protects the gap before reconciliation.
            if pending.get("outcome") is not None:
                self.clear_completed_search()
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
        # A manual/Daily visit supersedes a waiting fresh-search dispatch, but
        # never restores its already consumed automatic retry allowance.
        retry.clear_retry(self.config)
        self.survey_resume = survey.load_survey(
            self.config, day_key=day, own_rank=frame.screen.rank,
            time_budget_seconds=self.search_policy.time_budget_seconds,
            now=self.wall_clock().timestamp())
        if self.survey_resume is not None:
            self.restore_identities(self.survey_resume)
            self.search_state = self.survey_resume["search"]
            self.search_id = self.survey_resume["search_id"]
            self.survey_created_at = self.survey_resume["created_at"]
            self.survey_own_rank = frame.screen.rank
        else:
            survey.clear_survey(self.config)
            self.search_id = None
        self.restore_identities(persisted)
        battles = 0
        expired_choices = 0
        while (state.search_allowances(state.read_state(self.config), frame.screen.tickets, self.reserve)
               and frame.screen.rank > 1):
            if game_day(self.wall_clock()) != day:
                self.phase("The game day changed; the next daily visit will use the refreshed tickets")
                break
            # Standby is not scouting. Wait before choosing so the selected
            # current list remains valid for its immediate detail inspection.
            if getattr(frame.screen, "cooldown", 0):
                self.phase(f"Waiting {frame.screen.cooldown}s for Tactical Challenge standby")
                self.sleep(min(frame.screen.cooldown + 1, 35))
                frame = self.menu()
                continue
            frame, shortlist = self.scout(frame)
            self.save_identities(day)
            history = state.battle_history(state.read_state(self.config))
            try:
                choice = choose_opponent(shortlist, frame.screen.rank, history,
                                         frame.screen.tickets, self.reserve)
            except TacticalPlanningError:
                choice = None
            if choice is None:
                break
            # Selection is only from this frame. No historical-target lookup
            # and no refreshes after the search to chase an absent opponent.
            candidate = next((self.remember(p) for p in frame.screen.opponents
                              if same_opponent(self.observed[choice.opponent_id], p)), None)
            if candidate is None:
                self.fail("Selected opponent is no longer verified on the current list")
            if not self.battle_time_available():
                self.phase("Battle time budget reserved for a future visit; no ticket spent")
                self.defer_survey = True
                break
            self.search_restarted_after_rejection = False
            before_battle = self.clock()
            frame, won = self.battle(frame, candidate)
            if won is None:
                if self.survey_expired:
                    break
                # Rejected/expired previews explicitly restart the search.
                # Other deferrals keep the time already spent selecting.
                if self.search_state is not None and not self.search_restarted_after_rejection:
                    self.search_state.advance(max(0, self.clock() - before_battle))
                expired_choices += 1
                if expired_choices >= 2:
                    self.defer_survey = True
                    break
                continue
            battles += 1
            self.clear_completed_search()
            expired_choices = 0
            if battles >= 5:
                break
        persisted = state.read_state(self.config)
        allowances = state.search_allowances(persisted, frame.screen.tickets, self.reserve)
        self.phase(f"Tactical Challenge visit: {battles} battles; "
                   f"{frame.screen.tickets} tickets left, reserve {self.reserve}; "
                   f"{allowances} tickets available for automatic battles")
        if (self.search_state is not None and game_day(self.wall_clock()) == day
                and frame.screen.rank == self.survey_own_rank
                and ticket_budget(frame.screen.tickets, self.reserve)):
            self.save_search(status="blocked" if self.survey_expired or frame.screen.rank == 1 else "active")
            if self.defer_survey:
                self.phase("Opponent search saved; the queue will continue it after other work")
        else:
            survey.clear_survey(self.config)
        self.tap(frame, (1237, 23), "Return home from Tactical Challenge battles")
        self.home()
        # These exits invalidate the evidence rather than save a resumable
        # search. Surface them after returning home so unused tickets do not
        # disappear behind a successful daily step with no continuation.
        if self.survey_failure_reason and ticket_budget(frame.screen.tickets, self.reserve):
            self.fail(self.survey_failure_reason)
        return self.finish("deferred" if self.defer_survey else "success")

    def recover_pending_result(self):
        """Preserve a completed battle before any home navigation can hide it.

        The result alone never settles a ticket expenditure. Save its evidence
        before dismissal, then let run_menu require the exact ticket decrement.
        An unproven intent cannot authorize navigation away from another screen.
        """
        persisted = state.read_state(self.config)
        pending = persisted.get("pending")
        if pending is None:
            return None
        if pending["day_key"] != game_day(self.wall_clock()):
            self.fail("Unresolved Tactical Challenge battle crossed the game-day reset; inspect it manually")
        if persisted.get("blocked_reason") == state.CONFLICTING_OUTCOMES:
            self.fail(state.CONFLICTING_OUTCOMES)
        self.phase("Checking the interrupted Tactical Challenge battle before navigating")
        frame = self.wait({"result", "tactical", "home", "campaign", "battle_tip"}, timeout=40)
        if pending["day_key"] != game_day(self.wall_clock()):
            self.fail("The game day changed while checking the interrupted battle; no input sent")
        if frame.screen.kind == "result":
            won = frame.screen.won
            if type(won) is not bool:
                self.fail("The interrupted battle result has no verified victory or defeat")
            evidence = f'battle-{pending["id"]}-recovered-result.png'
            self.journal.save_image(evidence, frame.capture.png)
            state.record_outcome(self.config, pending["id"], won=won,
                                 evidence=str(self.run_dir / evidence), now=self.wall_clock())
            self.journal.record("battle_outcome_recovered", intent_id=pending["id"], won=won)
            self.tap(frame, frame.screen.target, "Close recovered Tactical Challenge battle result")
            return self.menu()
        if pending.get("outcome") is None:
            self.fail("The interrupted Tactical Challenge battle has no verified result on screen; "
                      "preserving its ticket hold for inspection")
        if frame.screen.kind == "battle_tip":
            self.tap(frame, frame.screen.target, "Dismiss notice after the saved battle result")
            return self.menu()
        if frame.screen.kind == "tactical":
            return frame
        # A previously saved outcome can survive a restart. Home/Campaign may
        # now be navigated normally, retaining the pending intent until menu
        # reconciliation succeeds.
        return None

    def run(self):
        recovered = self.recover_pending_result()
        if recovered is not None:
            return self.run_menu(recovered)
        frame = self.navigate("home", "campaign", (1200, 641))
        # Campaign can remain visible for several seconds after accepting the
        # input. Repeating its coordinate can select an opponent as the new
        # screen arrives, so this transition gets one input and a read-only wait.
        self.tap(frame, (868, 581), "Open tactical")
        return self.run_menu(self.wait("tactical"))


def run_tactical_battles(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = TacticalBattleRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            try:
                runner.advance_search_clock()
                runner.save_search(status="blocked")
            except (OSError, RuntimeError, ValueError) as checkpoint_error:
                runner.journal.record("search_checkpoint_failed", detail=str(checkpoint_error))
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
