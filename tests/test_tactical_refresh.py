"""Exact small-population checks for the conditional opponent survey model."""

from collections import Counter
from itertools import product
import math

import pytest

from ba_automator.tactical_refresh import (
    DEFAULT_CONFIDENCE, MAX_CONFIDENCE, MIN_CONFIDENCE, RefreshPlanner,
    coverage_draws, lookup_draws, occupancy_cdf, pool_upper_bound,
)


@pytest.mark.parametrize("pool", range(1, 5))
@pytest.mark.parametrize("draws", range(6))
def test_occupancy_cdf_matches_exhaustive_uniform_samples(pool, draws):
    distribution = Counter(len(set(sample)) for sample in product(range(pool), repeat=draws))
    for distinct in range(draws + 2):
        expected = sum(count for size, count in distribution.items() if size <= distinct) / pool ** draws
        assert occupancy_cdf(pool, draws, distinct) == pytest.approx(expected)


@pytest.mark.parametrize("alpha", [.1, .2, .4])
def test_collision_probability_gives_known_two_draw_pool_bound(alpha):
    # Seeing the same position twice has probability exactly 1/N.
    upper = pool_upper_bound(2, 1, alpha)
    assert upper == math.floor(1 / alpha)
    assert occupancy_cdf(upper, 2, 1) >= alpha - 1e-12
    assert occupancy_cdf(upper + 1, 2, 1) < alpha


@pytest.mark.parametrize("draws,distinct", [(10, 2), (20, 8), (50, 25), (50, 49)])
def test_upper_bound_is_last_pool_not_rejected_by_exact_cdf(draws, distinct):
    upper = pool_upper_bound(draws, distinct, .005 / 18)
    assert upper >= distinct
    assert occupancy_cdf(upper, draws, distinct) >= .005 / 18
    assert occupancy_cdf(upper + 1, draws, distinct) < .005 / 18


def test_no_collisions_cannot_produce_finite_pool_upper_bound():
    assert pool_upper_bound(1, 1) is None
    assert pool_upper_bound(50, 50) is None


@pytest.mark.parametrize("pool,draws", [(2, 5), (3, 6), (5, 6)])
def test_exact_bound_has_stated_small_population_coverage(pool, draws):
    distribution = Counter(len(set(sample)) for sample in product(range(pool), repeat=draws))
    alpha = .1
    failure = 0
    for distinct, count in distribution.items():
        upper = pool_upper_bound(draws, distinct, alpha)
        if upper is not None and upper < pool:
            failure += count / pool ** draws
    assert failure <= alpha


@pytest.mark.parametrize("pool,seen", [(1, 0), (1, 1), (2, 0), (2, 1), (30, 10), (100, 99), (100, 100)])
def test_coverage_budget_is_minimal_for_the_future_union_bound(pool, seen):
    alpha = .0001
    draws = coverage_draws(pool, seen, alpha)
    failure = (pool - seen) * (1 - 1 / pool) ** draws
    assert failure <= alpha
    if draws:
        assert (pool - seen) * (1 - 1 / pool) ** (draws - 1) > alpha


@pytest.mark.parametrize("pool", [1, 2, 30, 100, 10000])
def test_lookup_budget_bounds_missing_one_chosen_position(pool):
    draws = lookup_draws(pool, .0025)
    assert (1 - 1 / pool) ** draws <= .0025
    assert (1 - 1 / pool) ** (draws - 1) > .0025


def test_single_position_per_row_completes_after_pilot_and_lookup_takes_one():
    planner = RefreshPlanner()
    for i in range(50):
        plan = planner.observe((100, 200, 300))
        assert plan.status == ("complete" if i == 49 else "pilot")
    assert [row.pool_upper for row in plan.row_estimates] == [1, 1, 1]
    assert plan.target_valid_draws == 50
    lookup = planner.lookup_plan(200)
    assert lookup.row == 1 and lookup.required_refreshes == 1 and lookup.within_limit
    assert planner.lookup_plan(500).required_refreshes is None
    with pytest.raises(ValueError, match="finished"):
        planner.observe((100, 200, 300))


def test_full_lists_with_unreadable_team_data_can_still_provide_rank_samples():
    # The planner intentionally accepts only rank labels, not chosen candidate
    # data, so level/portrait readability cannot filter its sampling pool.
    planner = RefreshPlanner(pilot=2, max_refreshes=8)
    assert planner.observe((100, 200, 300)).valid_draws == 1
    assert planner.observe((100, 200, 300)).valid_draws == 2


