"""Search relaxes one observed strength tier per acknowledged overtime refresh."""

import copy
import json
import random

import pytest

from ba_automator.tactical_battles import Opponent, opponent_score
from ba_automator.tactical_search import SearchPolicy, SearchState


def opponent(identity, level=60):
    return Opponent(identity, 100, level)


def benchmark(*, budget=100, levels=(40, 50, 60, 70, 80)):
    state = SearchState(SearchPolicy(budget))
    opponents = [opponent(f"person-{index}", level) for index, level in enumerate(levels)]
    state.observe(opponents[:3], refreshed=False)
    state.advance(10)
    state.observe(opponents[3:] or opponents[:1])
    return state, opponents


def test_default_is_ten_minutes_of_active_time():
    state = SearchState()
    assert state.policy.time_budget_seconds == 600
    assert state.policy.benchmark_seconds == 222
    assert state.remaining_seconds == 600
    assert not state.exhausted
    assert state.decide([]).phase == "benchmark"


def test_cannot_select_during_benchmark_even_for_weakest_current_opponent():
    state, candidates = benchmark()
    state.advance(26.9)
    decision = state.decide([candidates[0]])
    assert decision.opponent_id is None
    assert decision.phase == "benchmark"
    assert decision.can_refresh


def test_benchmark_boundary_accepts_equal_score_without_reacquisition():
    state, candidates = benchmark()
    state.advance(27)
    decision = state.decide([candidates[0]])
    assert decision.opponent_id == candidates[0].opponent_id
    assert decision.phase == "accepted"
    assert decision.threshold == opponent_score(candidates[0])
    assert decision.percentile == 0
    assert not decision.can_refresh


def test_one_initial_frame_or_missing_data_cannot_authorize_battle():
    state = SearchState(SearchPolicy(100))
    candidate = opponent("single", 30)
    state.observe([candidate], refreshed=False)
    state.advance(5)
    state.observe([])
    state.advance(95)
    decision = state.decide([candidate])
    assert state.observations == state.benchmark_observations == 1
    assert decision.threshold is None
    assert decision.phase == "overtime"
    assert decision.opponent_id is None
    assert decision.can_refresh


def test_late_observations_do_not_repair_missing_benchmark():
    state = SearchState(SearchPolicy(100))
    state.advance(40)
    for index in range(3):
        state.observe([opponent(str(index))])
    assert state.decide([opponent("current", 1)]).threshold is None
    assert not state.benchmark_ids


def test_threshold_relaxes_using_conservative_discrete_percentile():
    state, candidates = benchmark()
    state.advance(58.5)  # 68.5%: halfway through the selection interval.
    midway = state.decide([candidates[1]])
    assert midway.percentile == pytest.approx(.125)
    assert midway.threshold == opponent_score(candidates[0])
    assert midway.opponent_id is None
    state.advance(31.5)
    final = state.decide([candidates[1]])
    assert final.percentile == .25
    assert final.threshold == opponent_score(candidates[1])
    assert final.opponent_id == candidates[1].opponent_id
    assert not final.can_refresh


def test_strong_current_candidate_is_rejected_at_deadline_without_fallback():
    state, candidates = benchmark()
    state.advance(90)
    decision = state.decide([candidates[-1]])
    assert decision.phase == "overtime"
    assert decision.opponent_id is None
    assert decision.remaining_seconds == 0
    assert decision.can_refresh


def test_empty_current_list_does_not_select_remembered_best():
    state, _ = benchmark()
    state.advance(90)
    assert state.decide([]).opponent_id is None
    assert state.decide([]).phase == "overtime"


def test_new_weak_opponent_can_qualify_without_becoming_reference():
    state, _ = benchmark()
    reference_ids = set(state.benchmark_ids)
    state.advance(30)
    weak = opponent("new", 20)
    state.observe([weak])
    assert state.benchmark_ids == reference_ids
    assert state.decide([weak]).opponent_id == "new"


