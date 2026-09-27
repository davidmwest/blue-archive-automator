"""Serial battle flow preserves tickets across scouting, result waits and failures."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ba_automator import tactical_state as state
from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_runtime import TacticalBattleRunner
from ba_automator.tactical_search import SearchPolicy, SearchState
from ba_automator.runtime import Capture
from ba_automator.shop_runtime import ShopFrame, ShopRunner
from ba_automator.tactical_vision import ObservedOpponent, same_opponent, same_opponent_identity


@dataclass(frozen=True)
class Candidate:
    choice: Opponent
    target: tuple = (640, 230)
    name: str = "Anonymous"
    signature: str = bytes((i * 7) % 256 for i in range(576)).hex()


def frame(kind="tactical", *, captured_at=0, **values):
    if kind == "opponent":
        values.setdefault("rank", 100)
    if kind == "tactical":
        values.setdefault("tickets", 5)
    if kind == "tactical" and "opponents" in values:
        values.setdefault("sampled_ranks", tuple(p.choice.rank for p in values["opponents"]))
    return NS(screen=NS(kind=kind, **values),
              capture=NS(png=b"image", captured_at=captured_at))


@pytest.mark.parametrize("minutes", [1, 5, 10, 30, 60])
def test_runner_converts_configured_minutes_into_search_policy(monkeypatch, minutes):
    monkeypatch.setattr("ba_automator.tactical_runtime.ShopRunner.__init__", lambda *a, **k: None)
    config = NS(tactical_battles_preserve_tickets=1,
                tactical_battles_search_minutes=minutes)
    runner = TacticalBattleRunner(config, object(), object())
    assert runner.search_policy.time_budget_seconds == minutes * 60
    assert runner.search_state is None and runner.search_clock is None


@pytest.fixture
def runner(tmp_path, monkeypatch):
    r = object.__new__(TacticalBattleRunner)
    r.config = NS(serial="test", package="test", state_dir=tmp_path,
                  tactical_battles_skip_battles=True,
                  tactical_battles_search_minutes=1 / 6,
                  tactical_battles_preserve_tickets=1,
                  tactical_battles_enabled_in_daily=False)
    r.reserve, r.observed = 1, {}
    r.search_policy = SearchPolicy(time_budget_seconds=10)
    r.search_state = r.search_clock = None
    r.last_refresh_acknowledged = True
    r.last_refresh_sent = False
    r.run_dir = tmp_path
    r.started = r.actions = r.refresh_count = 0
    r.now = [0.0]
    r.clock = lambda: r.now[0]
    r.sleep = lambda seconds: r.now.__setitem__(0, r.now[0] + seconds)
    r.identity_capacity_reached = False
    r.survey_own_rank = 100
    r.survey_resume = None
    r.defer_survey = False
    r.survey_created_at = None
    r.survey_expired = False
    r.survey_failure_reason = None
    r.wall_clock = lambda: datetime(2026, 9, 26, 21, tzinfo=timezone.utc)
    r.phase = lambda text: None
    r.events = []
    r.journal = NS(record=lambda *a, **k: r.events.append((a, k)),
                   save_image=lambda *a: None)
    r.fail = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    r.inputs = []
    r.tap = lambda f, target, detail: r.inputs.append((target, detail))
    r.home = lambda: None
    r.finish = lambda status="success": "done"
    for matcher in ("same_opponent", "same_opponent_identity"):
        monkeypatch.setattr(f"ba_automator.tactical_runtime.{matcher}",
                            lambda a, b: a.choice.opponent_id == b.choice.opponent_id)
    r.important = []
    monkeypatch.setattr("ba_automator.tactical_runtime.record_action",
                        lambda *a, **k: r.important.append((a, k)))
    state.state_for_day(r.config, "2026-09-26", now=r.wall_clock())
    return r


def timed_refresh(runner, current, *, seconds=1):
    calls = []
    def refresh(previous):
        calls.append(previous)
        runner.now[0] += seconds
        runner.refresh_count += 1
        runner.last_refresh_acknowledged = True
        return current
    runner.refresh = refresh
    return calls


@pytest.mark.parametrize("outcome", ["tactical", "timeout"])
def test_campaign_transition_does_not_repeat_tap_while_menu_loads(runner, outcome):
    now = [0.0]
    runner.clock = lambda: now[0]
    runner.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    campaign = ShopFrame(Capture(b"image", 0, "test"), NS(kind="campaign"))
    transitions = []
    runner.navigate = lambda *args: transitions.append(args) or campaign
    runner.wait = ShopRunner.wait.__get__(runner)
    runner.run_menu = lambda observed: observed.screen.kind

    def capture():
        # The live trace still showed Campaign four seconds after the first
        # input. A following capture finally showed the destination; another
        # source-coordinate tap would have selected the third opponent.
        now[0] += 1
        kind = "tactical" if outcome == "tactical" and now[0] >= 6 else "campaign"
        return ShopFrame(Capture(b"image", now[0], "test"), NS(kind=kind))

    runner.capture = capture
    if outcome == "timeout":
        with pytest.raises(RuntimeError, match="did not reach tactical"):
            runner.run()
        assert 40 <= now[0] < 42
    else:
        assert runner.run() == "tactical"
        assert now[0] >= 6
    assert transitions == [("home", "campaign", (1200, 641))]
    assert runner.inputs == [((868, 581), "Open tactical")]


def test_timed_search_selects_only_a_qualifying_current_opponent(runner):
    calls = []
    def menu(index):
        # The benchmark contains stronger teams. At the first decision time,
        # select the weaker current team without reacquiring a historical one.
        return frame(rank=1000, opponents=tuple(
            Candidate(Opponent(str(index * 3 + n), 100 + n, 80 - index))
            for n in range(3)))
    def refresh(previous):
        calls.append(previous)
        runner.now[0] += 1
        return menu(len(calls))
    runner.refresh = refresh
    current, choices = runner.scout(menu(0))
    assert len(calls) == 4 and len(choices) == 1
    assert choices[0] in tuple(p.choice for p in current.screen.opponents)
    assert choices[0].level == 76
    assert runner.search_state.elapsed_seconds == 4
    assert runner.search_state.refreshes == 4
    assert runner.search_state.benchmark_observations == 4




def test_initial_menu_is_not_counted_as_a_refresh_draw(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    current = frame(rank=100, opponents=candidates)
    timed_refresh(runner, current)
    runner.scout(current)
    assert runner.search_state.refreshes == 4
    assert runner.search_state.observations == 5
    assert runner.search_state.initial_observed



def test_configured_time_budget_reaches_selection_policy(runner):
    runner.search_policy = SearchPolicy(time_budget_seconds=20)
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    current = frame(rank=100, opponents=candidates)
    timed_refresh(runner, current)
    assert runner.scout(current)[1]
    assert runner.search_state.policy.time_budget_seconds == 20
    assert runner.search_state.elapsed_seconds == 8



def test_unsent_refresh_defers_without_counting_a_draw_or_fighting(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    f = frame(rank=100, opponents=candidates)
    runner.last_refresh_acknowledged = False
    runner.refresh = lambda previous: f
    assert runner.scout(f)[1] == ()
    assert runner.search_state.refreshes == 0
    assert runner.defer_survey is True
    assert runner.inputs == []



@pytest.mark.parametrize("before,after,elapsed,acknowledged", [
    (119, 114, 5, False),   # Ignored input follows the normal countdown.
    (119, 119, 5, True),    # Reset can leave the displayed integer unchanged.
    (120, 119, 5, True),
    (121, 119, 5, True),
    (119, 121, 5, True),
    (119, 118, 5, True),
    (115, 119, 1.3, True),
    (119, 119, 2.99, False),
    (119, 119, 3, True),
    (119, 115, 10, True),   # Slow OCR may observe a timer well after reset.
    (119, 112, 11.358, True),  # Live failure: valid reset was rejected at 1:52.
    (115, 119, 0, False),
    (115, 119, -1, False),
    (115, 119, 20, True),
    (111, 118, 40, True),   # A delayed network response can outlast OCR alone.
    (111, 71, 40, False),   # Ordinary countdown never acknowledges a refresh.
    (30, 119, 35, False),   # A possibly expired original list is ambiguous.
    (5, 119, 4, True),
    (5, 119, 10, False),    # The same expiry check applies to fast responses.
    (10, 119, 10, False),
    (115, 119, 60, False),
    (115, 119, float("inf"), False),
    (115, 119, float("nan"), False),
])
def test_refresh_ack_uses_capture_elapsed_time_for_identical_lists(
        runner, before, after, elapsed, acknowledged):
    # Exercise the acknowledgement calculation independently of the countdown
    # preflight. Dedicated tests below verify its headroom requirement.
    runner.prepare_refresh = lambda current: current
    if before > 114:
        # The real preflight normally observes a lower timer; use the lower
        # starting value while retaining each case's exact reset evidence.
        after -= before - 114
        before = 114
    ranks = (70, 71, 72)
    initial = frame(refresh_seconds=before, sampled_ranks=ranks,
                    refresh_target=(1174, 147), captured_at=10)
    result = frame(refresh_seconds=after, sampled_ranks=ranks,
                   captured_at=10 + elapsed)
    sleeps = []
    runner.sleep = sleeps.append
    runner.menu = lambda: result
    if acknowledged:
        assert runner.refresh(initial) is result
    else:
        with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
            runner.refresh(initial)
    assert runner.last_refresh_acknowledged is acknowledged
    assert runner.refresh_count == 1 and len(runner.inputs) == 1
    assert sleeps == [1.3]  # No artificial wait for the clock to tick down.
    event = next(fields for names, fields in runner.events if names == ("opponents_refreshed",))
    assert event["changed"] is False and event["acknowledged"] is acknowledged


@pytest.mark.parametrize("settle_at", [8, 28, None])
def test_refresh_waits_read_only_for_loading_overlay(runner, settle_at):
    runner.wait = ShopRunner.wait.__get__(runner)
    initial = frame(refresh_seconds=111, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147), rank=100)

    def capture():
        runner.now[0] += 2
        settled = settle_at is not None and runner.now[0] >= settle_at
        screen = NS(kind="tactical" if settled else "unknown", rank=100,
                    tickets=5, opponents=(), refresh_seconds=119,
                    sampled_ranks=(70, 71, 72))
        return ShopFrame(Capture(b"image", runner.now[0], "test"), screen)

    runner.capture = capture
    if settle_at is None:
        with pytest.raises(RuntimeError, match="did not reach"):
            runner.refresh(initial)
        assert runner.now[0] >= 40
        assert not runner.last_refresh_acknowledged
    else:
        result = runner.refresh(initial)
        assert result.screen.kind == "tactical"
        assert runner.now[0] >= settle_at
        assert runner.last_refresh_acknowledged
    assert runner.inputs == [((1174, 147), "Refresh Tactical Challenge opponents")]
    assert runner.refresh_count == 1


@pytest.mark.parametrize("timer", [None, -1, 122, True, "119", float("nan"), float("inf")])
def test_refresh_invalid_initial_timer_sends_no_tap(runner, timer):
    initial = frame(refresh_seconds=timer, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    runner.sleep = lambda duration: pytest.fail("Invalid input must not wait or refresh")
    runner.menu = lambda: pytest.fail("Invalid input must not refresh")
    assert runner.refresh(initial) is initial
    assert runner.last_refresh_acknowledged is False
    assert runner.last_refresh_sent is False
    assert runner.inputs == [] and runner.refresh_count == 0


@pytest.mark.parametrize("seconds", [115, 119, 121])
def test_refresh_waits_for_countdown_headroom_and_uses_fresh_preflight_capture(runner, seconds):
    initial = frame(refresh_seconds=seconds, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    ready = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                  refresh_target=(1174, 147), captured_at=7)
    result = frame(refresh_seconds=119, sampled_ranks=(70, 71, 72), captured_at=12)
    screens = iter((ready, result))
    sleeps = []
    runner.sleep = sleeps.append
    runner.menu = lambda: next(screens)
    assert runner.refresh(initial) is result
    assert runner.last_refresh_acknowledged and runner.last_refresh_sent
    assert sleeps == [seconds - 114, 1.3]
    event = next(fields for names, fields in runner.events if names == ("opponents_refreshed",))
    assert event["timer_before"] == 114 and event["capture_elapsed"] == 5
    assert runner.refresh_count == 1 and len(runner.inputs) == 1


@pytest.mark.parametrize("timer,ranks,expected_sleeps", [
    (None, (70, 71, 72), [5]),
    (117, (70, 71, 72), [5, 3]),
    (114, (70, 71), [5]),
])
def test_refresh_unready_preflight_defers_without_sending_input(runner, timer, ranks, expected_sleeps):
    initial = frame(refresh_seconds=119, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    unready = frame(refresh_seconds=timer, sampled_ranks=ranks, captured_at=5)
    sleeps = []
    runner.sleep = sleeps.append
    runner.menu = lambda: unready
    assert runner.refresh(initial) is unready
    assert not runner.last_refresh_sent and not runner.last_refresh_acknowledged
    assert runner.inputs == [] and runner.refresh_count == 0
    assert sleeps == expected_sleeps


@pytest.mark.parametrize("timer", [None, -1, 122, True, "119", float("nan"), float("inf")])
def test_refresh_invalid_result_timer_never_acknowledges_a_draw(runner, timer):
    initial = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    result = frame(refresh_seconds=timer, sampled_ranks=(70, 71, 72), captured_at=5)
    sleeps = []
    runner.sleep = sleeps.append
    runner.menu = lambda: result
    with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
        runner.refresh(initial)
    assert runner.last_refresh_acknowledged is False
    assert runner.last_refresh_sent is True
    assert len(runner.inputs) == 1 and runner.refresh_count == 1
    assert sleeps == [1.3, .7, .7]


def test_changed_complete_list_cannot_bypass_independent_timer_acknowledgement(runner):
    initial = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    result = frame(refresh_seconds=None, sampled_ranks=(73, 74, 75), captured_at=5.264)
    runner.sleep = lambda duration: None
    runner.menu = lambda: result
    with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
        runner.refresh(initial)
    assert runner.last_refresh_acknowledged is False
    event = next(fields for names, fields in runner.events if names == ("opponents_refreshed",))
    assert event["changed"] is True and event["acknowledged"] is False
    assert runner.last_refresh_sent and len(runner.inputs) == 1


def test_unsent_refresh_keeps_checkpoint_and_schedules_continuation(runner):
    from ba_automator import tactical_survey as survey
    runner.config.tactical_battles_enabled_in_daily = True
    candidates = tuple(observed(str(i), rank=70 + i) for i in range(3))
    f = frame(rank=100, tickets=5, opponents=candidates)
    def refresh(previous):
        runner.last_refresh_sent = runner.refresh_count == 0
        runner.last_refresh_acknowledged = runner.last_refresh_sent
        runner.refresh_count += runner.last_refresh_sent
        runner.now[0] += 1
        return f
    runner.refresh = refresh
    statuses = []
    runner.finish = lambda status="success": statuses.append(status)
    runner.run_menu(f)
    saved = survey.load_survey(runner.config, day_key="2026-09-26", own_rank=100,
                              time_budget_seconds=10, now=runner.wall_clock().timestamp())
    assert saved["search"].refreshes == 1
    assert saved["search"].elapsed_seconds == 2
    assert statuses == ["deferred"]
    assert survey.continuation_due(runner.config, now=runner.wall_clock().timestamp() + 61)
    assert state.read_state(runner.config)["pending"] is None



def test_refresh_rereads_incomplete_rank_labels_without_another_tap(runner):
    initial = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    moving = frame(refresh_seconds=121, sampled_ranks=(74, 75), captured_at=5)
    settled = frame(refresh_seconds=119, sampled_ranks=(74, 75, 76), captured_at=7)
    screens = iter((moving, settled))
    runner.sleep = lambda duration: None
    runner.menu = lambda: next(screens)
    assert runner.refresh(initial) is settled
    assert runner.last_refresh_acknowledged
    assert runner.refresh_count == 1 and len(runner.inputs) == 1


def test_refresh_rereads_unreadable_timer_on_same_draw_without_another_tap(runner):
    initial = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    unreadable = frame(refresh_seconds=None, sampled_ranks=(70, 71, 72), captured_at=5)
    settled = frame(refresh_seconds=117, sampled_ranks=(70, 71, 72), captured_at=7)
    screens = iter((unreadable, settled))
    sleeps = []
    runner.sleep = sleeps.append
    runner.menu = lambda: next(screens)
    assert runner.refresh(initial) is settled
    assert runner.last_refresh_acknowledged
    assert sleeps == [1.3, .7]
    assert runner.refresh_count == 1 and len(runner.inputs) == 1


def test_refresh_ack_identical_reset_lists_are_counted_in_search(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    def screen(timestamp):
        return frame(rank=100, opponents=candidates, refresh_seconds=114,
                     refresh_target=(1174, 147), captured_at=timestamp)
    def menu():
        runner.now[0] += 3.7
        return screen(runner.now[0])
    runner.menu = menu
    runner.prepare_refresh = lambda current: current
    runner.search_policy = SearchPolicy(time_budget_seconds=50)
    _, choices = runner.scout(screen(0))
    assert len(choices) == 1 and runner.refresh_count == 4
    assert runner.search_state.refreshes == 4
    assert runner.search_state.observations == 5



@pytest.mark.parametrize("ranks", [(), (70, 71), (70, 70, 72)])
def test_incomplete_rank_samples_defer_even_when_a_candidate_is_readable(runner, ranks):
    candidate = Candidate(Opponent("readable", 70, 75))
    partial = frame(rank=100, all_ahead=True, opponents=(candidate,), sampled_ranks=ranks)
    refreshes = []
    runner.refresh = lambda f: refreshes.append(f) or partial
    with pytest.raises(RuntimeError, match="Incomplete opponent rank labels"):
        runner.scout(partial)
    assert len(refreshes) == 1
    assert runner.search_state.refreshes == 0
    assert runner.inputs == []



def test_player_rank_change_keeps_benchmark_and_consumed_search_time(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    refreshed = frame(rank=101, opponents=candidates)
    calls = timed_refresh(runner, refreshed)
    returned, selected = runner.scout(frame(rank=100, opponents=candidates))
    assert returned is refreshed and selected[0] in tuple(p.choice for p in candidates)
    assert runner.search_state.elapsed_seconds == len(calls) == 4
    assert runner.search_state.benchmark_observations == 4
    assert runner.survey_own_rank == 101
    assert runner.survey_failure_reason is None



def test_player_rank_change_during_survey_saves_progress_at_current_rank(runner):
    from ba_automator import tactical_survey as survey
    candidates = tuple(observed(str(i), rank=70 + i, level=75) for i in range(3))
    current = frame(rank=100, tickets=5, opponents=candidates)
    timed_refresh(runner, frame(rank=101, tickets=5, opponents=candidates))
    runner.survey_time_available = lambda: runner.now[0] < 2
    runner.battle = lambda *a: pytest.fail("Benchmark is unfinished")
    assert runner.run_menu(current) == "done"
    saved = state.read_state(runner.config)
    assert saved["last_tickets"] == 5 and saved["attempts"] == {}
    assert saved["pending"] is None
    checkpoint = survey.load_survey(runner.config, day_key="2026-09-26", own_rank=101,
                                    time_budget_seconds=10, now=runner.wall_clock().timestamp())
    assert checkpoint["own_rank"] == 101
    assert checkpoint["search"].elapsed_seconds == 2
    assert set(checkpoint["search"].scores) == {"0", "1", "2"}
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]


def test_rank_change_rechecks_current_opponents_against_new_player_rank(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    current = frame(rank=100, opponents=candidates)
    timed_refresh(runner, frame(rank=71, opponents=candidates))
    assert runner.scout(current)[1] == ()
    assert runner.search_state.exhausted
    assert runner.survey_own_rank == 71


def test_scouting_time_limit_returns_home_with_tickets_intact(runner, monkeypatch):
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
    timed_refresh(runner, f)
    assert runner.scout(f)[1] == ()
    assert runner.search_state.exhausted
    assert runner.search_state.scores == {}



@pytest.mark.parametrize("all_ahead,accepted", [(True, True), (False, False)])
def test_independent_ahead_ranks_allow_one_fully_read_team(runner, all_ahead, accepted):
    candidate = Candidate(Opponent("readable", 90, 80, (70, 75, 80)))
    f = frame(rank=100, all_ahead=all_ahead, sampled_ranks=(90, 92, 95), opponents=(candidate,))
    timed_refresh(runner, f)
    assert bool(runner.scout(f)[1]) is accepted












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
    returned = frame(tickets=5, rank=100)
    runner.menu = lambda: returned
    assert runner.battle(frame(tickets=5, rank=100), candidate) == (returned, None)
    assert runner.inputs[-1] == ((1014, 97), "Close rejected opponent detail")
    assert runner.search_state.remaining_seconds == 10
    assert state.read_state(runner.config)["pending"] is None



@pytest.mark.parametrize("player_rank,opponent_rank", [(101, 80), (99, 80), (100, 100), (100, 101), (None, 80)])
def test_changed_detail_rank_never_opens_formation(runner, player_rank, opponent_rank):
    selected = Candidate(Opponent("enemy", 80, 79, (79, 75, 70)))
    fresh = Candidate(Opponent("enemy", opponent_rank, 79, (79, 75, 70)))
    runner.wait = lambda *a, **k: frame(
        "opponent", rank=player_rank, opponents=(fresh,), tickets=5, after_tickets=4,
        target=(640, 570))
    returned = frame(tickets=5, rank=100)
    runner.menu = lambda: returned
    assert runner.battle(frame(tickets=5, rank=100), selected) == (returned, None)
    assert runner.inputs[-1] == ((1014, 97), "Close rejected opponent detail")
    assert runner.search_state.remaining_seconds == 10
    assert runner.inputs[0][0] == selected.target
    assert state.read_state(runner.config)["pending"] is None


def test_changed_detail_team_levels_never_opens_formation(runner):
    selected = Candidate(Opponent("enemy", 80, 79, (79, 75, 70)))
    fresh = Candidate(Opponent("enemy", 80, 79, (79, 75, 78)))
    runner.wait = lambda *a, **k: frame(
        "opponent", rank=100, opponents=(fresh,), tickets=5, after_tickets=4,
        target=(640, 570))
    returned = frame(tickets=5, rank=100)
    runner.menu = lambda: returned
    assert runner.battle(frame(tickets=5, rank=100), selected) == (returned, None)
    assert runner.inputs[-1] == ((1014, 97), "Close rejected opponent detail")
    assert runner.search_state.remaining_seconds == 10
    assert state.read_state(runner.config)["pending"] is None


@pytest.mark.parametrize('change', [None, 'name', 'account', 'avatar', 'team',
                                   'tickets', 'projection', 'reserve', 'rank', 'behind'])
def test_registered_preview_keeps_all_battle_entry_guards(runner, monkeypatch, change):
    from ba_automator.tactical_vision import _signature
    from ba_automator.vision import decode_frame

    fixtures = Path(__file__).parent / 'fixtures'
    listed = decode_frame((fixtures / 'tactical-portrait-native-shift-list.png').read_bytes())
    png = (fixtures / 'tactical-portrait-native-shift-detail.png').read_bytes()
    selected = ObservedOpponent(Opponent('listed', 469, 79, (78, 79, 79)),
                                'Scout', (830, 409),
                                _signature(listed, (484, 365, 526, 401)))
    observed = replace(selected, choice=replace(selected.choice, opponent_id='detail'),
                       signature=_signature(decode_frame(png), (278, 179, 320, 215)))
    if change == 'name':
        observed = replace(observed, name='Another scout')
    elif change == 'account':
        observed = replace(observed, choice=replace(observed.choice, level=80))
    elif change == 'avatar':
        selected = replace(selected, signature=_signature(listed, (484, 206, 526, 242)))
    elif change == 'team':
        observed = replace(observed, choice=replace(observed.choice, visible_levels=(79, 79, 79)))
    elif change == 'behind':
        observed = replace(observed, choice=replace(observed.choice, rank=524))
    detail = frame('opponent', rank=522 if change == 'rank' else 523,
                   opponents=(observed,), tickets=3 if change == 'tickets' else 4,
                   after_tickets=2 if change == 'projection' else 3, target=(640, 575))
    detail.capture.png = png
    monkeypatch.setattr('ba_automator.tactical_runtime.same_opponent', same_opponent)

    def wait(kinds, **kwargs):
        if kinds == {'formation', 'timeout_notice'}:
            raise RuntimeError('Reached formation; test stops before battle')
        if change == 'reserve':
            runner.reserve = 4
        return detail

    runner.wait = wait
    returned = frame(tickets=4, rank=523)
    runner.menu = lambda: returned
    if change is None:
        with pytest.raises(RuntimeError, match='Reached formation'):
            runner.battle(frame(tickets=4, rank=523), selected)
        assert runner.inputs[-1] == ((640, 575), 'Open the saved attack formation')
        registration = [data for event, data in runner.events
                        if event == ('opponent_portrait_registered',)]
        assert len(registration) == 1
        assert registration[0]['offset'] == [0, 2]
        assert registration[0]['correlation'] >= .95
    else:
        assert runner.battle(frame(tickets=4, rank=523), selected) == (returned, None)
        assert runner.inputs[-1] == ((1014, 97), 'Close rejected opponent detail')
        assert not any(event == ('opponent_portrait_registered',) for event, _ in runner.events)
    assert state.read_state(runner.config)['pending'] is None


def test_unreadable_team_in_verified_preview_restarts_search_without_entering(runner):
    selected = Candidate(Opponent("enemy", 80, 79, (79, 75, 70)))
    runner.search_state = benchmark_search(runner, elapsed=9)
    runner.save_search()
    previous = runner.search_id
    runner.wait = lambda *a, **k: frame(
        "opponent", rank=100, opponents=(), tickets=5, after_tickets=4,
        target=None)
    returned = frame(tickets=5, rank=100)
    runner.menu = lambda: returned

    assert runner.battle(frame(tickets=5, rank=100), selected) == (returned, None)
    assert runner.inputs == [
        (selected.target, "Inspect selected Tactical Challenge opponent"),
        ((1014, 97), "Close rejected opponent detail"),
    ]
    assert runner.search_id != previous
    assert runner.search_state.remaining_seconds == 10
    assert runner.search_state.scores == {}
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
    timed_refresh(runner, current)
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
    refreshes = timed_refresh(runner, current)

    def battle(f, candidate):
        saved = state.read_state(runner.config)
        assert candidate.choice.opponent_id == "0"
        assert saved["retry_opponent_id"] is None
        assert saved["attempts"] == {"gone": 1}
        return frame(tickets=1, rank=100), False

    runner.battle = battle
    assert runner.run_menu(current) == "done"
    assert len(refreshes) == 4
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


@pytest.mark.parametrize("won", [True, False])
@pytest.mark.parametrize("already_saved", [True, False])
def test_run_recovers_visible_result_before_navigation_and_counts_once(runner, won, already_saved):
    now = runner.wall_clock()
    intent = state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100, now=now)
    if already_saved:
        state.record_outcome(runner.config, intent, won=won, evidence="original.png", now=now)
    runner.reserve = 4
    runner.wait = lambda *a, **k: frame("result", won=won, target=(640, 531))
    runner.menu = lambda: frame(tickets=4, rank=80 if won else 100)
    runner.navigate = lambda *a: pytest.fail("Result must be recovered before navigating")
    runner.scout = lambda *a: pytest.fail("Recovery must honor the ticket reserve")
    images = []
    runner.journal.save_image = lambda *a: images.append(a)
    tap = runner.tap

    def guarded_tap(observed, target, detail):
        if observed.screen.kind == "result":
            saved = state.read_state(runner.config)
            assert saved["pending"]["outcome"]["won"] is won
            assert saved["attempts"] == {}
        tap(observed, target, detail)

    runner.tap = guarded_tap
    assert runner.run() == "done"
    saved = state.read_state(runner.config)
    assert saved["pending"] is None and saved["attempts"] == {"enemy": 1}
    assert saved["last_tickets"] == 4
    assert images == [(f"battle-{intent}-recovered-result.png", b"image")]
    assert runner.inputs == [
        ((640, 531), "Close recovered Tactical Challenge battle result"),
        ((1237, 23), "Return home from Tactical Challenge battles"),
    ]
    assert len(runner.important) == 1
    args, detail = runner.important[0]
    assert args[1] == "tactical_battle_recovered" and detail["won"] is won
    assert detail["evidence"] == ("original.png" if already_saved else
                                  str(runner.run_dir / images[0][0]))


@pytest.mark.parametrize("kind", ["home", "campaign", "tactical", "battle_tip"])
def test_run_unproven_pending_never_navigates_from_nonresult_screen(runner, kind):
    intent = state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100,
                                now=runner.wall_clock())
    runner.wait = lambda *a, **k: frame(kind, target=(640, 660))
    runner.navigate = lambda *a: pytest.fail("Unproven pending battle blocks navigation")
    with pytest.raises(RuntimeError, match="no verified result on screen"):
        runner.run()
    saved = state.read_state(runner.config)
    assert saved["pending"]["id"] == intent
    assert saved["pending"].get("outcome") is None and saved["attempts"] == {}
    assert runner.inputs == []


def test_run_pending_result_wait_is_bounded_and_read_only(runner):
    intent = state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100,
                                now=runner.wall_clock())

    def wait(kinds, *, timeout):
        assert "result" in kinds and timeout == 40
        raise RuntimeError("no recognized result")

    runner.wait = wait
    runner.navigate = lambda *a: pytest.fail("Unknown screen must preserve pending battle")
    with pytest.raises(RuntimeError, match="no recognized result"):
        runner.run()
    assert state.read_state(runner.config)["pending"]["id"] == intent
    assert runner.inputs == []


@pytest.mark.parametrize("reason", ["wrong_count", "conflicting_result", "new_day", "unread_outcome"])
def test_run_result_recovery_preserves_ambiguous_ticket_hold(runner, reason):
    now = runner.wall_clock()
    intent = state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100, now=now)
    if reason == "conflicting_result":
        state.record_outcome(runner.config, intent, won=False, evidence="loss.png", now=now)
    if reason == "new_day":
        runner.wall_clock = lambda: now + timedelta(days=1)
    runner.wait = lambda *a, **k: frame("result", won=None if reason == "unread_outcome" else True,
                                      target=(640, 531))
    runner.menu = lambda: frame(tickets=5, rank=80)
    runner.navigate = lambda *a: pytest.fail("Recovery must not replay a battle")
    runner.scout = lambda *a: pytest.fail("Recovery must reconcile before scouting")
    with pytest.raises(RuntimeError):
        runner.run()
    saved = state.read_state(runner.config)
    assert saved["pending"]["id"] == intent and saved["attempts"] == {}
    assert len(runner.inputs) == (1 if reason == "wrong_count" else 0)


def test_run_result_is_not_dismissed_if_durable_outcome_write_fails(runner, monkeypatch):
    intent = state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100,
                                now=runner.wall_clock())
    runner.wait = lambda *a, **k: frame("result", won=True, target=(640, 531))

    def fail_write(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(state, "record_outcome", fail_write)
    with pytest.raises(OSError, match="disk unavailable"):
        runner.run()
    saved = state.read_state(runner.config)
    assert saved["pending"]["id"] == intent and saved["pending"].get("outcome") is None
    assert runner.inputs == []


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
    initial = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    ignored = frame(refresh_seconds=109, sampled_ranks=(70, 71, 72), captured_at=5)
    runner.refresh_count = 999
    runner.sleep = lambda duration: None
    runner.menu = lambda: ignored
    with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
        runner.refresh(initial)
    assert runner.refresh_count == 1000 and not runner.last_refresh_acknowledged
    assert runner.refresh(initial) is initial
    assert runner.refresh_count == 1000 and len(runner.inputs) == 1
    assert not runner.last_refresh_acknowledged


def test_search_saves_after_final_available_chunk_refresh(runner):
    candidates = tuple(Candidate(Opponent(str(i), 70 + i, 75)) for i in range(3))
    initial = frame(rank=100, opponents=candidates, refresh_seconds=114,
                    refresh_target=(1174, 147))
    refreshed = frame(rank=100, opponents=candidates, refresh_seconds=119, captured_at=5)
    runner.refresh_count = 99
    runner.menu = lambda: refreshed
    assert runner.scout(initial) == (refreshed, ())
    assert runner.refresh_count == 100
    assert runner.search_state.refreshes == 1
    assert runner.defer_survey
    assert len(runner.inputs) == 1






def test_identity_capacity_reports_failure_without_discarding_fought_identity(runner, monkeypatch):
    # A small cap exercises the same persistence boundary without a huge fixture.
    monkeypatch.setattr(state, "MAX_IDENTITIES", 3)
    remembered = tuple(observed(str(i), rank=70 + i, level=75) for i in range(3))
    seed_history(runner, remembered[0])
    runner.observed = {p.choice.opponent_id: p for p in remembered}
    current = frame(rank=100, tickets=2, opponents=(observed("new", rank=60),))
    runner.refresh = lambda f: pytest.fail("A full identity store must release the queue")
    with pytest.raises(RuntimeError, match="Opponent identity limit reached"):
        runner.run_menu(current)
    saved = state.read_state(runner.config)
    assert runner.identity_capacity_reached
    assert len(saved["identities"]) == 3 and "new" not in saved["identities"]
    assert saved["attempts"] == {"0": 1} and saved["pending"] is None
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]


def test_survey_expiring_during_a_visit_returns_home_then_reports_failure(runner, monkeypatch):
    from ba_automator import tactical_survey as survey
    candidates = tuple(observed(str(i), rank=70 + i, level=75) for i in range(3))
    current = frame(rank=100, tickets=5, opponents=candidates)
    now = runner.wall_clock()
    search = SearchState(runner.search_policy)
    resumed = {"search": search, "search_id": "a" * 32, "identities": {},
               "created_at": now.timestamp() - survey.MAX_AGE_SECONDS + 1}
    monkeypatch.setattr(survey, "load_survey", lambda *a, **k: resumed)
    def refresh(f):
        runner.wall_clock = lambda: now + timedelta(seconds=2)
        runner.now[0] += 1
        return current
    runner.refresh = refresh
    runner.battle = lambda *a: pytest.fail("Expired evidence must not authorize a battle")
    runner.finish = lambda *a: pytest.fail("Expired evidence is not a successful visit")
    with pytest.raises(RuntimeError, match="Saved opponent observations expired"):
        runner.run_menu(current)
    saved = state.read_state(runner.config)
    assert runner.survey_expired and runner.search_state.elapsed_seconds == 1
    assert saved["last_tickets"] == 5 and saved["attempts"] == {}
    assert saved["pending"] is None
    import json
    checkpoint = json.loads(survey.survey_path(runner.config).read_text())
    assert checkpoint["status"] == "blocked"
    assert checkpoint["search"]["elapsed_seconds"] == 1
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
    returned = frame(tickets=5, rank=100)
    runner.menu = lambda: returned
    assert runner.battle(frame(tickets=5, rank=100), selected) == (returned, None)
    assert runner.inputs == [(selected.target, "Inspect selected Tactical Challenge opponent"),
                             ((1014, 97), "Close rejected opponent detail")]
    assert runner.search_state.remaining_seconds == 10
    assert state.read_state(runner.config)["pending"] is None


def enable_survey_retry(runner):
    runner.config.tactical_battles_enabled_in_daily = True
    runner.config.tactical_battles_preserve_tickets = 1
    state.observe_ladder(runner.config, "2026-09-26", tickets=5, rank=100,
                         now=runner.wall_clock())


def test_sent_refresh_verification_failure_schedules_fresh_search_after_backoff(runner):
    from ba_automator import tactical_retry as retry
    enable_survey_retry(runner)
    before = state.state_path(runner.config).read_bytes()
    initial = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    runner.menu = lambda: frame(refresh_seconds=109, sampled_ranks=(70, 71, 72), captured_at=5)
    runner.sleep = lambda seconds: None
    with pytest.raises(RuntimeError, match="refresh was sent"):
        runner.refresh(initial)
    now = runner.wall_clock().timestamp()
    assert not retry.retry_due(runner.config, now=now + 899)
    assert retry.retry_due(runner.config, now=now + 900)
    assert state.state_path(runner.config).read_bytes() == before
    assert len(runner.inputs) == 1  # No in-visit retry or battle input.


def test_incomplete_refresh_rank_labels_schedule_fresh_search(runner):
    from ba_automator import tactical_retry as retry
    enable_survey_retry(runner)
    initial = frame(rank=100, opponents=(), sampled_ranks=(70, 71, 72))
    unreadable = frame(rank=100, opponents=(), sampled_ranks=(70, 71))
    runner.refresh = lambda f: unreadable
    with pytest.raises(RuntimeError, match="Incomplete opponent rank labels"):
        runner.scout(initial)
    assert retry.retry_due(runner.config, now=runner.wall_clock().timestamp() + 900)
    assert state.read_state(runner.config)["attempts"] == {}


@pytest.mark.parametrize("guard", ["pending", "blocked", "new_day", "disabled", "reserve"])
def test_survey_verification_failure_does_not_bypass_battle_or_schedule_guards(runner, guard):
    from ba_automator import tactical_retry as retry
    enable_survey_retry(runner)
    if guard == "pending":
        state.begin_battle(runner.config, "2026-09-26", "enemy", 5, 100,
                           now=runner.wall_clock())
    elif guard == "blocked":
        saved = state.read_state(runner.config)
        saved["blocked_reason"] = "Unknown battle outcome"
        state.write_state(runner.config, saved)
    elif guard == "new_day":
        runner.run_day = "2026-09-25"
    elif guard == "disabled":
        runner.config.tactical_battles_enabled_in_daily = False
    else:
        runner.config.tactical_battles_preserve_tickets = 5
    before = state.state_path(runner.config).read_bytes()
    with pytest.raises(RuntimeError, match="Bad draw"):
        runner.fail_survey_verification("Bad draw")
    assert not retry.retry_path(runner.config).exists()
    assert state.state_path(runner.config).read_bytes() == before


@pytest.mark.parametrize("failure_at", ["tap", "read"])
def test_transport_failure_does_not_schedule_a_survey_retry(runner, failure_at):
    from ba_automator import tactical_retry as retry
    enable_survey_retry(runner)
    initial = frame(refresh_seconds=114, sampled_ranks=(70, 71, 72),
                    refresh_target=(1174, 147))
    runner.sleep = lambda seconds: None

    def broken(*args):
        raise OSError("transport interrupted")

    if failure_at == "tap":
        runner.tap = broken
    else:
        runner.menu = broken
    with pytest.raises(OSError, match="transport interrupted"):
        runner.refresh(initial)
    assert not retry.retry_path(runner.config).exists()


def test_manual_visit_cancels_a_waiting_retry_without_resetting_daily_cap(runner):
    from ba_automator import tactical_retry as retry
    enable_survey_retry(runner)
    now = runner.wall_clock().timestamp()
    for _ in range(2):
        assert retry.schedule_retry(runner.config, now=now)
        retry.clear_retry(runner.config)
    assert retry.schedule_retry(runner.config, now=now)
    runner.reserve = 5
    assert runner.run_menu(frame(tickets=5, rank=100)) == "done"
    assert not retry.retry_due(runner.config, now=now + 900)
    assert not retry.schedule_retry(runner.config, now=now)


def benchmark_search(runner, *, elapsed=4):
    """A real persisted benchmark whose weakest quarter is level 50."""
    candidates = tuple(observed(f"benchmark-{i}", rank=60 + i, level=50 + i * 5)
                       for i in range(3))
    search = SearchState(runner.search_policy)
    search.observe(tuple(p.choice for p in candidates), refreshed=False)
    search.advance(1)
    search.observe(tuple(p.choice for p in candidates))
    search.advance(elapsed - 1)
    runner.observed.update({p.choice.opponent_id: p for p in candidates})
    return search


def test_overtime_can_refresh_after_waiting_for_timer_headroom(runner):
    runner.search_state = benchmark_search(runner, elapsed=10)
    initial = frame(rank=100, sampled_ranks=(70, 71, 72), refresh_seconds=119,
                    refresh_target=(1174, 147), captured_at=0)
    ready = frame(rank=100, sampled_ranks=(70, 71, 72), refresh_seconds=114,
                  refresh_target=(1174, 147), captured_at=5)
    reset = frame(rank=100, sampled_ranks=(70, 71, 72), refresh_seconds=119,
                  captured_at=10)
    observations = iter((ready, reset))
    runner.menu = lambda: next(observations)
    assert runner.refresh(initial) is reset
    assert runner.last_refresh_acknowledged
    assert runner.refresh_count == 1
    assert len(runner.inputs) == 1


@pytest.mark.parametrize("qualifies", [True, False])
def test_overtime_still_rejects_four_hour_old_evidence(runner, qualifies):
    from ba_automator import tactical_survey as survey
    runner.search_state = benchmark_search(runner, elapsed=10)
    runner.survey_created_at = runner.wall_clock().timestamp() - survey.MAX_AGE_SECONDS - 1
    candidate = observed("current", rank=70, level=40 if qualifies else 89)
    current = frame(rank=100, all_ahead=True, opponents=(candidate,), sampled_ranks=(70, 71, 72))
    runner.refresh = lambda f: pytest.fail("Stale observations cannot authorize more scouting")
    assert runner.scout(current)[1] == ()
    assert runner.search_state.exhausted and runner.survey_expired
    assert runner.inputs == []


def test_resumed_search_excludes_time_spent_waiting_in_the_queue(runner):
    from ba_automator import tactical_survey as survey
    search = benchmark_search(runner, elapsed=2)
    survey.save_survey(runner.config, day_key="2026-09-26", own_rank=100,
                       search=search, identities={k: asdict(v) for k, v in runner.observed.items()},
                       now=runner.wall_clock().timestamp() - 120)
    # A different monotonic origin mimics a later process. Only its active
    # refreshes may count, never either queue delay or the process clock origin.
    runner.now[0] = runner.started = 3000
    current = frame(rank=100, tickets=2, cooldown=0, opponents=tuple(runner.observed.values()))
    refreshes = timed_refresh(runner, current)
    selected = []
    def battle(f, candidate):
        selected.append(runner.search_state.to_dict())
        intent = state.begin_battle(runner.config, "2026-09-26", candidate.choice.opponent_id,
                                    2, 100, now=runner.wall_clock())
        state.complete_battle(runner.config, intent, tickets_after=1, won=False,
                              rank_after=100, now=runner.wall_clock())
        return frame(rank=100, tickets=1), False
    runner.battle = battle
    assert runner.run_menu(current) == "done"
    assert len(refreshes) == 2
    assert selected[0]["elapsed_seconds"] == 4
    assert selected[0]["refreshes"] == 3
    assert not survey.survey_path(runner.config).exists()
    assert state.read_state(runner.config)["attempts"] == {"benchmark-0": 1}


@pytest.mark.parametrize("first_won", [False, True])
def test_completed_battle_starts_a_fresh_benchmark_and_excludes_fought_opponents(runner, first_won):
    from ba_automator import tactical_survey as survey
    candidates = tuple(observed(str(i), rank=70 + i, level=50 + i * 10) for i in range(3))
    initial = frame(rank=100, tickets=3, opponents=candidates, cooldown=0)
    current = [initial]
    refreshes = []
    def refresh(previous):
        runner.now[0] += 1
        runner.refresh_count += 1
        refreshes.append(previous)
        return current[0]
    runner.refresh = refresh
    histories = []
    def battle(f, candidate):
        snapshot = runner.search_state.to_dict()
        histories.append((candidate.choice.opponent_id, snapshot))
        # A substantial battle wait must not debit the following search.
        runner.now[0] += 60
        won = first_won if len(histories) == 1 else False
        rank = 90 if won else f.screen.rank
        after = f.screen.tickets - 1
        intent = state.begin_battle(runner.config, "2026-09-26", candidate.choice.opponent_id,
                                    f.screen.tickets, f.screen.rank, now=runner.wall_clock())
        state.complete_battle(runner.config, intent, tickets_after=after,
                              won=won, rank_after=rank, now=runner.wall_clock())
        current[0] = frame(rank=rank, tickets=after, opponents=candidates, cooldown=0)
        return current[0], won
    runner.battle = battle
    assert runner.run_menu(initial) == "done"
    assert [identity for identity, _ in histories] == ["0", "1"]
    assert len(refreshes) == 8
    for _, snapshot in histories:
        assert snapshot["elapsed_seconds"] == 4
        assert snapshot["refreshes"] == 4
        assert snapshot["benchmark_observations"] == 4
    assert "0" not in histories[1][1]["scores"]
    assert state.read_state(runner.config)["attempts"] == {"0": 1, "1": 1}
    assert not survey.survey_path(runner.config).exists()


def test_rejected_preview_discards_bad_benchmark_and_gives_fresh_timer(runner):
    from ba_automator import tactical_survey as survey
    old = observed("old", level=50)
    seed_history(runner, observed("fought"))
    runner.search_state = benchmark_search(runner, elapsed=9)
    runner.save_search()
    previous = runner.search_id
    runner.run_day = "2026-09-26"
    returned = frame(tickets=4, rank=100)
    assert runner.restart_rejected_search(returned, tickets_before=4, reason="Bad OCR") == (returned, None)
    saved = survey.load_survey(runner.config, day_key="2026-09-26", own_rank=100,
                              time_budget_seconds=10, now=runner.wall_clock().timestamp())
    assert saved["search_id"] != previous
    assert saved["search"].remaining_seconds == 10
    assert saved["search"].scores == {} and saved["search"].refreshes == 0
    assert state.read_state(runner.config)["attempts"] == {"fought": 1}
    assert state.read_state(runner.config)["pending"] is None


@pytest.mark.parametrize("tickets", [3, 5, None])
def test_rejected_preview_cannot_reset_search_with_changed_ticket_count(runner, tickets):
    runner.search_state = benchmark_search(runner, elapsed=9)
    runner.save_search()
    previous = runner.search_id
    with pytest.raises(RuntimeError, match="Ticket count changed"):
        runner.restart_rejected_search(frame(tickets=tickets, rank=100),
                                        tickets_before=4, reason="Bad OCR")
    assert runner.search_id == previous and runner.search_state.remaining_seconds == 1


def test_recovered_loss_discards_previous_match_checkpoint_before_fresh_search(runner):
    from ba_automator import tactical_survey as survey
    candidate = observed("old-enemy", rank=70, level=50)
    search = benchmark_search(runner, elapsed=9)
    survey.save_survey(runner.config, day_key="2026-09-26", own_rank=100,
                       search=search, identities={k: asdict(v) for k, v in runner.observed.items()},
                       now=runner.wall_clock().timestamp())
    state.save_identities(runner.config, "2026-09-26", {"old-enemy": asdict(candidate)},
                          now=runner.wall_clock())
    intent = state.begin_battle(runner.config, "2026-09-26", "old-enemy", 3, 100,
                                now=runner.wall_clock())
    state.record_outcome(runner.config, intent, won=False, evidence="result.png", now=runner.wall_clock())
    current = frame(rank=100, tickets=2, opponents=(candidate,), cooldown=0)
    def scout(f):
        assert runner.survey_resume is None and runner.search_state is None
        assert not survey.survey_path(runner.config).exists()
        assert state.read_state(runner.config)["attempts"] == {"old-enemy": 1}
        return f, ()
    runner.scout = scout
    assert runner.run_menu(current) == "done"
    assert state.read_state(runner.config)["pending"] is None


@pytest.mark.parametrize("recovered", [False, True])
def test_completed_loss_logging_failure_cannot_reuse_previous_match_search(
        runner, monkeypatch, recovered):
    from ba_automator import tactical_survey as survey

    old = observed("old-enemy", rank=70, level=40)
    runner.search_state = benchmark_search(runner, elapsed=9)
    runner.observed[old.choice.opponent_id] = old
    runner.save_search()
    state.save_identities(runner.config, "2026-09-26",
                          {key: asdict(value) for key, value in runner.observed.items()},
                          now=runner.wall_clock())
    returned = frame(tickets=4, rank=100, cooldown=0)

    def logging_failure(*args, **kwargs):
        raise OSError("Important action log unavailable")

    monkeypatch.setattr("ba_automator.tactical_runtime.record_action", logging_failure)
    if recovered:
        intent = state.begin_battle(runner.config, "2026-09-26", old.choice.opponent_id,
                                    5, 100, now=runner.wall_clock())
        state.record_outcome(runner.config, intent, won=False, evidence="result.png",
                             now=runner.wall_clock())
        with pytest.raises(OSError, match="Important action log unavailable"):
            runner.run_menu(returned)
    else:
        observations = iter((
            frame("opponent", opponents=(old,), tickets=5, after_tickets=4,
                  target=(640, 570)),
            frame("formation", formation_seconds=120, skip_selected=True,
                  target=(1170, 666)),
            frame("result", won=False, target=(1100, 650)),
        ))
        runner.wait = lambda *args, **kwargs: next(observations)
        runner.menu = lambda: returned
        runner.fill_attack_formation = lambda current: current
        with pytest.raises(OSError, match="Important action log unavailable"):
            runner.battle(frame(tickets=5, rank=100), old)

    saved = state.read_state(runner.config)
    assert saved["pending"] is None and saved["attempts"] == {"old-enemy": 1}
    assert runner.search_state is runner.search_clock is runner.survey_resume is None
    assert runner.survey_created_at is None
    # The outer runner's exception handler must not resurrect the old search.
    runner.advance_search_clock()
    runner.save_search(status="blocked")
    assert not survey.survey_path(runner.config).exists()

    monkeypatch.setattr("ba_automator.tactical_runtime.record_action", lambda *args, **kwargs: None)
    candidates = tuple(observed(f"next-{index}", rank=71 + index, level=50 + index * 10)
                       for index in range(3))
    current = frame(tickets=4, rank=100, opponents=candidates, cooldown=0)
    refreshes = timed_refresh(runner, current)

    def next_battle(current, candidate):
        assert runner.search_state.elapsed_seconds == 4
        assert runner.search_state.refreshes == 4
        assert runner.search_state.benchmark_observations == 4
        assert runner.search_state.scores.keys() == {p.choice.opponent_id for p in candidates}
        raise RuntimeError("Next match has a fresh benchmark")

    runner.battle = next_battle
    with pytest.raises(RuntimeError, match="Next match has a fresh benchmark"):
        runner.run_menu(current)
    assert len(refreshes) == 4
    assert state.read_state(runner.config)["attempts"] == {"old-enemy": 1}


@pytest.mark.parametrize("recovered", [False, True])
def test_proven_pending_protects_search_removal_before_completion_failure(
        runner, monkeypatch, recovered):
    from ba_automator import tactical_survey as survey

    candidate = observed("old-enemy", rank=70, level=40)
    runner.search_state = benchmark_search(runner, elapsed=9)
    runner.save_search()
    assert survey.survey_path(runner.config).exists()
    returned = frame(tickets=4, rank=100, cooldown=0)

    def completion_failure(*args, **kwargs):
        assert not survey.survey_path(runner.config).exists()
        assert runner.search_state is runner.search_clock is runner.survey_resume is None
        pending = state.read_state(runner.config)["pending"]
        assert pending["opponent_id"] == "old-enemy"
        assert pending["outcome"]["won"] is False
        raise OSError("Completion interrupted")

    monkeypatch.setattr(state, "complete_battle", completion_failure)
    if recovered:
        intent = state.begin_battle(runner.config, "2026-09-26", "old-enemy",
                                    5, 100, now=runner.wall_clock())
        state.record_outcome(runner.config, intent, won=False, evidence="result.png",
                             now=runner.wall_clock())
        with pytest.raises(OSError, match="Completion interrupted"):
            runner.run_menu(returned)
    else:
        observations = iter((
            frame("opponent", opponents=(candidate,), tickets=5, after_tickets=4,
                  target=(640, 570)),
            frame("formation", formation_seconds=120, skip_selected=True,
                  target=(1170, 666)),
            frame("result", won=False, target=(1100, 650)),
        ))
        runner.wait = lambda *args, **kwargs: next(observations)
        runner.menu = lambda: returned
        runner.fill_attack_formation = lambda current: current
        with pytest.raises(OSError, match="Completion interrupted"):
            runner.battle(frame(tickets=5, rank=100), candidate)

    saved = state.read_state(runner.config)
    assert saved["pending"]["outcome"]["won"] is False
    assert saved["attempts"] == {}
    with pytest.raises(state.TacticalStateError):
        state.begin_battle(runner.config, "2026-09-26", "next-enemy",
                           4, 100, now=runner.wall_clock())
    runner.advance_search_clock()
    runner.save_search(status="blocked")
    assert not survey.survey_path(runner.config).exists()
    assert len([detail for _, detail in runner.inputs if detail.startswith("Mobilize")]) == (
        0 if recovered else 1)


def test_unproven_pending_preserves_existing_search_and_blocks_entry(runner):
    from ba_automator import tactical_survey as survey

    search = runner.search_state = benchmark_search(runner, elapsed=9)
    runner.save_search()
    path = survey.survey_path(runner.config)
    original = path.read_bytes()
    state.begin_battle(runner.config, "2026-09-26", "old-enemy",
                       5, 100, now=runner.wall_clock())
    with pytest.raises(state.TacticalStateError, match="no proven outcome"):
        runner.run_menu(frame(tickets=5, rank=100, cooldown=0))
    assert runner.search_state is search
    assert path.read_bytes() == original
    assert state.read_state(runner.config)["pending"].get("outcome") is None
    assert runner.inputs == []


def ignored_refresh_frames(*, rank=100, tickets=5):
    candidates = tuple(observed(f"same-{i}", rank=70 + i, level=50 + i) for i in range(3))
    initial = frame(rank=rank, tickets=tickets, opponents=candidates, refresh_seconds=111,
                    refresh_target=(1174, 147), captured_at=0)
    ignored = frame(rank=rank, tickets=tickets, opponents=candidates, refresh_seconds=108,
                    refresh_target=(1174, 147), captured_at=5.192)
    acknowledged = frame(rank=rank, tickets=tickets, opponents=candidates,
                         refresh_seconds=118, captured_at=10.392)
    return initial, ignored, acknowledged


def test_verified_ignored_refresh_retries_once_and_counts_only_acknowledged_draw(runner):
    initial, ignored, acknowledged = ignored_refresh_frames()
    runner.search_state = SearchState(SearchPolicy(time_budget_seconds=30))
    runner.search_clock = 0
    observations = iter((ignored, acknowledged))

    def menu():
        current = next(observations)
        runner.now[0] = current.capture.captured_at
        return current

    runner.menu = menu
    assert runner.refresh(initial) is acknowledged
    assert runner.last_refresh_acknowledged
    assert runner.refresh_count == len(runner.inputs) == 2
    assert runner.search_state.elapsed_seconds == 10.392
    runner.search_state.observe((p.choice for p in acknowledged.screen.opponents),
                                refreshed=runner.last_refresh_acknowledged)
    assert runner.search_state.refreshes == 1
    resets = [fields["acknowledged"] for names, fields in runner.events
              if names == ("opponents_refreshed",)]
    assert resets == [False, True]


@pytest.mark.parametrize("limit", ["chunk", "day"])
def test_ignored_refresh_cannot_retry_past_existing_limits(runner, limit):
    initial, ignored, _ = ignored_refresh_frames()
    runner.search_state = SearchState(SearchPolicy(time_budget_seconds=5 if limit == "deadline" else 30))
    runner.search_clock = 0
    if limit == "chunk":
        runner.refresh_count = 99
    if limit == "day":
        runner.run_day = "2026-09-25"

    def menu():
        runner.now[0] = ignored.capture.captured_at
        return ignored

    runner.menu = menu
    assert runner.refresh(initial) is ignored
    assert len(runner.inputs) == 1
    assert not runner.last_refresh_acknowledged
    assert runner.search_state.refreshes == 0


@pytest.mark.parametrize("change", ["ranks", "own_rank", "tickets", "identity", "unreadable", "timer_jump"])
def test_local_refresh_retry_requires_unchanged_verified_context(runner, change):
    initial, ignored, _ = ignored_refresh_frames()
    if change == "ranks":
        ignored.screen.sampled_ranks = (71, 72, 73)
    elif change == "own_rank":
        ignored.screen.rank = 101
    elif change == "tickets":
        ignored.screen.tickets = 4
    elif change == "identity":
        ignored.screen.opponents = (observed("different", rank=70), *ignored.screen.opponents[1:])
    elif change == "unreadable":
        ignored.screen.opponents = ignored.screen.opponents[:2]
    else:
        ignored.screen.refresh_seconds = 100  # Does not match the natural countdown.
    runner.menu = lambda: ignored
    with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
        runner.refresh(initial)
    assert len(runner.inputs) == runner.refresh_count == 1
    assert not runner.last_refresh_acknowledged


def test_second_ignored_refresh_keeps_bounded_failure_backoff_and_consumed_time(runner):
    from ba_automator import tactical_retry as retry
    enable_survey_retry(runner)
    initial, ignored, _ = ignored_refresh_frames()
    still_ignored = frame(rank=100, tickets=5, opponents=ignored.screen.opponents,
                          refresh_seconds=103, captured_at=10.392)
    observations = iter((ignored, still_ignored))
    runner.search_state = benchmark_search(runner, elapsed=1)
    runner.search_state.policy = SearchPolicy(time_budget_seconds=30)
    runner.search_clock = 0

    def menu():
        current = next(observations)
        runner.now[0] = current.capture.captured_at
        return current

    runner.menu = menu
    with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
        runner.refresh(initial)
    assert runner.refresh_count == len(runner.inputs) == 2
    assert not runner.last_refresh_acknowledged
    assert runner.search_state.elapsed_seconds == 11.392
    assert runner.search_state.refreshes == 1  # Existing benchmark only.
    assert retry.retry_due(runner.config, now=runner.wall_clock().timestamp() + 900)


def test_real_ignored_refresh_pair_needs_no_missing_team_levels_to_retry(runner):
    from pathlib import Path
    from ba_automator.tactical_vision import TacticalBattleVision
    from ba_automator.vision import StartupVision

    vision = TacticalBattleVision(StartupVision())
    fixtures = Path(__file__).parent / "fixtures"
    captures = []
    for suffix, timestamp in (("before", 0), ("after", 5.192)):
        png = (fixtures / f"tactical-battles-ignored-refresh-{suffix}.png").read_bytes()
        screen = vision.analyze(png)
        assert screen.kind == "tactical"
        assert (screen.rank, screen.tickets, screen.sampled_ranks) == (571, 5, (445, 487, 523))
        assert screen.opponents == ()  # Tiny-level uncertainty remains excluded from battle scoring.
        captures.append(ShopFrame(Capture(png, timestamp, "test"), screen))
    assert [capture.screen.refresh_seconds for capture in captures] == [111, 108]
    runner.menu = lambda: captures[1]
    result, ignored = runner.refresh_once(captures[0])
    assert result is captures[1] and ignored
    assert not runner.last_refresh_acknowledged
    assert len(runner.inputs) == 1


@pytest.mark.parametrize("change", ["none", "row", "identity", "outside", "invalid"])
def test_refresh_only_image_guard_requires_exact_unchanged_opponent_rows(change):
    from pathlib import Path
    import cv2
    from ba_automator.tactical_runtime import unchanged_opponent_rows
    from ba_automator.vision import decode_frame

    png = (Path(__file__).parent / "fixtures" /
           "tactical-battles-ignored-refresh-before.png").read_bytes()
    image = decode_frame(png)
    if change == "row":
        image[365:375, 740:750] = 0
    elif change == "identity":
        image[196:220, 484:515] = 0
    elif change == "outside":
        image[0:40, 800:900] = 0
    other = b"invalid" if change == "invalid" else cv2.imencode(".png", image)[1].tobytes()
    assert unchanged_opponent_rows(png, other) is (change in {"none", "outside"})


@pytest.mark.parametrize("change", ["own_rank", "tickets", "rank_labels", "timer"])
def test_identical_pixels_cannot_override_refresh_context_guards(runner, change):
    from pathlib import Path

    initial, ignored, _ = ignored_refresh_frames()
    png = (Path(__file__).parent / "fixtures" /
           "tactical-battles-ignored-refresh-before.png").read_bytes()
    initial.capture.png = ignored.capture.png = png
    initial.screen.opponents = ignored.screen.opponents = ()
    if change == "own_rank":
        ignored.screen.rank += 1
    elif change == "tickets":
        ignored.screen.tickets -= 1
    elif change == "rank_labels":
        ignored.screen.sampled_ranks = (1, 2, 3)
    else:
        ignored.screen.refresh_seconds = None
    runner.menu = lambda: ignored
    with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
        runner.refresh(initial)
    assert len(runner.inputs) == 1
