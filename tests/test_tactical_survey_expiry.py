"""Expired search observations cannot authorize another battle or fresh budget."""

from datetime import timedelta

import pytest

from ba_automator import tactical_state as state, tactical_survey as survey
from ba_automator.tactical_search import SearchState
from test_tactical_runtime import battle_frames, frame, observed, runner  # noqa: F401


def approaching_expiry(runner):
    now = runner.wall_clock()
    runner.survey_created_at = now.timestamp() - survey.MAX_AGE_SECONDS + 1
    runner.search_state = SearchState(runner.search_policy)
    runner.search_state.advance(2)
    return now


def expire(runner, now):
    runner.wall_clock = lambda: now + timedelta(seconds=1)


def test_survey_expiry_mid_refresh_stops_without_claiming_a_result(runner):
    now = approaching_expiry(runner)
    runner.survey_resume = {"search": runner.search_state, "identities": {},
                            "created_at": runner.survey_created_at}
    current = frame(rank=100, tickets=5, all_ahead=True, opponents=(), sampled_ranks=(50, 60, 70))
    def refresh(previous):
        expire(runner, now)
        return current
    runner.refresh = refresh
    assert runner.scout(current) == (current, ())
    assert runner.survey_expired and runner.search_state.elapsed_seconds == 2
    assert not runner.defer_survey
    assert state.read_state(runner.config)["pending"] is None


def test_expired_search_stops_before_any_more_refreshes(runner):
    now = approaching_expiry(runner)
    expire(runner, now)
    runner.refresh = lambda f: pytest.fail("Expired evidence cannot refresh or enter")
    current = frame(rank=100, tickets=5, opponents=(observed("enemy"),))
    assert runner.scout(current) == (current, ())
    assert runner.survey_expired and runner.inputs == []


def test_expiry_before_entry_leaves_ticket_and_screen_untouched(runner):
    now = approaching_expiry(runner)
    expire(runner, now)
    current = frame(rank=100, tickets=5)
    assert runner.battle(current, observed("enemy")) == (current, None)
    assert runner.inputs == []
    assert state.read_state(runner.config)["pending"] is None


def test_expiry_during_formation_backs_out_before_durable_reservation(runner):
    now = approaching_expiry(runner)
    candidate = observed("enemy")
    detail, formation = battle_frames(candidate)
    screens = iter((detail, formation))
    runner.wait = lambda *a, **k: next(screens)
    def fill(current):
        expire(runner, now)
        return current
    runner.fill_attack_formation = fill
    returned = frame(rank=100, tickets=5)
    runner.menu = lambda: returned
    assert runner.battle(frame(rank=100, tickets=5), candidate) == (returned, None)
    assert runner.inputs[-1] == ((54, 33), "Leave formation after the opponent survey expired")
    assert not any("Mobilize" in label for _, label in runner.inputs)
    assert state.read_state(runner.config)["pending"] is None


def test_expired_selection_exits_visit_without_renewing_consumed_search_time(runner):
    now = runner.wall_clock()
    candidate = observed("enemy")
    runner.observed["enemy"] = candidate
    calls = []
    def scout(current):
        calls.append(1)
        runner.search_state = SearchState(runner.search_policy)
        runner.search_state.advance(2)
        runner.survey_created_at = now.timestamp() - survey.MAX_AGE_SECONDS
        return current, (candidate.choice,)
    runner.scout = scout
    current = frame(rank=100, tickets=5, opponents=(candidate,))
    with pytest.raises(RuntimeError, match="Saved opponent observations expired"):
        runner.run_menu(current)
    assert len(calls) == 1
    assert runner.inputs == [((1237, 23), "Return home from Tactical Challenge battles")]
    saved = survey.load_survey(runner.config, day_key="2026-09-26", own_rank=100,
                              time_budget_seconds=10, now=now.timestamp())
    assert saved["status"] == "blocked" and saved["search"].elapsed_seconds == 2
    assert state.read_state(runner.config)["pending"] is None