def test_repeated_identities_do_not_reweight_percentile_population():
    state, candidates = benchmark()
    for _ in range(50):
        state.observe([candidates[-1]])
    state.advance(90)
    assert len(state.scores) == len(candidates)
    assert state.decide(candidates[:3]).threshold == opponent_score(candidates[1])


def test_conflicting_readings_keep_maximum_score_for_reference_and_candidate():
    state, candidates = benchmark()
    identity = candidates[0].opponent_id
    state.observe([opponent(identity, 90)])
    state.observe([opponent(identity, 1)])
    state.advance(30)
    assert state.scores[identity] == 1080
    assert state.decide([opponent(identity, 1)]).opponent_id is None
    assert state.decide([candidates[1]]).opponent_id == candidates[1].opponent_id


def test_decision_rejects_a_newly_worse_current_reading_without_mutating_state():
    state, candidates = benchmark()
    state.advance(30)
    before = state.to_dict()
    assert state.decide([opponent(candidates[0].opponent_id, 90)]).opponent_id is None
    assert state.to_dict() == before


def test_only_weakest_qualifying_current_ties_are_randomized():
    state, _ = benchmark()
    state.advance(90)
    current = [opponent("a", 20), opponent("b", 20), opponent("c", 30)]
    selections = {state.decide(current, rng=random.Random(seed)).opponent_id for seed in range(20)}
    assert selections == {"a", "b"}
    assert state.decide(current).candidate_ids == ("a", "b")


def test_actual_refresh_estimate_uses_elapsed_active_time():
    state = SearchState(SearchPolicy(600))
    assert state.estimated_refreshes is None
    state.observe([opponent("a")], refreshed=False)
    for _ in range(5):
        state.advance(6)
        state.observe([opponent("a")])
    assert state.refreshes == 5
    assert state.observations == 6
    assert state.estimated_refreshes == 100


def test_elapsed_overrun_reaches_soft_threshold_without_forcing_a_match():
    state, _ = benchmark()
    state.advance(1000)
    assert state.elapsed_seconds == 100
    assert state.exhausted
    assert state.deadline_reached
    assert state.decide([]).can_refresh
    assert state.decide([]).opponent_id is None
    state.advance(1)
    assert state.elapsed_seconds == 100


def test_overtime_admits_next_observed_tier_per_acknowledged_refresh():
    state, candidates = benchmark(levels=(50, 55, 60))
    state.advance(90)
    assert state.decide([candidates[2]]).threshold == opponent_score(candidates[0])
    assert state.decide([candidates[2]]).opponent_id is None

    state.observe([candidates[2]])
    first = state.decide([candidates[2]])
    assert state.overtime_refreshes == 1
    assert first.threshold == opponent_score(candidates[1])
    assert first.opponent_id is None
    assert first.can_refresh

    state.observe([candidates[2]])
    second = state.decide([candidates[2]])
    assert state.overtime_refreshes == 2
    assert second.threshold == opponent_score(candidates[2])
    assert second.opponent_id == candidates[2].opponent_id
    assert not second.can_refresh


def test_overtime_starts_above_existing_percentile_and_counts_distinct_tiers():
    state, candidates = benchmark(levels=(40, 50, 50, 60, 70))
    state.advance(90)
    assert state.decide([]).threshold == opponent_score(candidates[1])
    state.observe([candidates[-1]])
    assert state.decide([]).threshold == opponent_score(candidates[3])
    state.observe([candidates[-1]])
    assert state.decide([candidates[-1]]).opponent_id == candidates[-1].opponent_id


def test_overtime_never_selects_a_remembered_opponent_absent_from_current_list():
    state, candidates = benchmark(levels=(50, 55, 60))
    state.advance(90)
    state.observe([])
    assert state.overtime_refreshes == 1
    assert state.decide([]).threshold == opponent_score(candidates[1])
    assert state.decide([]).candidate_ids == ()
    assert state.decide([]).opponent_id is None
    # Partial current lists are already filtered and verified by the caller.
    assert state.decide([candidates[2]]).opponent_id is None
    state.observe([candidates[2]])
    assert state.decide([candidates[2]]).candidate_ids == (candidates[2].opponent_id,)