@pytest.mark.parametrize("ranks", [None, (), (1, 2), (1, 2, None), (1, 2, 2), (1, 2, True), (1, 2, 0), (1, 2, 3.0)])
def test_incomplete_or_invalid_list_counts_physical_refresh_but_no_statistical_draw(ranks):
    planner = RefreshPlanner(pilot=2, max_refreshes=4)
    assert planner.observe(ranks).physical_refreshes == 1
    assert planner.plan().valid_draws == 0
    assert planner.plan().row_estimates == ()
    for _ in range(3):
        plan = planner.observe(ranks)
    assert plan.status == "deferred" and plan.valid_draws == 0
    assert "no complete survey" in plan.reason
    assert not planner.lookup_plan(1).within_limit


def test_rows_are_fitted_separately_without_pooling_collisions():
    planner = RefreshPlanner(pilot=2, max_refreshes=8)
    for i in range(8):
        plan = planner.observe((1, 100 + i, 200 + i))
    assert plan.status == "deferred"
    assert plan.row_estimates[0].pool_upper is not None
    assert [row.pool_upper for row in plan.row_estimates[1:]] == [None, None]
    assert plan.target_valid_draws is None


def test_fixed_checkpoints_spend_error_budget_without_refitting_on_each_draw():
    planner = RefreshPlanner(pilot=4, max_refreshes=20)
    assert planner.checkpoints == (4, 8, 16, 20)
    for i in range(4):
        plan = planner.observe((1 + i % 3, 100 + i % 3, 200 + i % 3))
    estimates = plan.row_estimates
    for i in range(3):
        plan = planner.observe((1 + i % 3, 100 + i % 3, 200 + i % 3))
        assert plan.row_estimates == estimates
    assert planner.observe((1, 100, 200)).row_estimates != estimates


def test_contradicted_pool_bound_cannot_remain_a_stopping_certificate():
    planner = RefreshPlanner()
    for i in range(50):
        plan = planner.observe((1 + i % 10, 100 + i % 10, 200 + i % 10))
    previous_upper = plan.row_estimates[0].pool_upper
    assert previous_upper > 10
    for i in range(10, previous_upper + 1):
        plan = planner.observe((1 + i, 100 + i, 200 + i))
    assert plan.target_valid_draws is None and plan.status == "sampling"


def test_stratified_fixed_populations_complete_with_conservative_lookup_bound():
    planner = RefreshPlanner()
    for i in range(1000):
        plan = planner.observe((1 + i % 10, 100 + i % 20, 200 + i % 30))
        if plan.status == "complete":
            break
    assert plan.status == "complete"
    assert 50 <= plan.valid_draws <= 1000
    for row, population in zip(plan.row_estimates, (10, 20, 30)):
        assert row.pool_upper >= population
        assert row.pool_estimate == population
    for rank, population in ((1, 10), (100, 20), (200, 30)):
        lookup = planner.lookup_plan(rank)
        assert lookup.within_limit
        assert (1 - 1 / population) ** lookup.required_refreshes <= .0025


def test_lower_confidence_reduces_survey_and_relocation_targets_for_identical_samples():
    planners = [RefreshPlanner(confidence=value) for value in (.90, .99)]
    for i in range(50):
        for planner in planners:
            planner.observe((1 + i % 10, 100 + i % 20, 200 + i % 30))
    lower, higher = (planner.plan() for planner in planners)
    assert lower.target_valid_draws < higher.target_valid_draws
    assert all(low.pool_upper <= high.pool_upper
               for low, high in zip(lower.row_estimates, higher.row_estimates))

    # Fixed checkpoints can make actual completion coincide even when the
    # required forecast decreases; neither plan may claim early completion.
    for i in range(50, 1000):
        for planner in planners:
            if planner.plan().status != "complete":
                planner.observe((1 + i % 10, 100 + i % 20, 200 + i % 30))
        if all(planner.plan().status == "complete" for planner in planners):
            break
    assert all(planner.plan().status == "complete" for planner in planners)
    lower_lookup, higher_lookup = (planner.lookup_plan(200) for planner in planners)
    assert lower_lookup.required_refreshes < higher_lookup.required_refreshes
    assert lower_lookup.confidence == .90 and higher_lookup.confidence == .99
    for planner, lookup in zip(planners, (lower_lookup, higher_lookup)):
        pool = planner.plan().row_estimates[lookup.row].pool_upper
        assert (1 - 1 / pool) ** lookup.required_refreshes <= (1 - planner.confidence) / 4


