"""Total Assault reward visits recognize claims without exposing battle inputs."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from ba_automator import assault_rewards
from ba_automator.assault_rewards import (
    AssaultRewardScreen,
    AssaultRewardsRunner,
    AssaultRewardVision,
    classify_rewards,
)
from ba_automator.config import Config
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame, ShopRunner
from ba_automator.vision import StartupVision, Word, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


def png(name):
    return (FIXTURES / f"assault-rewards-{name}.png").read_bytes()


def observed(name):
    words = json.loads((FIXTURES / f"assault-rewards-{name}.json").read_text())
    return decode_frame(png(name)), [
        Word(word["text"], word["confidence"], tuple(word["box"])) for word in words
    ]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("menu", AssaultRewardScreen("menu", (1180, 655), tickets=0)),
        ("panel", AssaultRewardScreen("rank_info", (915, 170))),
        ("rank", AssaultRewardScreen("rewards", tab="rank", claimable=False)),
        ("points", AssaultRewardScreen("rewards", (1056, 578), tab="points", claimable=True)),
    ],
)
def test_sanitized_live_reward_screens(name, expected):
    assert classify_rewards(*observed(name)) == expected


@pytest.fixture(scope="module")
def startup():
    return StartupVision()


@pytest.mark.parametrize("name", ["menu", "panel", "rank", "points"])
def test_live_fixtures_survive_local_ocr(startup, name):
    assert AssaultRewardVision(startup).analyze(png(name)) == classify_rewards(*observed(name))


@pytest.mark.parametrize("name", ["rank", "points"])
@pytest.mark.parametrize(
    "missing",
    ["total assault info", "detailed rank info", "reward details", "rank reward",
     "total points rewards", "reward claim period", "claim"],
)
def test_reward_controls_require_complete_modal_identity(name, missing):
    image, words = observed(name)
    words = [word for word in words if word.normalized != missing]
    assert classify_rewards(image, words).kind == "unknown"


@pytest.mark.parametrize("name", ["rank", "points"])
def test_shading_resolution_and_unreadable_claim_color_fail_closed(name):
    image, words = observed(name)
    assert classify_rewards((image * 0.5).astype("uint8"), words).kind == "unknown"
    assert classify_rewards(image[:600], words).kind == "unknown"
    image[552:605, 949:1158] = (160, 90, 10)
    assert classify_rewards(image, words).kind == "unknown"


@pytest.mark.parametrize("selection", ["neither", "both", "wrong_body"])
def test_ambiguous_or_mismatched_selected_tab_is_not_actionable(selection):
    image, words = observed("rank")
    if selection == "neither":
        image[220:260, 102:117] = 255
    elif selection == "both":
        image[288:328, 102:117] = image[220:260, 102:117]
    else:
        image[288:328, 102:117] = image[220:260, 102:117]
        image[220:260, 102:117] = 255
    assert classify_rewards(image, words).kind == "unknown"


@pytest.fixture
def runner(tmp_path, monkeypatch):
    config = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        run_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    result = AssaultRewardsRunner(
        config, None, None, vision=object(), monotonic=lambda: 0, sleep=lambda _: None
    )
    result.home = lambda: None

    def inspected(runner, view, path):
        path.with_suffix(".loot.json").write_text(json.dumps({
            "items": list(view.screen.items), "items_complete": True,
        }))
        return view

    monkeypatch.setattr(assault_rewards, "inspect_receipt", inspected)
    try:
        yield result
    finally:
        result.journal.close()


def frame(runner, screen):
    return ShopFrame(Capture(png("menu"), 0, runner.config.package), screen)


def menu(runner, tickets=0):
    return frame(runner, AssaultRewardScreen("menu", (1180, 655), tickets=tickets))


def reward(runner, tab, claimable=False):
    return frame(runner, AssaultRewardScreen(
        "rewards", (1056, 578) if claimable else None, tab=tab, claimable=claimable
    ))


def receipt(runner, quantity=10):
    return frame(runner, AssaultRewardScreen(
        "receipt", (640, 631), items=({"name": "Pyroxenes", "quantity": quantity},)
    ))


def views(runner, sequence):
    sequence = iter(sequence)

    def wait(kinds, *, predicate=lambda screen: True, **kwargs):
        result = next(sequence)
        assert result.screen.kind in ({kinds} if isinstance(kinds, str) else kinds)
        assert predicate(result.screen)
        return result

    runner.wait = wait
    taps = []
    runner.tap = lambda view, target, detail: taps.append((target, detail))
    return taps


def actions(runner):
    path = runner.config.state_dir / "important-actions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def events(runner):
    return [json.loads(line) for line in (runner.run_dir / "events.jsonl").read_text().splitlines()]


def test_budget_allows_two_long_receipts_and_navigation_without_relaxing_other_jobs(runner):
    # Two 900-second/160-input receipt allowances still leave room to close
    # both reward panels and verify home. Other ShopRunner tasks keep 600/200.
    runner.clock = lambda: 1900
    runner.actions = 330
    runner.budget()
    with pytest.raises(TaskError, match="time or input limit"):
        ShopRunner.budget(runner)


@pytest.mark.parametrize("elapsed,inputs", [(1920, 399), (1919.9, 399)])
def test_budget_allows_final_input_before_either_limit(runner, elapsed, inputs):
    runner.clock = lambda: elapsed
    runner.actions = inputs
    runner.budget()


@pytest.mark.parametrize("elapsed,inputs", [(1920.1, 0), (0, 400)])
def test_budget_stops_when_either_bound_is_reached(runner, elapsed, inputs):
    runner.clock = lambda: elapsed
    runner.actions = inputs
    with pytest.raises(TaskError, match="assault_rewards reached its time or input limit"):
        runner.budget()


def test_disabled_tabs_never_claim_and_zero_tickets_are_sufficient(runner, monkeypatch):
    monkeypatch.setattr(assault_rewards, "inspect_receipt", lambda *args: pytest.fail("No claim expected"))
    taps = views(runner, [
        frame(runner, AssaultRewardScreen("rank_info", (915, 170))),
        reward(runner, "rank"), reward(runner, "points"), menu(runner),
    ])
    assert runner.collect(menu(runner)).status == "success"
    assert [target for target, _ in taps] == [
        (1180, 655), (915, 170), (240, 308), (1166, 107), (1237, 23)
    ]
    assert actions(runner) == []
    checked = next(event for event in events(runner) if event["event"] == "rewards_checked")
    assert checked["claimed"] == []
    assert checked["tickets_before"] == checked["tickets_after"] == 0


def test_each_category_claims_once_and_records_identified_receipts(runner, monkeypatch):
    def inspect(runner, view, path):
        path.with_suffix(".loot.json").write_text(json.dumps({
            "items": [{"name": "Pyroxenes", "quantity": 30}], "items_complete": True
        }))
        return view

    monkeypatch.setattr(assault_rewards, "inspect_receipt", inspect)
    taps = views(runner, [
        reward(runner, "rank", True), receipt(runner), reward(runner, "rank"),
        reward(runner, "points", True), receipt(runner), reward(runner, "points"),
        menu(runner, 3),
    ])
    assert runner.collect(menu(runner, 3)).status == "success"
    # Complete input allowlist: neither battle entry nor sweep is accessible.
    assert [target for target, _ in taps] == [
        (1180, 655), (1056, 578), (640, 631), (240, 308),
        (1056, 578), (640, 631), (1166, 107), (1237, 23),
    ]
    history = actions(runner)
    assert [action["action"] for action in history] == [
        "assault_reward_requested", "assault_rewards_received",
        "assault_reward_requested", "assault_rewards_received",
    ]
    received = [action for action in history if action["action"] == "assault_rewards_received"]
    assert [action["reward"] for action in received] == ["rank", "points"]
    assert all(action["items"] == [{"name": "Pyroxenes", "quantity": 30}] for action in received)
    assert all(action["items_complete"] and Path(action["evidence"]).is_file() for action in received)
    checked = next(event for event in events(runner) if event["event"] == "rewards_checked")
    assert checked["claimed"] == ["rank", "points"]
    assert checked["tickets_before"] == checked["tickets_after"] == 3


@pytest.mark.parametrize("claimable,target", [(None, None), (True, None)])
def test_unverified_claim_control_stops_without_claim(runner, claimable, target):
    uncertain = replace(reward(runner, "rank"), screen=AssaultRewardScreen(
        "rewards", target, tab="rank", claimable=claimable
    ))
    taps = views(runner, [uncertain])
    with pytest.raises(TaskError, match="Cannot verify"):
        runner.collect(menu(runner))
    assert [target for target, _ in taps] == [(1180, 655)]
    assert actions(runner) == []


def test_missing_receipt_never_retries_or_reports_reward_received(runner):
    taps = views(runner, [reward(runner, "rank", True)])
    normal_wait = runner.wait

    def wait(kinds, **kwargs):
        if kinds == "receipt":
            runner.fail("Receipt unavailable")
        return normal_wait(kinds, **kwargs)

    runner.wait = wait
    with pytest.raises(TaskError, match="Receipt unavailable"):
        runner.collect(menu(runner))
    assert [target for target, _ in taps] == [(1180, 655), (1056, 578)]
    assert [action["action"] for action in actions(runner)] == ["assault_reward_requested"]


def test_receipt_inspection_failure_stops_without_claim_retry(runner, monkeypatch):
    def unreadable(*args):
        runner.fail("Receipt details could not be read")

    monkeypatch.setattr(assault_rewards, "inspect_receipt", unreadable)
    taps = views(runner, [reward(runner, "rank", True), receipt(runner)])
    with pytest.raises(TaskError, match="Receipt details could not be read"):
        runner.collect(menu(runner))
    assert [target for target, _ in taps] == [(1180, 655), (1056, 578)]
    assert [action["action"] for action in actions(runner)] == ["assault_reward_requested"]
    assert (runner.run_dir / "rank-receipt.png").is_file()


@pytest.mark.parametrize("sidecar", [None, {"items": [], "items_complete": False}])
def test_incomplete_or_missing_receipt_details_remain_open(runner, monkeypatch, sidecar):
    def inspect(runner, view, path):
        if sidecar is not None:
            path.with_suffix(".loot.json").write_text(json.dumps(sidecar))
        return view

    monkeypatch.setattr(assault_rewards, "inspect_receipt", inspect)
    taps = views(runner, [reward(runner, "rank", True), receipt(runner)])
    with pytest.raises(TaskError, match="receipt is incomplete; left open"):
        runner.collect(menu(runner))
    assert [target for target, _ in taps] == [(1180, 655), (1056, 578)]
    assert [action["action"] for action in actions(runner)] == ["assault_reward_requested"]
    assert (runner.run_dir / "rank-receipt.png").is_file()


def test_claim_still_enabled_after_receipt_stops_before_any_repeat(runner):
    taps = views(runner, [
        reward(runner, "rank", True), receipt(runner), reward(runner, "rank", True),
    ])
    with pytest.raises(TaskError, match="no repeated claim"):
        runner.collect(menu(runner))
    assert [target for target, _ in taps] == [(1180, 655), (1056, 578), (640, 631)]
    assert len([action for action in actions(runner) if action["action"] == "assault_rewards_received"]) == 1


def test_missing_initial_ticket_balance_sends_no_input(runner):
    taps = views(runner, [])
    with pytest.raises(TaskError, match="could not read the ticket balance"):
        runner.collect(menu(runner, None))
    assert taps == []


@pytest.mark.parametrize("final_tickets", [2, None])
def test_ticket_balance_must_be_preserved_before_returning_home(runner, final_tickets):
    taps = views(runner, [reward(runner, "rank"), reward(runner, "points"), menu(runner, final_tickets)])
    with pytest.raises(TaskError, match="ticket balance changed"):
        runner.collect(menu(runner, 3))
    assert [target for target, _ in taps] == [(1180, 655), (240, 308), (1166, 107)]
    assert not any(event["event"] == "rewards_checked" for event in events(runner))


def test_run_navigates_only_to_reward_menu(runner):
    navigations = []
    destination = menu(runner)
    runner.navigate = lambda source, target, position: navigations.append((source, target, position)) or destination
    collected = []
    runner.collect = lambda view: collected.append(view) or "complete"
    assert runner.run() == "complete"
    assert navigations == [("home", "campaign", (1200, 641)), ("campaign", "menu", (906, 456))]
    assert collected == [destination]