def test_overtime_reads_and_queue_resumes_do_not_advance_threshold():
    state, candidates = benchmark(levels=(50, 55, 60))
    state.advance(90)
    state.observe([candidates[-1]])
    restored = SearchState.from_dict(json.loads(json.dumps(state.to_dict())))
    before = restored.to_dict()
    for _ in range(10):
        restored.advance(600)
        decision = restored.decide([candidates[-1]])
        assert decision.opponent_id is None
        assert decision.threshold == opponent_score(candidates[1])
    assert restored.to_dict() == before
    restored.observe([candidates[-1]])
    assert restored.overtime_refreshes == 2
    assert restored.decide([candidates[-1]]).opponent_id == candidates[-1].opponent_id


def test_new_low_scores_cannot_make_overtime_threshold_regress():
    state, candidates = benchmark(levels=(50, 55, 60))
    state.advance(90)
    state.observe([candidates[-1]])
    assert state.overtime_threshold == opponent_score(candidates[1])
    # Scores learned later may introduce tiers between earlier ones. The next
    # refresh must still advance from the persisted ceiling, not an array index.
    state.observe([opponent("new-low", 30), opponent("new-middle", 52)])
    assert state.overtime_threshold == opponent_score(candidates[-1])


def test_new_strong_overtime_candidate_is_not_accepted_until_its_tier_is_admitted():
    state, candidates = benchmark(levels=(50, 55, 60))
    state.advance(90)
    strong = opponent("new-strong", 90)
    state.observe([strong])
    assert state.decide([strong]).opponent_id is None
    state.observe([strong])
    assert state.decide([strong]).opponent_id is None
    state.observe([strong])
    assert state.overtime_threshold == opponent_score(strong)
    assert state.decide([strong]).opponent_id == strong.opponent_id
    assert state.decide([candidates[0]]).opponent_id == candidates[0].opponent_id


def test_overtime_missing_benchmark_requires_acknowledged_verified_observation():
    state = SearchState(SearchPolicy(100))
    state.observe([], refreshed=False)
    state.advance(100)
    candidate = opponent("verified-current", 60)
    assert state.decide([candidate]).threshold is None
    state.observe([])
    assert state.decide([candidate]).threshold is None
    state.observe([candidate])
    assert not state.benchmark_ids
    assert state.decide([candidate]).opponent_id == candidate.opponent_id
    assert SearchState.from_dict(state.to_dict()).to_dict() == state.to_dict()


def test_overtime_refreshes_do_not_inflate_within_budget_refresh_estimate():
    state, candidates = benchmark(levels=(50, 55, 60))
    state.advance(90)
    estimate = state.estimated_refreshes
    for _ in range(5):
        state.observe([candidates[-1]])
    assert state.refreshes == 6
    assert state.overtime_refreshes == 5
    assert state.estimated_refreshes == estimate == 1


def test_legacy_expired_search_migrates_without_resetting_time_or_skipping_tiers():
    state, candidates = benchmark(levels=(50, 55, 60))
    state.advance(90)
    encoded = state.to_dict()
    del encoded["overtime_refreshes"]
    del encoded["overtime_threshold"]
    encoded["version"] = 1
    restored = SearchState.from_dict(encoded)
    assert restored.elapsed_seconds == 100
    assert restored.refreshes == state.refreshes
    assert restored.scores == state.scores
    assert restored.overtime_refreshes == 0
    assert restored.overtime_threshold is None
    assert restored.decide([candidates[-1]]).opponent_id is None
    restored.observe([candidates[-1]])
    assert restored.overtime_refreshes == 1
    assert restored.overtime_threshold == opponent_score(candidates[1])
    assert restored.to_dict()["version"] == 2


def test_legacy_state_cannot_smuggle_an_overtime_authorization():
    state, _ = benchmark()
    encoded = state.to_dict()
    encoded["version"] = 1
    with pytest.raises(ValueError, match="legacy"):
        SearchState.from_dict(encoded)


