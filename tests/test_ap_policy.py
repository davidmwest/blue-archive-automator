from dataclasses import replace
import pytest
from ba_automator.ap_policy import choose_hard, default_order, sweep_count, stage_key
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