@pytest.mark.parametrize("confidence", [MIN_CONFIDENCE, .85, .90, .95, DEFAULT_CONFIDENCE, MAX_CONFIDENCE])
def test_supported_confidences_are_preserved_without_rounding(confidence):
    planner = RefreshPlanner(confidence=confidence)
    assert planner.confidence == confidence
    assert planner.plan().confidence == confidence
    for _ in range(50):
        plan = planner.observe((100, 200, 300))
    assert plan.status == "complete"
    assert planner.lookup_plan(100).confidence == confidence


def test_default_confidence_is_99_percent_and_stricter_setting_cannot_bypass_cap():
    assert RefreshPlanner().confidence == DEFAULT_CONFIDENCE == .99
    planner = RefreshPlanner(pilot=4, max_refreshes=20, confidence=MAX_CONFIDENCE)
    for i in range(20):
        plan = planner.observe((1 + i, 100 + i, 200 + i))
    assert plan.status == "deferred" and plan.confidence == MAX_CONFIDENCE
    assert planner.lookup_plan(1).required_refreshes is None


def test_no_repeat_samples_reach_cap_without_false_confidence_claim():
    planner = RefreshPlanner(pilot=4, max_refreshes=20)
    for i in range(20):
        plan = planner.observe((1 + i, 100 + i, 200 + i))
    assert plan.status == "deferred" and plan.target_valid_draws is None
    assert planner.lookup_plan(1).required_refreshes is None


@pytest.mark.parametrize("kwargs", [
    {"pilot": 1}, {"pilot": True}, {"max_refreshes": 49},
    {"confidence": 0}, {"confidence": 1}, {"confidence": .7999},
    {"confidence": .9991}, {"confidence": True}, {"confidence": "90"},
    {"confidence": float("nan")}, {"confidence": float("inf")},
])
def test_bad_planner_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        RefreshPlanner(**kwargs)


@pytest.mark.parametrize("call,args", [
    (occupancy_cdf, (0, 1, 1)), (occupancy_cdf, (2, -1, 1)),
    (pool_upper_bound, (2, 3)), (pool_upper_bound, (2, True)),
    (coverage_draws, (2, 3, .1)), (coverage_draws, (2, 1, 0)),
    (lookup_draws, (True,)), (lookup_draws, (2, 1)),
])
def test_invalid_statistical_inputs_are_not_silently_coerced(call, args):
    with pytest.raises(ValueError):
        call(*args)


def test_serialization_replays_raw_draws_including_missing_observations():
    planner = RefreshPlanner(pilot=4, max_refreshes=20, confidence=.90)
    for ranks in ((100, 200, 300), None, (101, 201, 301), (100, 200, 300), (101, 201, 301)):
        planner.observe(ranks)
    saved = planner.to_dict()
    assert set(saved) == {"version", "pilot", "max_refreshes", "confidence", "draws"}
    assert saved["draws"][1] is None
    restored = RefreshPlanner.from_dict(saved)
    assert restored.plan() == planner.plan()
    for index in range(15):
        ranks = (100 + index % 2, 200 + index % 2, 300 + index % 2)
        assert restored.observe(ranks) == planner.observe(ranks)
        if planner.plan().status in ("complete", "deferred"):
            break
    saved["draws"][0][0] = 999
    assert planner.to_dict()["draws"][0][0] == 100
    assert restored.to_dict()["draws"][0][0] == 100


@pytest.mark.parametrize("edit", [
    lambda v: v.update(version=True),
    lambda v: v.update(complete=True),
    lambda v: v.update(max_refreshes=1001),
    lambda v: v.update(draws=[None] * 1001),
    lambda v: v.update(draws=[[1, 2, True]]),
    lambda v: v.update(draws=[[1, 1, 3]]),
    lambda v: v.update(draws=[[1, 2, 3, 4]]),
    lambda v: v.update(draws="123"),
])
def test_serialization_rejects_unbounded_or_tampered_history(edit):
    value = RefreshPlanner().to_dict()
    edit(value)
    with pytest.raises(ValueError):
        RefreshPlanner.from_dict(value)


def test_serialization_rejects_added_draw_after_completed_certificate():
    value = RefreshPlanner().to_dict()
    value["draws"] = [[100, 200, 300]] * 51
    with pytest.raises(ValueError, match="finished"):
        RefreshPlanner.from_dict(value)


def test_planner_history_has_a_hard_memory_bound():
    with pytest.raises(ValueError, match="1000"):
        RefreshPlanner(max_refreshes=1001)
    planner = RefreshPlanner()
    for _ in range(1000):
        planner.observe(None)
    assert len(planner.to_dict()["draws"]) == 1000
    assert RefreshPlanner.from_dict(planner.to_dict()).plan() == planner.plan()
    with pytest.raises(ValueError, match="finished"):
        planner.observe(None)
