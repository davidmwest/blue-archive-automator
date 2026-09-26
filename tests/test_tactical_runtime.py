"""Serial battle flow preserves tickets across scouting, result waits and failures."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from ba_automator import tactical_state as state
from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_runtime import TacticalBattleRunner
from ba_automator.tactical_refresh import LookupPlan, RefreshPlan
from ba_automator.tactical_vision import ObservedOpponent, same_opponent, same_opponent_identity


@dataclass(frozen=True)
class Candidate:
    choice: Opponent
    target: tuple = (640, 230)


def frame(kind="tactical", *, captured_at=0, **values):
    if kind == "opponent":
        values.setdefault("rank", 100)
    if kind == "tactical" and "opponents" in values:
        values.setdefault("sampled_ranks", tuple(p.choice.rank for p in values["opponents"]))
    return NS(screen=NS(kind=kind, **values),
              capture=NS(png=b"image", captured_at=captured_at))


class TinyPlanner:
    """A cheap contract double; statistical bounds have their own tests."""

    def __init__(self, **kwargs):
        self.samples = []
        self.physical_refreshes = self.valid_draws = 0
        self.confidence = kwargs.get("confidence", .99)

    def observe(self, ranks):
        self.samples.append(ranks)
        self.physical_refreshes += 1
        if ranks is not None and len(ranks) == 3:
            self.valid_draws += 1
        return self.plan()

    def plan(self):
        status = "complete" if self.valid_draws >= 3 else (
            "deferred" if self.physical_refreshes >= 3 else "pilot")
        return RefreshPlan(status, self.physical_refreshes, self.valid_draws,
                           (), 3, self.confidence, "Test sampling contract")

    def lookup_plan(self, rank):
        return LookupPlan(3, 0, True, self.confidence, "Test lookup contract")


@pytest.mark.parametrize("percent", [80, 90, 99, 99.9])
def test_runner_converts_configured_percentage_into_a_supported_probability(monkeypatch, percent):
    from ba_automator.tactical_refresh import RefreshPlanner
    monkeypatch.setattr("ba_automator.tactical_runtime.ShopRunner.__init__", lambda *a, **k: None)
    config = NS(tactical_battles_preserve_tickets=1, tactical_battles_refresh_limit=50,
                tactical_battles_confidence_percent=percent)
    runner = TacticalBattleRunner(config, object(), object())
    planner = RefreshPlanner(confidence=runner.confidence)
    assert planner.plan().confidence == pytest.approx(percent / 100)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    r = object.__new__(TacticalBattleRunner)
    r.config = NS(serial="test", package="test", state_dir=tmp_path,
                  tactical_battles_skip_battles=True)
    r.reserve, r.refresh_limit, r.observed = 1, 3, {}
    r.confidence = .99
    r.last_refresh_acknowledged = True
    r.run_dir = tmp_path
    r.started = r.actions = r.refresh_count = 0
    r.clock = lambda: 0
    r.identity_capacity_reached = False
    r.survey_own_rank = 100
    r.survey_resume = None
    r.survey_candidates = {}
    r.lookup_progress = None
    r.defer_survey = False
    monkeypatch.setattr("ba_automator.tactical_runtime.RefreshPlanner", TinyPlanner)
    r.wall_clock = lambda: datetime(2026, 9, 26, 21, tzinfo=timezone.utc)
    r.phase = lambda text: None
    r.events = []
    r.journal = NS(record=lambda *a, **k: r.events.append((a, k)),
                   save_image=lambda *a: None)
    r.fail = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    r.inputs = []
    r.tap = lambda f, target, detail: r.inputs.append((target, detail))
    r.home = lambda: None
    r.finish = lambda: "done"
    for matcher in ("same_opponent", "same_opponent_identity"):
        monkeypatch.setattr(f"ba_automator.tactical_runtime.{matcher}",
                            lambda a, b: a.choice.opponent_id == b.choice.opponent_id)
    r.important = []
    monkeypatch.setattr("ba_automator.tactical_runtime.record_action",
                        lambda *a, **k: r.important.append((a, k)))
    return r


def test_survey_is_bounded_and_collects_across_refreshes(runner):
    calls = []
    def menu(index):
        return frame(rank=1000, opponents=tuple(
            Candidate(Opponent(str(index * 3 + n), 100 + n, 80 + index))
            for n in range(3)))
    def refresh(f):
        calls.append(f)
        return menu(len(calls))
    runner.refresh = refresh
    _, ranked = runner.scout(menu(0))
    assert len(calls) == 3 and len(ranked) == 12
    assert ranked[0].level == 80 and ranked[-1].level == 83



def test_initial_menu_is_not_counted_as_a_refresh_draw(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    initial = frame(rank=100, opponents=candidates, sampled_ranks=(70, 71, 72))
    refreshed = frame(rank=100, opponents=candidates, sampled_ranks=(80, 81, 82))
    runner.refresh = lambda f: refreshed
    runner.scout(initial)
    assert runner.refresh_planner.samples == [(80, 81, 82)] * 3


def test_configured_confidence_reaches_sampling_planner(runner):
    runner.confidence = .90
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    f = frame(rank=100, opponents=candidates)
    runner.refresh = lambda previous: f
    assert runner.scout(f)[1]
    assert runner.refresh_planner.confidence == .90


def test_unacknowledged_refresh_defers_without_counting_a_draw_or_fighting(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    f = frame(rank=100, opponents=candidates)
    runner.last_refresh_acknowledged = False
    runner.refresh = lambda previous: f
    assert runner.scout(f)[1] == ()
    assert runner.refresh_planner.valid_draws == 0
    assert runner.inputs == []


@pytest.mark.parametrize("before,after,elapsed,acknowledged", [
    (119, 114, 5, False),   # Ignored input follows the normal countdown.
    (119, 119, 5, True),    # Reset can leave the displayed integer unchanged.
    (120, 119, 5, True),
    (119, 118, 5, True),
    (115, 119, 1.3, True),
    (119, 119, 2.99, False),
    (119, 119, 3, True),
    (119, 115, 10, False),  # The observed result must still be near reset.
    (115, 119, 0, False),
    (115, 119, -1, False),
    (115, 119, 20, False),
    (115, 119, float("inf"), False),
    (115, 119, float("nan"), False),
])
def test_refresh_ack_uses_capture_elapsed_time_for_identical_lists(
        runner, before, after, elapsed, acknowledged):
    ranks = (70, 71, 72)
    initial = frame(refresh_seconds=before, sampled_ranks=ranks,
                    refresh_target=(1174, 147), captured_at=10)
    result = frame(refresh_seconds=after, sampled_ranks=ranks,
                   captured_at=10 + elapsed)
    sleeps = []
    runner.sleep = sleeps.append
    runner.menu = lambda: result
    assert runner.refresh(initial) is result
    assert runner.last_refresh_acknowledged is acknowledged
    assert runner.refresh_count == 1 and len(runner.inputs) == 1
    assert sleeps == [1.3]  # No artificial wait for the clock to tick down.
    event = next(fields for names, fields in runner.events if names == ("opponents_refreshed",))
    assert event["changed"] is False and event["acknowledged"] is acknowledged


@pytest.mark.parametrize("timer", [None, -1, 121, True, "119", float("nan"), float("inf")])
def test_refresh_invalid_initial_timer_sends_no_tap(runner, timer):
    initial = frame(refresh_seconds=timer, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    runner.sleep = lambda duration: pytest.fail("Invalid input must not wait or refresh")
    runner.menu = lambda: pytest.fail("Invalid input must not refresh")
    assert runner.refresh(initial) is initial
    assert runner.last_refresh_acknowledged is False
    assert runner.inputs == [] and runner.refresh_count == 0


@pytest.mark.parametrize("timer", [None, -1, 121, True, "119", float("nan"), float("inf")])
def test_refresh_invalid_result_timer_never_acknowledges_a_draw(runner, timer):
    initial = frame(refresh_seconds=119, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    result = frame(refresh_seconds=timer, sampled_ranks=(70, 71, 72), captured_at=5)
    runner.sleep = lambda duration: None
    runner.menu = lambda: result
    assert runner.refresh(initial) is result
    assert runner.last_refresh_acknowledged is False
    assert len(runner.inputs) == 1 and runner.refresh_count == 1


def test_refresh_ack_identical_reset_lists_are_counted_in_survey(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    def screen(timestamp):
        return frame(rank=100, opponents=candidates, refresh_seconds=119,
                     refresh_target=(1174, 147), captured_at=timestamp)
    screens = iter(screen(timestamp) for timestamp in (5, 10, 15))
    runner.menu = lambda: next(screens)
    runner.sleep = lambda duration: None
    _, ranked = runner.scout(screen(0))
    assert len(ranked) == 3 and runner.refresh_count == 3
    assert runner.refresh_planner.samples == [(70, 71, 72)] * 3
    assert runner.refresh_planner.valid_draws == 3


@pytest.mark.parametrize("ranks", [(), (70, 71), (70, 70, 72)])
def test_incomplete_rank_samples_defer_even_when_a_candidate_is_readable(runner, ranks):
    candidate = Candidate(Opponent("readable", 70, 75))
    partial = frame(rank=100, all_ahead=True, opponents=(candidate,), sampled_ranks=ranks)
    refreshes = []
    runner.refresh = lambda f: refreshes.append(f) or partial
    assert runner.scout(partial)[1] == ()
    assert len(refreshes) == 1
    assert runner.refresh_planner.physical_refreshes == 0
    assert runner.refresh_planner.valid_draws == 0
    assert runner.inputs == []


def test_player_rank_change_discards_completed_survey_candidates(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    runner.refresh = lambda f: frame(rank=101, opponents=candidates)
    assert runner.scout(frame(rank=100, opponents=candidates))[1] == ()
    assert runner.refresh_planner is None


def test_scouting_time_limit_returns_home_with_tickets_intact(runner, monkeypatch):
    from ba_automator.tactical_refresh import RefreshPlanner
    monkeypatch.setattr("ba_automator.tactical_runtime.RefreshPlanner", RefreshPlanner)
    candidates = tuple(observed(str(i), rank=70 + i, level=75) for i in range(3))
    runner.survey_time_available = lambda: False
    runner.refresh = lambda f: pytest.fail("No budget remains for another refresh")
    runner.battle = lambda *a: pytest.fail("An incomplete estimate must not authorize combat")
    assert runner.run_menu(frame(rank=100, tickets=5, opponents=candidates)) == "done"
    saved = state.read_state(runner.config)
    assert saved["last_tickets"] == 5 and saved["attempts"] == {}
    assert saved["pending"] is None
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]


def test_scanning_never_admits_partial_list_behind_player(runner):
    f = frame(rank=100, opponents=tuple(Candidate(Opponent(str(i), rank, 50))
                                       for i, rank in enumerate((90, 95, 105))))
    runner.refresh = lambda previous: f
    assert runner.scout(f)[1] == ()


def test_independent_ahead_ranks_allow_one_fully_read_team(runner):
    candidate = Candidate(Opponent("readable", 90, 80, (70, 75, 80)))
    f = frame(rank=100, all_ahead=True, sampled_ranks=(90, 92, 95), opponents=(candidate,))
    runner.refresh = lambda previous: f
    assert runner.scout(f)[1] == (candidate.choice,)
    f.screen.all_ahead = False
    assert runner.scout(f)[1] == ()


def test_missing_target_lookup_stops_at_refresh_limit(runner):
    candidate = Candidate(Opponent("gone", 50, 80))
    runner.observed["gone"] = candidate
    f = frame(rank=100, opponents=tuple(Candidate(Opponent(str(i), 80 + i, 75))
                                       for i in range(3)))
    runner.refresh_planner = TinyPlanner()
    calls = []
    runner.refresh = lambda old: calls.append(old) or f
    assert runner.locate(f, candidate.choice) == (f, None)
    assert len(calls) == 3


def test_lookup_counts_readable_rank_lists_without_requiring_three_readable_teams(runner):
    wanted = Candidate(Opponent("gone", 50, 80))
    runner.observed["gone"] = wanted
    candidate = Candidate(Opponent("readable", 80, 75))
    f = frame(rank=100, opponents=(candidate,), sampled_ranks=(80, 81, 82))
    runner.refresh_planner = TinyPlanner()
    calls = []
    runner.refresh = lambda previous: calls.append(previous) or f
    assert runner.locate(f, wanted.choice) == (f, None)
    assert len(calls) == 3


@pytest.mark.parametrize("ranks", [(50, 81, 82), (80, 81)])
def test_lookup_defers_when_selected_rank_cannot_be_identified_or_ranks_are_incomplete(runner, ranks):
    wanted = Candidate(Opponent("unverified", 50, 80))
    runner.observed["unverified"] = wanted
    candidate = Candidate(Opponent("readable", 80, 75))
    initial = frame(rank=100, opponents=(candidate,), sampled_ranks=(80, 81, 82))
    refreshed = frame(rank=100, opponents=(candidate,), sampled_ranks=ranks)
    runner.refresh_planner = TinyPlanner()
    calls = []
    runner.refresh = lambda previous: calls.append(previous) or refreshed
    assert runner.locate(initial, wanted.choice) == (refreshed, None)
    assert len(calls) == 1


@pytest.mark.parametrize("tickets,rank", [(1, 100), (0, 100), (5, 1)])
def test_reserve_or_first_place_goes_home_without_scouting(runner, tickets, rank):
    runner.scout = lambda f: pytest.fail("Must not scout")
    assert runner.run_menu(frame(tickets=tickets, rank=rank)) == "done"
    assert len(runner.inputs) == 1 and runner.inputs[0][0] == (1237, 23)


@pytest.mark.parametrize("skip", [True, False])
def test_both_skip_modes_wait_for_result_before_recording(runner, skip):
    runner.config.tactical_battles_skip_battles = skip
    candidate = Candidate(Opponent("enemy", 80, 79))
    detail = frame("opponent", opponents=(candidate,), tickets=5, after_tickets=4,
                   target=(640, 570))
    formation = frame("formation", formation_seconds=120, skip_selected=not skip, target=(1170, 666))
    changed = frame("formation", formation_seconds=120, skip_selected=skip, target=(1170, 666))
    result = frame("result", won=False, target=(1100, 650))
    returned = frame(tickets=4, rank=100)
    observations = iter((detail, formation, changed, result))
    waits = []
    def wait(kinds, **kwargs):
        waits.append((kinds, kwargs))
        if kinds == "result":
            pending = state.read_state(runner.config)["pending"]
            assert pending["tickets_before"] == 5
            assert pending["opponent_id"] == "enemy"
        return next(observations)
    runner.wait = wait
    runner.menu = lambda: returned
    runner.fill_attack_formation = lambda f: f
    assert runner.battle(frame(tickets=5, rank=100), candidate) == (returned, False)
    saved = state.read_state(runner.config)
    assert saved["pending"] is None and saved["retry_opponent_id"] is None
    assert saved["attempts"] == {"enemy": 1}
    assert ("result", {"timeout": 600}) in waits
    assert sum(target == (1170, 666) for target, _ in runner.inputs) == 1


def test_unrecognized_result_leaves_pending_and_never_reenters(runner):
    candidate = Candidate(Opponent("enemy", 80, 79))
    detail = frame("opponent", opponents=(candidate,), tickets=5, after_tickets=4,
                   target=(640, 570))
    formation = frame("formation", formation_seconds=120, skip_selected=True, target=(1170, 666))
    observations = iter((detail, formation))
    def wait(kinds, **kwargs):
        if kinds == "result":
            raise RuntimeError("result timed out")
        return next(observations)
    runner.wait = wait
    runner.fill_attack_formation = lambda f: f
    with pytest.raises(RuntimeError, match="result timed out"):
        runner.battle(frame(tickets=5, rank=100), candidate)
    assert state.read_state(runner.config)["pending"]["opponent_id"] == "enemy"
    assert sum(target == (1170, 666) for target, _ in runner.inputs) == 1


def test_changed_detail_ticket_projection_never_opens_formation(runner):
    candidate = Candidate(Opponent("enemy", 80, 79))
    runner.wait = lambda *a, **k: frame("opponent", opponents=(candidate,),
                                       tickets=5, after_tickets=3)
    with pytest.raises(RuntimeError, match="projected ticket"):
        runner.battle(frame(tickets=5, rank=100), candidate)
    assert len(runner.inputs) == 1
    assert state.read_state(runner.config)["pending"] is None



@pytest.mark.parametrize("player_rank,opponent_rank", [(101, 80), (99, 80), (100, 100), (100, 101), (None, 80)])
def test_changed_detail_rank_never_opens_formation(runner, player_rank, opponent_rank):
    selected = Candidate(Opponent("enemy", 80, 79, (79, 75, 70)))
    fresh = Candidate(Opponent("enemy", opponent_rank, 79, (79, 75, 70)))
    runner.wait = lambda *a, **k: frame(
        "opponent", rank=player_rank, opponents=(fresh,), tickets=5, after_tickets=4,
        target=(640, 570))
    with pytest.raises(RuntimeError, match="Opponent or projected ticket count changed"):
        runner.battle(frame(tickets=5, rank=100), selected)
    assert len(runner.inputs) == 1
    assert runner.inputs[0][0] == selected.target
    assert state.read_state(runner.config)["pending"] is None


def test_changed_detail_team_levels_never_opens_formation(runner):
    selected = Candidate(Opponent("enemy", 80, 79, (79, 75, 70)))
    fresh = Candidate(Opponent("enemy", 80, 79, (79, 75, 78)))
    runner.wait = lambda *a, **k: frame(
        "opponent", rank=100, opponents=(fresh,), tickets=5, after_tickets=4,
        target=(640, 570))
    with pytest.raises(RuntimeError, match="Opponent or projected ticket count changed"):
        runner.battle(frame(tickets=5, rank=100), selected)
    assert len(runner.inputs) == 1
    assert state.read_state(runner.config)["pending"] is None


def observed(identity, *, rank=80, level=79, name="Anonymous", pixel_shift=0):
    signature = bytes(min(255, (i * 7) % 256 + pixel_shift) for i in range(576)).hex()
    return ObservedOpponent(Opponent(identity, rank, level), name, (640, 230), signature)


def seed_history(runner, candidate, attempts=1, won=False):
    now = runner.wall_clock()
    day = "2026-09-26"
    state.save_identities(runner.config, day, {candidate.choice.opponent_id: asdict(candidate)}, now=now)
    intent = state.begin_battle(runner.config, day, candidate.choice.opponent_id,
                                3, 100, now=now)
    state.complete_battle(runner.config, intent, tickets_after=2,
                          won=won, rank_after=80 if won else 100, now=now)
    if attempts == 2:
        # Migrate history from the older two-attempt policy without trying to
        # spend a second ticket through the new one-attempt entry guard.
        legacy = state.read_state(runner.config)
        legacy["attempts"][candidate.choice.opponent_id] = 2
        state.write_state(runner.config, legacy)


@pytest.mark.parametrize("attempts,won", [(1, False), (1, True), (2, False)])
@pytest.mark.parametrize("current_level", [79, 80])
def test_restarted_survey_never_rematches_same_day_identity(
        runner, monkeypatch, attempts, won, current_level):
    previous = observed("canonical-id")
    seed_history(runner, previous, attempts, won)
    monkeypatch.setattr("ba_automator.tactical_runtime.same_opponent", same_opponent)
    monkeypatch.setattr("ba_automator.tactical_runtime.same_opponent_identity", same_opponent_identity)
    changed_hash = observed("different-pixel-hash", rank=75, level=current_level,
                            name="anonymous", pixel_shift=1)
    easier = observed("easier", rank=70, level=50, name="Another player")
    third = observed("third", rank=60, level=90, name="Third player")
    current = frame(tickets=2, rank=100, cooldown=0,
                    opponents=(changed_hash, easier, third))
    runner.refresh = lambda f: current
    selected = []

    def battle(f, candidate):
        selected.append(candidate.choice.opponent_id)
        saved = state.read_state(runner.config)
        assert saved["attempts"] == {"canonical-id": attempts}
        assert "different-pixel-hash" not in saved["identities"]
        intent = state.begin_battle(runner.config, "2026-09-26", candidate.choice.opponent_id,
                                    2, 100, now=runner.wall_clock())
        state.complete_battle(runner.config, intent, tickets_after=1, won=False,
                              rank_after=100, now=runner.wall_clock())
        return frame(tickets=1, rank=100), False

    runner.battle = battle
    assert runner.run_menu(current) == "done"
    assert selected == ["easier"]
    assert state.read_state(runner.config)["attempts"]["canonical-id"] == attempts
    assert runner.observed["canonical-id"].choice.rank == 75
    assert runner.observed["canonical-id"].choice.level == current_level
    assert state.read_state(runner.config)["identities"]["canonical-id"]["choice"]["level"] == current_level


def test_absent_fought_opponent_does_not_trigger_lookup_or_erase_history(runner):
    seed_history(runner, observed("gone"))
    candidates = tuple(observed(str(n), rank=50 + n, level=50 + n) for n in range(3))
    current = frame(tickets=2, rank=100, cooldown=0, opponents=candidates)
    runner.refresh_limit = 2
    refreshes = []
    runner.refresh = lambda f: refreshes.append(f) or current

    def battle(f, candidate):
        saved = state.read_state(runner.config)
        assert candidate.choice.opponent_id == "0"
        assert saved["retry_opponent_id"] is None
        assert saved["attempts"] == {"gone": 1}
        return frame(tickets=1, rank=100), False

    runner.battle = battle
    assert runner.run_menu(current) == "done"
    assert len(refreshes) == 3
    assert state.read_state(runner.config)["attempts"] == {"gone": 1}


@pytest.mark.parametrize("won", [True, False])
def test_saved_result_recovers_on_menu_without_entering_again(runner, won):
    candidate = observed("enemy")
    now = runner.wall_clock()
    state.save_identities(runner.config, "2026-09-26", {"enemy": asdict(candidate)}, now=now)
    intent = state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100, now=now)
    state.record_outcome(runner.config, intent, won=won, evidence="result.png", now=now)
    runner.reserve = 4
    runner.scout = lambda f: pytest.fail("Recovered battle already reached the reserve")
    assert runner.run_menu(frame(tickets=4, rank=80 if won else 100)) == "done"
    saved = state.read_state(runner.config)
    assert saved["pending"] is None and saved["attempts"] == {"enemy": 1}
    assert saved["retry_opponent_id"] is None
    assert runner.observed["enemy"] == candidate
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]
    assert runner.run_menu(frame(tickets=4, rank=80 if won else 100)) == "done"
    assert state.read_state(runner.config)["attempts"] == {"enemy": 1}
    assert len(runner.important) == 1
    args, details = runner.important[0]
    assert args[1] == "tactical_battle_recovered"
    assert details["won"] is won and details["evidence"] == "result.png"
    assert details["tickets_before"] == 5 and details["tickets_after"] == 4


@pytest.mark.parametrize("reason", ["unproven", "wrong_count", "new_day"])
def test_ambiguous_pending_never_scouts_or_sends_input(runner, reason):
    now = runner.wall_clock()
    intent = state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100, now=now)
    if reason != "unproven":
        state.record_outcome(runner.config, intent, won=True, evidence="result.png", now=now)
    if reason == "new_day":
        runner.wall_clock = lambda: now + timedelta(days=1)
    runner.scout = lambda f: pytest.fail("Pending outcome must block scouting")
    with pytest.raises(state.TacticalStateError):
        runner.run_menu(frame(tickets=5 if reason == "wrong_count" else 4, rank=80))
    saved = state.read_state(runner.config)
    assert saved["pending"]["id"] == intent and saved["attempts"] == {}
    assert runner.inputs == [] and runner.important == []


def test_new_daily_run_clears_resolved_retry_and_identity_history(runner):
    seed_history(runner, observed("yesterday"))
    now = runner.wall_clock()
    runner.wall_clock = lambda: now + timedelta(days=1)
    runner.reserve = 5
    assert runner.run_menu(frame(tickets=5, rank=100)) == "done"
    saved = state.read_state(runner.config)
    assert saved["day_key"] == "2026-09-27"
    assert saved["attempts"] == {} and saved["identities"] == {}
    assert saved["retry_opponent_id"] is None and runner.observed == {}


def battle_frames(candidate, *, seconds=120):
    detail = frame("opponent", opponents=(candidate,), tickets=5, after_tickets=4,
                   target=(640, 570))
    formation = frame("formation", formation_seconds=seconds, skip_selected=True,
                      target=(1170, 666))
    return detail, formation


def test_outcome_is_persisted_before_result_dismissal_and_recovers_after_crash(runner):
    candidate = observed("enemy")
    detail, formation = battle_frames(candidate)
    result = frame("result", won=True, target=(1100, 650))
    observations = iter((detail, formation, result))
    runner.wait = lambda *a, **k: next(observations)
    runner.fill_attack_formation = lambda f: f
    original_tap = runner.tap

    def interrupted_tap(f, target, text):
        if f.screen.kind == "result":
            saved = state.read_state(runner.config)
            assert saved["pending"]["outcome"]["won"] is True
            assert saved["attempts"] == {}
            raise RuntimeError("process interrupted before result dismissal")
        original_tap(f, target, text)

    runner.tap = interrupted_tap
    with pytest.raises(RuntimeError, match="process interrupted"):
        runner.battle(frame(tickets=5, rank=100), candidate)
    runner.tap = original_tap
    runner.reserve = 4
    assert runner.run_menu(frame(tickets=4, rank=80)) == "done"
    assert state.read_state(runner.config)["attempts"] == {"enemy": 1}
    assert sum(target == (1170, 666) for target, _ in runner.inputs) == 1


def test_reset_during_scouting_prevents_opponent_detail_or_entry(runner):
    before = datetime(2026, 9, 26, 18, 59, 59, tzinfo=timezone.utc)
    after = before + timedelta(seconds=2)
    runner.wall_clock = lambda: after
    runner.run_day = "2026-09-25"
    with pytest.raises(RuntimeError, match="day changed during scouting"):
        runner.battle(frame(tickets=5, rank=100), observed("enemy"))
    assert runner.inputs == [] and state.read_state(runner.config)["pending"] is None


@pytest.mark.parametrize("reset_during", ["fill", "timer_wait"])
def test_reset_during_formation_prevents_mobilization(runner, reset_during):
    now = [datetime(2026, 9, 26, 18, 59, 59, tzinfo=timezone.utc)]
    runner.wall_clock = lambda: now[0]
    runner.run_day = "2026-09-25"
    candidate = observed("enemy")
    detail, formation = battle_frames(candidate, seconds=None if reset_during == "timer_wait" else 120)
    observations = iter((detail, formation))

    def wait(kinds, **kwargs):
        if "predicate" in kwargs:
            now[0] += timedelta(seconds=2)
            return battle_frames(candidate)[1]
        if kinds == "result":
            pytest.fail("A stale formation must never be mobilized after reset")
        return next(observations)

    def fill(f):
        if reset_during == "fill":
            now[0] += timedelta(seconds=2)
        return f

    runner.wait, runner.fill_attack_formation = wait, fill
    with pytest.raises(RuntimeError, match="day changed during formation"):
        runner.battle(frame(tickets=5, rank=100), candidate)
    assert state.read_state(runner.config)["pending"] is None
    assert all(target != (1170, 666) for target, _ in runner.inputs)


@pytest.mark.parametrize("elapsed,actions,refreshes,allowed", [
    (299, 249, 99, True), (300, 0, 0, False),
    (0, 250, 0, False), (0, 0, 100, False),
])
def test_survey_chunk_budget_releases_queue_and_shares_refresh_count(
        runner, elapsed, actions, refreshes, allowed):
    runner.clock = lambda: elapsed
    runner.actions, runner.refresh_count = actions, refreshes
    assert runner.survey_time_available() is allowed


def test_refresh_cap_counts_actual_inputs_including_unacknowledged_taps(runner):
    initial = frame(refresh_seconds=115, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    ignored = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72), captured_at=5)
    runner.refresh_count = 999
    runner.sleep = lambda duration: None
    runner.menu = lambda: ignored
    assert runner.refresh(initial) is ignored
    assert runner.refresh_count == 1000 and not runner.last_refresh_acknowledged
    assert runner.refresh(initial) is initial
    assert runner.refresh_count == 1000 and len(runner.inputs) == 1
    assert not runner.last_refresh_acknowledged


def test_scout_and_lookup_share_the_final_available_chunk_refresh(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    initial = frame(rank=100, opponents=candidates, refresh_seconds=115,
                    refresh_target=(1174, 147))
    refreshed = frame(rank=100, opponents=candidates, refresh_seconds=119, captured_at=5)
    runner.refresh_count = 99
    runner.sleep = lambda duration: None
    runner.menu = lambda: refreshed
    assert runner.scout(initial) == (refreshed, ())
    assert runner.refresh_count == 100
    assert runner.refresh_planner.valid_draws == 1
    wanted = Candidate(Opponent("missing", 50, 75))
    runner.observed["missing"] = wanted
    assert runner.locate(refreshed, wanted.choice) == (refreshed, None)
    assert runner.refresh_count == 100 and len(runner.inputs) == 1


@pytest.mark.parametrize("rank", [99, 101])
def test_lookup_rejects_a_rank_change_since_the_completed_survey(runner, rank):
    wanted = Candidate(Opponent("enemy", 50, 75))
    runner.observed["enemy"] = wanted
    runner.refresh_planner = TinyPlanner()
    current = frame(rank=rank, opponents=(wanted,))
    runner.refresh = lambda f: pytest.fail("A changed player rank invalidates the survey")
    assert runner.locate(current, wanted.choice) == (current, None)
    assert runner.refresh_planner is None and runner.inputs == []


def test_identity_capacity_defers_survey_without_discarding_fought_identity(runner, monkeypatch):
    # A small cap exercises the same persistence boundary without a huge fixture.
    monkeypatch.setattr(state, "MAX_IDENTITIES", 3)
    remembered = tuple(observed(str(i), rank=70 + i, level=75) for i in range(3))
    seed_history(runner, remembered[0])
    runner.observed = {p.choice.opponent_id: p for p in remembered}
    current = frame(rank=100, tickets=2, opponents=(observed("new", rank=60),))
    runner.refresh = lambda f: pytest.fail("A full identity store must release the queue")
    assert runner.run_menu(current) == "done"
    saved = state.read_state(runner.config)
    assert runner.identity_capacity_reached
    assert len(saved["identities"]) == 3 and "new" not in saved["identities"]
    assert saved["attempts"] == {"0": 1} and saved["pending"] is None
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]


@pytest.mark.parametrize("elapsed,actions,allowed", [
    (1079, 1019, True), (1080, 0, False), (0, 1020, False),
])
def test_battle_budget_reserves_twelve_minutes_and_eighty_inputs(
        runner, elapsed, actions, allowed):
    runner.clock = lambda: elapsed
    runner.actions = actions
    assert runner.battle_time_available() is allowed


@pytest.mark.parametrize("resource", ["time", "inputs"])
def test_insufficient_battle_budget_never_opens_opponent_detail(runner, resource):
    runner.clock = lambda: 1080 if resource == "time" else 0
    runner.actions = 1020 if resource == "inputs" else 0
    current = frame(rank=100, tickets=5)
    runner.wait = lambda *a, **k: pytest.fail("Do not navigate into an unaffordable battle")
    assert runner.battle(current, observed("enemy")) == (current, None)
    assert runner.inputs == [] and state.read_state(runner.config)["pending"] is None


@pytest.mark.parametrize("resource", ["time", "inputs"])
def test_budget_exhausted_during_formation_backs_out_without_reserving_ticket(runner, resource):
    candidate = observed("enemy")
    detail, formation = battle_frames(candidate)
    observations = iter((detail, formation))
    runner.wait = lambda *a, **k: next(observations)
    returned = frame(rank=100, tickets=5)
    runner.menu = lambda: returned

    def fill(f):
        if resource == "time":
            runner.clock = lambda: 1080
        else:
            runner.actions = 1020
        return f

    runner.fill_attack_formation = fill
    assert runner.battle(frame(tickets=5, rank=100), candidate) == (returned, None)
    assert runner.inputs[-1][0] == (54, 33)
    assert all(target != (1170, 666) for target, _ in runner.inputs)
    saved = state.read_state(runner.config)
    assert saved["pending"] is None and saved["attempts"] == {}


def test_survey_finishing_too_late_releases_queue_without_starting_battle(runner):
    candidate = observed("enemy")
    current = frame(rank=100, tickets=5, cooldown=0, opponents=(candidate,))
    runner.observed["enemy"] = candidate

    def scout(f):
        runner.clock = lambda: 1080
        return f, (candidate.choice,)

    runner.scout = scout
    runner.locate = lambda f, choice: (f, candidate)
    runner.battle = lambda *a: pytest.fail("No time remains to finish battle safely")
    assert runner.run_menu(current) == "done"
    saved = state.read_state(runner.config)
    assert saved["last_tickets"] == 5 and saved["pending"] is None
    assert saved["attempts"] == {}
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]


def test_post_battle_tip_is_dismissed_before_ticket_reconciliation(runner):
    tip = frame("battle_tip", target=(643, 554))
    menu = frame(tickets=4, rank=100)
    observations = iter((tip, menu))
    runner.wait = lambda kinds: next(observations)
    assert runner.menu() is menu
    assert runner.inputs == [((643, 554), "Dismiss Tactical Challenge notice")]


def test_repeating_post_battle_notices_are_bounded(runner):
    tip = frame("battle_tip", target=(643, 554))
    runner.wait = lambda kinds: tip
    with pytest.raises(RuntimeError, match="Repeated Tactical Challenge notices"):
        runner.menu()
    assert len(runner.inputs) == 3


def test_account_level_change_in_detail_still_blocks_entry(runner, monkeypatch):
    # History aliases ignore account level; a selected battle must still match
    # the exact level that was scored by the completed survey.
    monkeypatch.setattr("ba_automator.tactical_runtime.same_opponent", same_opponent)
    selected = observed("enemy", level=79)
    changed = observed("new-hash", level=80, pixel_shift=1)
    assert same_opponent_identity(selected, changed)
    runner.wait = lambda *a, **k: frame(
        "opponent", rank=100, opponents=(changed,), tickets=5, after_tickets=4,
        target=(640, 570))
    with pytest.raises(RuntimeError, match="Opponent or projected ticket count changed"):
        runner.battle(frame(tickets=5, rank=100), selected)
    assert runner.inputs == [(selected.target, "Inspect selected Tactical Challenge opponent")]
    assert state.read_state(runner.config)["pending"] is None
