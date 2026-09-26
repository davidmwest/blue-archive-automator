"""Queue continuations retain active search time and verified opponent evidence."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from ba_automator import tactical_state as state
from ba_automator import tactical_survey as survey
from ba_automator.club import game_day
from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_runtime import TacticalBattleRunner
from ba_automator.tactical_search import SearchPolicy, SearchState
from ba_automator.tactical_vision import ObservedOpponent


def observed(key, rank=50, level=60):
    return ObservedOpponent(Opponent(key, rank, level, (level,) * 3), key,
                            (1100, 230), "0" * 1152)


def frame(*people, captured_at=0, **values):
    defaults = dict(kind="tactical", rank=100, tickets=5, cooldown=0,
                    all_ahead=True, sampled_ranks=(50, 60, 70),
                    opponents=tuple(people), refresh_seconds=114,
                    refresh_target=(1174, 147))
    return NS(screen=NS(**(defaults | values)),
              capture=NS(png=b"image", captured_at=captured_at))


@pytest.fixture
def runner(tmp_path, monkeypatch):
    r = object.__new__(TacticalBattleRunner)
    r.config = NS(serial="test", package="test", state_dir=tmp_path,
                  tactical_battles_skip_battles=True,
                  tactical_battles_search_minutes=10,
                  tactical_battles_preserve_tickets=1,
                  tactical_battles_enabled_in_daily=True)
    r.reserve = 1
    r.search_policy = SearchPolicy(600)
    r.search_state = r.search_clock = None
    r.observed = {}
    r.last_refresh_acknowledged = True
    r.last_refresh_sent = False
    r.run_dir = tmp_path
    r.started = r.actions = r.refresh_count = 0
    r.active_seconds = 0
    r.clock = lambda: r.active_seconds
    r.sleep = lambda seconds: setattr(r, "active_seconds", r.active_seconds + seconds)
    r.identity_capacity_reached = False
    r.survey_own_rank = 100
    r.survey_resume = None
    r.survey_created_at = None
    r.survey_expired = False
    r.survey_failure_reason = None
    r.defer_survey = False
    r.now = datetime(2026, 9, 26, 21, tzinfo=timezone.utc)
    r.wall_clock = lambda: r.now
    r.phase = lambda text: None
    r.events = []
    r.journal = NS(record=lambda *a, **k: r.events.append((a, k)),
                   save_image=lambda *a: None)
    r.fail = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    r.inputs = []
    r.tap = lambda f, target, detail: r.inputs.append((target, detail))
    r.home = lambda: None
    r.finish = lambda status="success": status
    r.battle = lambda *a: pytest.fail("Unfinished search must not enter a battle")
    for matcher in ("same_opponent", "same_opponent_identity"):
        monkeypatch.setattr(f"ba_automator.tactical_runtime.{matcher}",
                            lambda a, b: a.choice.opponent_id == b.choice.opponent_id)
    monkeypatch.setattr("ba_automator.tactical_runtime.record_action", lambda *a, **k: None)
    return r


def load(runner):
    return survey.load_survey(
        runner.config, day_key=game_day(runner.wall_clock()), own_rank=100,
        time_budget_seconds=runner.search_policy.time_budget_seconds,
        now=runner.wall_clock().timestamp())


def next_visit(runner, queue_seconds=61):
    """Forget in-memory observations, retaining only real persistent files."""
    runner.now += timedelta(seconds=queue_seconds)
    runner.observed = {}
    runner.survey_resume = runner.search_state = runner.search_clock = None
    runner.survey_created_at = None
    runner.refresh_count = runner.actions = runner.active_seconds = 0
    runner.inputs.clear()


def checkpoint(runner, elapsed=100, status="active"):
    search = SearchState(runner.search_policy)
    person = observed("best")
    search.observe((person.choice,), refreshed=False)
    search.advance(20)
    search.observe((person.choice,))
    search.advance(elapsed - 20)
    survey.save_survey(
        runner.config, day_key=game_day(runner.wall_clock()), own_rank=100,
        search=search, identities={person.choice.opponent_id: asdict(person)},
        status=status, now=runner.wall_clock().timestamp())
    return search


def stronger_menu(**kwargs):
    kwargs.setdefault("sampled_ranks", (51, 61, 71))
    return frame(observed("new-a", rank=51, level=85),
                 observed("new-b", rank=61, level=86),
                 observed("new-c", rank=71, level=87),
                 **kwargs)


def assert_no_battle(runner):
    saved = state.read_state(runner.config)
    assert saved["pending"] is None and saved["attempts"] == {}
    assert saved["last_tickets"] == 5
    assert runner.inputs[-1:] == [((1237, 23), "Return home from Tactical Challenge battles")]


def test_unfinished_search_resumes_time_scores_and_counters_across_queue_visits(runner):
    current = stronger_menu()

    def refresh(previous):
        runner.active_seconds += 100
        runner.refresh_count += 1
        runner.last_refresh_acknowledged = True
        return current

    runner.refresh = refresh
    assert runner.run_menu(frame(observed("best"))) == "deferred"
    first = load(runner)
    assert first["search"].elapsed_seconds == 300
    assert first["search"].refreshes == 3
    assert set(first["search"].scores) == {"best", "new-a", "new-b", "new-c"}
    assert_no_battle(runner)
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp())

    next_visit(runner, queue_seconds=1800)
    assert survey.continuation_due(runner.config, now=runner.now.timestamp())
    assert runner.run_menu(current) == "success"
    resumed = load(runner)
    assert resumed["search"].elapsed_seconds == 600
    assert resumed["search"].refreshes == 6
    assert resumed["search"].observations == 7  # Initial list recorded only once.
    assert resumed["created_at"] == first["created_at"]
    assert resumed["search"].scores == first["search"].scores
    assert_no_battle(runner)
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp() + 61)


def test_exhausted_search_survives_restart_without_another_refresh_allowance(runner):
    checkpoint(runner, elapsed=600)
    next_visit(runner, queue_seconds=5 * 3600)
    runner.refresh = lambda f: pytest.fail("Exhausted allowance cannot refresh again")
    assert runner.run_menu(stronger_menu()) == "success"
    assert load(runner)["search"].exhausted
    assert_no_battle(runner)
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp() + 61)


def test_rank_changes_between_visits_never_renew_unspent_ticket_search(runner):
    checkpoint(runner, elapsed=500)
    created = load(runner)["created_at"]
    next_visit(runner)
    current = stronger_menu(rank=101)

    def refresh(previous):
        runner.active_seconds += 25
        runner.refresh_count += 1
        runner.last_refresh_acknowledged = True
        return current

    runner.refresh = refresh
    assert runner.run_menu(current) == "success"
    assert runner.refresh_count == 4
    saved = load(runner)
    assert saved["search"].exhausted
    assert saved["created_at"] == created
    assert saved["own_rank"] == 101
    assert_no_battle(runner)

    next_visit(runner)
    runner.refresh = lambda f: pytest.fail("Another defensive rank loss cannot restore time")
    assert runner.run_menu(stronger_menu(rank=102)) == "success"
    assert load(runner)["search"].exhausted
    assert_no_battle(runner)


def test_first_place_visit_preserves_prior_budget_if_rank_later_changes(runner):
    checkpoint(runner, elapsed=600)
    next_visit(runner)
    runner.refresh = lambda f: pytest.fail("No refresh at first place or with an exhausted budget")
    assert runner.run_menu(stronger_menu(rank=1)) == "success"
    assert load(runner)["search"].exhausted
    assert load(runner)["status"] == "blocked"
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp() + 61)
    next_visit(runner)
    assert runner.run_menu(stronger_menu(rank=102)) == "success"
    assert load(runner)["search"].exhausted
    assert_no_battle(runner)


def test_resumed_search_does_not_count_current_list_as_a_second_initial_observation(runner):
    checkpoint(runner)
    next_visit(runner)
    runner.last_refresh_acknowledged = False
    runner.refresh = lambda f: f
    assert runner.run_menu(stronger_menu()) == "deferred"
    saved = load(runner)["search"]
    assert saved.observations == 2
    assert saved.refreshes == 1
    assert saved.elapsed_seconds == 100
    assert set(saved.scores) == {"best"}


def test_blocked_retry_resumes_remaining_budget_and_can_reactivate_continuation(runner):
    checkpoint(runner, elapsed=150, status="blocked")
    next_visit(runner, queue_seconds=900)
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp())
    runner.last_refresh_acknowledged = False
    runner.refresh = lambda f: f
    assert runner.run_menu(stronger_menu()) == "deferred"
    saved = load(runner)
    assert saved["status"] == "active"
    assert saved["search"].elapsed_seconds == 150
    assert survey.continuation_due(runner.config, now=runner.now.timestamp() + 61)


def test_unreadable_timer_before_input_preserves_checkpoint_for_continuation(runner):
    original = checkpoint(runner)
    current = stronger_menu(refresh_seconds=None)
    runner.menu = lambda: pytest.fail("No refresh should be sent")
    assert runner.run_menu(current) == "deferred"
    saved = load(runner)
    assert saved["search"].to_dict() == original.to_dict()
    assert saved["status"] == "active"
    assert runner.defer_survey and not runner.last_refresh_sent
    assert_no_battle(runner)


@pytest.mark.parametrize("changed", [False, True], ids=["identical-list", "changed-list"])
def test_sent_refresh_without_timer_ack_blocks_same_budget_and_preserves_history(runner, changed):
    checkpoint(runner, elapsed=125)
    current = stronger_menu()
    unresolved = stronger_menu(captured_at=5, refresh_seconds=None)
    if changed:
        unresolved.screen.sampled_ranks = (52, 62, 72)
    runner.menu = lambda: unresolved
    with pytest.raises(RuntimeError, match="refresh was sent.*timer reset"):
        runner.run_menu(current)
    saved = load(runner)
    assert saved["status"] == "blocked"
    assert saved["search"].elapsed_seconds > 125
    assert saved["search"].refreshes == 1  # Unverified draw is never observed.
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp() + 61)
    assert runner.last_refresh_sent and not runner.last_refresh_acknowledged
    assert state.read_state(runner.config)["pending"] is None
    assert len(runner.inputs) == 1 and runner.refresh_count == 1


@pytest.mark.parametrize("failure_at", ["tap", "read"])
def test_transport_failure_preserves_consumed_budget_and_blocks_search(runner, failure_at):
    checkpoint(runner, elapsed=130)
    current = stronger_menu()
    def broken(*args):
        runner.active_seconds += 3
        raise RuntimeError("transport or screen read failed")
    if failure_at == "tap":
        runner.tap = broken
    else:
        runner.menu = broken
    with pytest.raises(RuntimeError, match="transport or screen read failed"):
        runner.run_menu(current)
    saved = load(runner)
    assert saved["status"] == "blocked"
    assert saved["search"].elapsed_seconds >= 133
    assert state.read_state(runner.config)["pending"] is None
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp() + 900)


def test_incomplete_acknowledged_rank_labels_preserve_budget_but_block_search(runner):
    checkpoint(runner, elapsed=120)
    current = stronger_menu(sampled_ranks=(51, 61))
    def refresh(previous):
        runner.active_seconds += 7
        runner.last_refresh_acknowledged = True
        return current
    runner.refresh = refresh
    with pytest.raises(RuntimeError, match="Incomplete opponent rank labels"):
        runner.run_menu(stronger_menu())
    saved = load(runner)
    assert saved["status"] == "blocked"
    assert saved["search"].elapsed_seconds == 127
    assert saved["search"].refreshes == 1
    assert state.read_state(runner.config)["pending"] is None


@pytest.mark.parametrize("guard", ["pending", "blocked", "reserve"])
def test_saved_progress_cannot_bypass_spending_history_or_reserve(runner, guard):
    checkpoint(runner)
    day = game_day(runner.now)
    state.observe_ladder(runner.config, day, tickets=5, rank=100, now=runner.now)
    runner.refresh = lambda f: pytest.fail("No scouting may bypass a spending guard")
    if guard == "pending":
        state.begin_battle(runner.config, day, "previous", 5, 100, now=runner.now)
    elif guard == "blocked":
        state.block(runner.config, "Unresolved result", now=runner.now)
    if guard == "reserve":
        assert runner.run_menu(stronger_menu(tickets=1)) == "success"
    else:
        with pytest.raises(RuntimeError):
            runner.run_menu(stronger_menu())
    assert runner.refresh_count == 0


def test_stale_evidence_blocks_without_renewing_consumed_search_budget(runner):
    checkpoint(runner, elapsed=150)
    next_visit(runner, queue_seconds=5 * 3600)
    runner.refresh = lambda f: pytest.fail("Stale search must not refresh or reset its time")
    with pytest.raises(RuntimeError, match="expired"):
        runner.run_menu(stronger_menu())
    saved = load(runner)
    assert saved["status"] == "blocked"
    assert saved["search"].elapsed_seconds == 150
    assert not survey.continuation_due(runner.config, now=runner.now.timestamp() + 61)
    assert_no_battle(runner)
