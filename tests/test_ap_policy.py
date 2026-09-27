from dataclasses import replace
import pytest
from ba_automator.ap_policy import (
    choose_hard, default_order, sweep_count, stage_key, sweep_refill,
)
from ba_automator.config import Config, ConfigError
from ba_automator.ap_state import read_state, write_state, state_path


@pytest.mark.parametrize(
    "ap,floor,cost,expected",
    [
        (461, 100, 20, 18),
        (119, 100, 20, 0),
        (120, 100, 20, 1),
        (99, 100, 20, 0),
        (426, 100, 35, 9),
        (100, 0, 40, 2),
    ],
)
def test_floor_never_rounds_up(ap, floor, cost, expected):
    assert sweep_count(ap, floor, cost) == expected
    assert ap - sweep_count(ap, floor, cost) * cost >= min(ap, floor)
    assert sweep_count(ap, floor, cost, 1) <= 1


@pytest.mark.parametrize(
    "values",
    [(100, 0, 0), (100, -1, 20), (None, 100, 20), (True, 100, 20), (100, 0, 1.2)],
)
def test_unknown_or_invalid_budget_stops(values):
    with pytest.raises(ValueError):
        sweep_count(*values)


@pytest.mark.parametrize("before_capacity,after_capacity", [
    (218, 218), (None, None), (218, None), (None, 218),
])
@pytest.mark.parametrize("observed_ap,expected_refill", [
    (136, 0), (137, 0), (135, None), (138, None), (356, None),
])
def test_sweep_refill_normal_balance_allows_one_regeneration_tick(
    before_capacity, after_capacity, observed_ap, expected_refill,
):
    assert sweep_refill(136, observed_ap, before_capacity, after_capacity) == expected_refill


@pytest.mark.parametrize("observed_ap", [356, 357])
def test_sweep_refill_proves_single_level_from_both_capacities(observed_ap):
    assert sweep_refill(136, observed_ap, 218, 220) == 220


@pytest.mark.parametrize("observed_ap,before_capacity,after_capacity", [
    (136, 218, 220),  # Capacity changed without the matching refill.
    (354, 218, 220),  # The old capacity is not the awarded amount.
    (355, 218, 220),
    (358, 218, 220),  # More than one unproven regeneration tick.
    (356, 220, 218),
    (356, 218, 219),
    (356, 218, 221),
    (356, None, 220),
    (356, 218, None),
])
def test_sweep_refill_cannot_explain_wrong_balance_or_capacity(
    observed_ap, before_capacity, after_capacity,
):
    assert sweep_refill(136, observed_ap, before_capacity, after_capacity) is None


@pytest.mark.parametrize("observed_ap", [358, 578, 579])
def test_sweep_refill_keeps_multiple_level_ups_unresolved(observed_ap):
    # A +4 capacity jump could entail multiple rewards. Neither the final
    # capacity nor a guessed sum of intervening capacities proves the receipt.
    assert sweep_refill(136, observed_ap, 218, 222) is None


@pytest.mark.parametrize("expected_ap,observed_ap", [
    (None, 136), (136, None), (-1, 136), (136, -1),
    (True, 136), (136, True), (136.0, 136), (136, "136"),
])
def test_sweep_refill_rejects_invalid_balances(expected_ap, observed_ap):
    assert sweep_refill(expected_ap, observed_ap, 218, 218) is None


@pytest.mark.parametrize("capacity", [0, -1, True, 218.0, "218"])
def test_sweep_refill_rejects_invalid_known_capacities(capacity):
    assert sweep_refill(136, 136, capacity, capacity) is None
    assert sweep_refill(136, 136, capacity, None) is None
    assert sweep_refill(136, 136, None, capacity) is None


def test_numeric_descending_order_and_rotation_continuity():
    order = default_order(["1-1", "13-3", "9-2", "13-1"])
    assert order == ("13-3", "13-1", "9-2", "1-1")
    first = choose_hard(order, None)
    assert (first.stage, first.next_stage) == ("13-3", "13-1")
    assert choose_hard(order, first.next_stage).stage == "13-1"
    assert choose_hard(order, "1-1").next_stage == "13-3"
    assert choose_hard(order, "13-1", {"13-1", "9-2"}).stage == "1-1"
    assert choose_hard(order, None, set(order)) is None
    assert choose_hard([], None) is None


def test_configuration_and_durable_state(tmp_path):
    c = Config(
        serial="127.0.0.1:5695", package="com.nexon.bluearchive", state_dir=tmp_path
    )
    assert c.ap_floor == 100 and c.ap_strategy == "elephs" and not c.ap_schedule_enabled
    for changes in (
        {"ap_floor": -1},
        {"ap_floor": True},
        {"ap_strategy": "random"},
        {"ap_hard_order": ["1-4"]},
        {"ap_hard_order": ["1-1", "1-1"]},
        {"ap_schedule_enabled": 1},
    ):
        with pytest.raises(ConfigError):
            replace(c, **changes)
    s = read_state(c)
    s["hard_stages"] = ["13-3", "1-1"]
    s["next_stage"] = "1-1"
    s["pending"] = {
        "strategy": "elephs",
        "stage": "13-3",
        "ap_before": 200,
        "floor": 100,
        "cost": 20,
        "count": 1,
    }
    write_state(c, s)
    assert read_state(c) == s
    assert read_state(replace(c, serial="127.0.0.1:5675"))["pending"] is None
    state_path(c).write_text("{")
    with pytest.raises(RuntimeError):
        read_state(c)
