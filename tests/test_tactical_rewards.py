"""Tactical reward claims stay on the two observed controls and preserve tickets."""

from dataclasses import replace
import json
from pathlib import Path
import cv2
import pytest
from ba_automator import loot
from ba_automator.config import Config
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame
from ba_automator.tactical_rewards import (
    TacticalRewardsRunner,
    TacticalScreen,
    TacticalVision,
    classify_tactical,
)
from ba_automator.tasks import task_plan
from ba_automator.vision import StartupVision, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


def png(name):
    return (FIXTURES / f"tactical-{name}.png").read_bytes()


@pytest.fixture(scope="module")
def startup():
    return StartupVision()


@pytest.mark.parametrize(
    "name,kind,claims",
    [
        ("time-ready", "tactical", (("time", (353, 388)),)),
        ("checked", "tactical", ()),
        ("time-receipt", "receipt", ()),
    ],
)
def test_sanitized_live_screens(startup, name, kind, claims):
    result = TacticalVision(startup).analyze(png(name))
    assert result.kind == kind and result.claims == claims
    if kind == "tactical":
        assert result.tickets == 5
    else:
        assert result.items == ({"name": "Credit Points", "quantity": 70570},)


def test_daily_claim_enabled_color_is_detected_independently_offline(startup):
    f = decode_frame(png("time-ready"))
    words = startup.read(f)
    # Synthetic control state, explicitly not evidence of a live daily claim.
    f[442:492, 310:400] = f[363:413, 310:400]
    result = classify_tactical(f, words)
    assert result.claims == (("time", (353, 388)), ("daily", (353, 467)))
    f[363:413, 310:400] = cv2.cvtColor(
        cv2.cvtColor(f[363:413, 310:400], cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR
    )
    assert classify_tactical(f, words).claims == (("daily", (353, 467)),)


def test_menu_requires_full_identity_tickets_and_unshaded_controls(startup):
    f = decode_frame(png("time-ready"))
    words = startup.read(f)
    for prefix in (
        "tactical challenge",
        "season",
        "tickets owned",
        "time reward",
        "daily reward",
        "claim",
    ):
        assert (
            classify_tactical(
                f, [w for w in words if not w.normalized.startswith(prefix)]
            ).kind
            == "unknown"
        )
    assert classify_tactical((f * 0.5).astype("uint8"), words).kind == "unknown"
    assert classify_tactical(f[:600], words).kind == "unknown"


@pytest.fixture
def runner(tmp_path):
    c = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        run_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    r = TacticalRewardsRunner(
        c, None, None, vision=object(), monotonic=lambda: 0, sleep=lambda _: None
    )
    r.home = lambda: None
    try:
        yield r
    finally:
        r.journal.close()


def frame(r, screen):
    return ShopFrame(Capture(png("time-ready"), 0, r.config.package), screen)


def menu(r, claims=(), tickets=5):
    return frame(r, TacticalScreen("tactical", claims=claims, tickets=tickets))


def receipt(r, name="Credit Points", quantity=60):
    return frame(
        r,
        TacticalScreen(
            "receipt", (640, 631), items=({"name": name, "quantity": quantity},)
        ),
    )


def test_each_reward_claims_once_even_when_time_credits_regenerate(runner):
    r = runner
    both = (("time", (353, 388)), ("daily", (353, 467)))
    views = iter(
        [
            receipt(r),
            menu(r, both),
            receipt(r, "Pyroxenes", 16),
            menu(r, (("time", (353, 388)),)),
        ]
    )
    r.wait = lambda *a, **k: next(views)
    taps = []
    r.tap = lambda f, t, d: taps.append(t)
    result = r.collect_menu(menu(r, both))
    assert result.status == "success"
    assert taps == [(353, 388), (640, 631), (353, 467), (640, 631), (1237, 23)]
    totals = {i["name"]: i["quantity"] for i in loot.snapshot(r.config)["items"]}
    assert totals == {"Credits": 60, "Pyroxenes": 16}
    events = [
        json.loads(x) for x in (r.run_dir / "events.jsonl").read_text().splitlines()
    ]
    checked = next(e for e in events if e["event"] == "rewards_checked")
    assert checked["tickets_before"] == checked["tickets_after"] == 5


def test_unavailable_rewards_only_return_home(runner):
    r = runner
    taps = []
    r.tap = lambda f, t, d: taps.append(t)
    r.wait = lambda *a, **k: pytest.fail("no claim should be attempted")
    assert r.collect_menu(menu(r)).status == "success"
    assert taps == [(1237, 23)] and loot.snapshot(r.config)["receipt_count"] == 0


def test_missing_receipt_does_not_retry_claim_or_report_loot(runner):
    r = runner
    taps = []
    r.tap = lambda f, t, d: taps.append(t)
    r.wait = lambda *a, **k: r.fail("Receipt unavailable")
    with pytest.raises(TaskError):
        r.collect_menu(menu(r, (("time", (353, 388)),)))
    assert taps == [(353, 388)] and loot.snapshot(r.config)["receipt_count"] == 0


@pytest.mark.parametrize("tickets,claims", [(4, ()), (5, (("daily", (353, 467)),))])
def test_ticket_change_or_unchanged_daily_claim_stops_before_more_input(
    runner, tickets, claims
):
    r = runner
    taps = []
    r.tap = lambda f, t, d: taps.append(t)
    views = iter([receipt(r, "Pyroxenes", 16), menu(r, claims, tickets)])
    r.wait = lambda *a, **k: next(views)
    with pytest.raises(TaskError):
        r.collect_menu(menu(r, (("daily", (353, 467)),)))
    assert taps == [(353, 467), (640, 631)]


def test_reward_job_is_in_daily_and_never_includes_battle_stub(runner):
    assert task_plan("tactical_rewards", runner.config) == (
        "restart",
        "tactical_rewards",
        "red_dots",
    )
    assert "tactical_rewards" in task_plan("daily", runner.config)
    assert "tactical_battles" not in task_plan("daily", runner.config)