def test_resume_preserves_threshold_counter_and_remaining_budget_without_wall_time():
    state, candidates = benchmark()
    state.advance(40)
    encoded = json.loads(json.dumps(state.to_dict()))
    restored = SearchState.from_dict(encoded)
    assert restored.to_dict() == state.to_dict()
    for seconds in (5, 10, 35):
        for search in (state, restored):
            search.advance(seconds)
            search.observe([candidates[0]])
        assert restored.decide(candidates[:3], rng=random.Random(1)) == state.decide(candidates[:3], rng=random.Random(1))


def test_serialization_does_not_share_mutable_state():
    state, _ = benchmark()
    encoded = state.to_dict()
    restored = SearchState.from_dict(encoded)
    encoded["scores"].clear()
    encoded["benchmark_ids"].clear()
    restored.scores["extra"] = 20
    assert "extra" not in state.scores
    assert state.benchmark_ids


@pytest.mark.parametrize("value", [0, -1, True, None, "600", float("inf"), float("nan"), 3601])
def test_invalid_budgets_are_rejected(value):
    with pytest.raises(ValueError):
        SearchPolicy(value)


@pytest.mark.parametrize("value", [-1, True, None, "1", float("inf"), float("nan")])
def test_invalid_time_increments_are_rejected(value):
    state = SearchState()
    with pytest.raises(ValueError):
        state.advance(value)
    assert state.elapsed_seconds == 0


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 3), ("elapsed_seconds", -1),
    ("elapsed_seconds", 101), ("elapsed_seconds", float("nan")),
    ("refreshes", True), ("refreshes", -1), ("refreshes", 10001),
    ("observations", 99), ("observations", 0),
    ("benchmark_observations", 99), ("benchmark_observations", 0),
    ("benchmark_ids", ["absent"]), ("benchmark_ids", [["nested"]]),
    ("benchmark_ids", ["person-0", "person-0"]),
    ("initial_observed", 1), ("scores", {"a": -1}),
    ("scores", {"a": float("inf")}), ("scores", {"a": True}),
    ("scores", {" ": 10}), ("scores", {}),
    ("policy", {"time_budget_seconds": 0}), ("policy", {}),
    ("overtime_refreshes", True), ("overtime_refreshes", -1),
    ("overtime_refreshes", 10001), ("overtime_refreshes", 1),
    ("overtime_threshold", True), ("overtime_threshold", 0),
    ("overtime_threshold", -1), ("overtime_threshold", float("nan")),
    ("overtime_threshold", 100),
])
def test_invalid_persisted_state_is_rejected(field, value):
    state, _ = benchmark()
    encoded = state.to_dict()
    encoded[field] = value
    with pytest.raises(ValueError):
        SearchState.from_dict(encoded)


def test_unknown_persisted_fields_cannot_add_cached_authorization():
    state, _ = benchmark()
    encoded = state.to_dict()
    encoded["accepted"] = True
    with pytest.raises(ValueError):
        SearchState.from_dict(encoded)


def test_empty_initial_observation_survives_roundtrip():
    state = SearchState()
    state.observe([], refreshed=False)
    assert SearchState.from_dict(state.to_dict()).to_dict() == state.to_dict()


def test_initial_observation_cannot_be_counted_twice():
    state = SearchState()
    state.observe([opponent("a")], refreshed=False)
    with pytest.raises(ValueError):
        state.observe([opponent("a")], refreshed=False)
    assert state.observations == 1


@pytest.mark.parametrize("current", [[None], [opponent("a")] * 2,
                                      [opponent(str(index)) for index in range(4)]])
def test_invalid_current_lists_are_rejected_without_mutation(current):
    state = SearchState()
    before = copy.deepcopy(state.to_dict())
    with pytest.raises(ValueError):
        state.observe(current)
    with pytest.raises(ValueError):
        state.decide(current)
    assert state.to_dict() == before


def test_identity_population_bound_prevents_unbounded_checkpoints():
    state = SearchState(SearchPolicy(3600))
    for index in range(1000):
        state.observe([opponent(str(index))])
    before = state.to_dict()
    with pytest.raises(ValueError):
        state.observe([opponent("extra")])
    assert state.to_dict() == before
