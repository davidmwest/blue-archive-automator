"""Runtime checkpoints yield the queue without losing verified search evidence."""

from dataclasses import asdict
from datetime import timedelta

import pytest

from ba_automator import tactical_state as state
from ba_automator import tactical_survey as survey
from ba_automator.club import game_day
from ba_automator.tactical_refresh import RefreshPlanner
from test_tactical_runtime import frame, observed, runner as basic_runner  # noqa: F401


@pytest.fixture
def runner(basic_runner, monkeypatch):
    runner = basic_runner
    monkeypatch.setattr("ba_automator.tactical_runtime.RefreshPlanner", RefreshPlanner)
    runner.config.tactical_battles_confidence_percent = 99
    runner.config.tactical_battles_refresh_limit = runner.refresh_limit
    runner.config.tactical_battles_preserve_tickets = runner.reserve
    runner.battle = lambda *a: pytest.fail("An unfinished search must not enter a battle")
    return runner


def load(runner):
    return survey.load_survey(
        runner.config, day_key=game_day(runner.wall_clock()), own_rank=100,
        confidence=runner.confidence, pilot=runner.refresh_limit,
        now=runner.wall_clock().timestamp())


def next_visit(runner):
    """Forget in-memory observations, retaining only the real files on disk."""
    now = runner.wall_clock() + timedelta(seconds=61)
    runner.wall_clock = lambda: now
    runner.observed = {}
    runner.survey_candidates = {}
    runner.survey_resume = None
    runner.lookup_progress = None
    runner.refresh_count = runner.actions = 0
    runner.inputs.clear()
    runner.clock = lambda: 0


def checkpoint(runner, *, completed=True, lookup=None):
    planner = RefreshPlanner(pilot=runner.refresh_limit, confidence=runner.confidence)
    if completed:
        for index in range(1000):
            plan = planner.observe((50 + index % 2, 60 + index % 2, 70 + index % 2))
            if plan.status == "complete":
                break
        assert plan.status == "complete"
    else:
        planner.observe((50, 60, 70))
    candidates = (observed("best", rank=50, level=60),
                  observed("middle", rank=60, level=75),
                  observed("last", rank=70, level=80))
    survey.save_survey(
        runner.config, day_key=game_day(runner.wall_clock()), own_rank=100,
        confidence=runner.confidence, pilot=runner.refresh_limit, planner=planner,
        candidates=[asdict(p.choice) for p in candidates],
        identities={p.choice.opponent_id: asdict(p) for p in candidates},
        lookup=lookup, now=runner.wall_clock().timestamp())
    return planner, candidates


def menu_without_selected():
    return frame(rank=100, tickets=5, cooldown=0, all_ahead=True,
                 sampled_ranks=(51, 61, 71),
                 opponents=(observed("new", rank=51, level=85),))


def assert_no_battle(runner):
    saved = state.read_state(runner.config)
    assert saved["pending"] is None and saved["attempts"] == {}
    assert saved["last_tickets"] == 5
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]


def test_unfinished_survey_yields_then_resumes_verified_draws_and_candidates(runner):
    initial = frame(rank=100, tickets=5, all_ahead=True,
                    opponents=(observed("best", rank=50, level=60),),
                    sampled_ranks=(50, 60, 70))
    refreshed = menu_without_selected()

    def one_refresh_then_yield(previous):
        runner.refresh_count = 100
        runner.last_refresh_acknowledged = True
        return refreshed

    runner.refresh = one_refresh_then_yield
    assert runner.run_menu(initial) == "done"
    first = load(runner)
    assert first["planner"].valid_draws == 1
    assert first["planner"].plan().status != "complete"
    assert {p["opponent_id"] for p in first["candidates"]} == {"best", "new"}
    assert_no_battle(runner)
    assert not survey.continuation_due(runner.config, now=runner.wall_clock().timestamp())

    next_visit(runner)
    assert survey.continuation_due(runner.config, now=runner.wall_clock().timestamp())
    assert runner.run_menu(refreshed) == "done"
    resumed = load(runner)
    assert resumed["planner"].valid_draws == 2
    assert resumed["planner"].to_dict()["draws"] == [[51, 61, 71], [51, 61, 71]]
    assert resumed["created_at"] == first["created_at"]
    assert resumed["updated_at"] > first["updated_at"]
    assert {p["opponent_id"] for p in resumed["candidates"]} == {"best", "new"}
    assert_no_battle(runner)


def test_completed_survey_restores_weakest_candidate_missing_from_current_menu(runner):
    planner, candidates = checkpoint(runner, lookup={"opponent_id": "best", "valid_draws": 3})
    runner.survey_resume = load(runner)
    runner.restore_identities(runner.survey_resume)
    runner.refresh = lambda f: pytest.fail("Completed coverage must not restart from zero")
    current, ranked = runner.scout(menu_without_selected())
    assert ranked[0] == candidates[0].choice
    assert {p.opponent_id for p in ranked} == {"best", "middle", "last", "new"}
    assert runner.refresh_planner.to_dict() == planner.to_dict()
    assert runner.lookup_progress == {"opponent_id": "best", "valid_draws": 3}
    assert runner.inputs == []


def test_target_lookup_preserves_counter_across_two_queue_visits(runner):
    planner, _ = checkpoint(runner, lookup={"opponent_id": "best", "valid_draws": 3})
    current = menu_without_selected()

    def one_refresh_then_yield(previous):
        runner.refresh_count = 100
        runner.last_refresh_acknowledged = True
        return current

    runner.refresh = one_refresh_then_yield
    for expected in (4, 5):
        assert runner.run_menu(current) == "done"
        saved = load(runner)
        assert saved["lookup"] == {"opponent_id": "best", "valid_draws": expected}
        assert saved["planner"].to_dict() == planner.to_dict()
        assert_no_battle(runner)
        next_visit(runner)


def test_resumed_lookup_keeps_original_choice_among_equal_scores(runner, monkeypatch):
    planner, candidates = checkpoint(runner, lookup={"opponent_id": "best", "valid_draws": 3})
    saved = load(runner)
    tied = observed("tie", rank=51, level=60)
    saved["candidates"].append(asdict(tied.choice))
    saved["identities"]["tie"] = asdict(tied)
    survey.save_survey(
        runner.config, day_key=saved["day_key"], own_rank=100, confidence=.99,
        pilot=runner.refresh_limit, planner=planner, candidates=saved["candidates"],
        identities=saved["identities"], lookup=saved["lookup"],
        now=runner.wall_clock().timestamp())
    monkeypatch.setattr("ba_automator.tactical_runtime.choose_opponent", lambda *a: tied.choice)
    chosen = []

    def locate(current, opponent):
        chosen.append(opponent)
        runner.defer_survey = True
        return current, None

    runner.locate = locate
    assert runner.run_menu(menu_without_selected()) == "done"
    assert chosen == [candidates[0].choice]
    assert load(runner)["lookup"] == {"opponent_id": "best", "valid_draws": 3}
    assert_no_battle(runner)


@pytest.mark.parametrize("failure", ["unacknowledged", "unreadable_ranks"])
def test_invalid_resumed_draw_discards_checkpoint_without_selective_sample_omission(runner, failure):
    checkpoint(runner, completed=False)
    current = menu_without_selected()
    if failure == "unreadable_ranks":
        current.screen.sampled_ranks = (51, 61)
    runner.last_refresh_acknowledged = failure != "unacknowledged"
    runner.refresh = lambda f: current
    assert runner.run_menu(current) == "done"
    assert runner.refresh_planner.valid_draws == 1
    assert not survey.survey_path(runner.config).exists()
    assert_no_battle(runner)
