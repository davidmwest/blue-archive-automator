"""Observed red-dot navigation and the boundary between free and paid claims."""

from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import cv2
import pytest

from ba_automator import loot
from ba_automator.club import (
    ClubRunner,
    ClubScreen,
    ClubVision,
    classify_club,
    game_day,
    last_visit,
    save_visit,
    state_path,
)
from ba_automator.config import Config
from ba_automator.free_pack import (
    FreePackRunner,
    FreePackScreen,
    FreePackVision,
    classify_free_pack,
)
from ba_automator.home_badges import BadgeScreen, BadgeVision
from ba_automator.mail import MailRunner
from ba_automator.red_dots import RedDotsRunner
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame
from ba_automator.shop_vision import ShopScreen, ShopVision
from ba_automator.vision import StartupVision, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


def png(name):
    return (FIXTURES / f"red-dots-{name}.png").read_bytes()


@pytest.fixture(scope="module")
def startup():
    return StartupVision()


@pytest.mark.parametrize(
    "name,kind",
    [
        ("store", "store"),
        ("free-available", "free_available"),
        ("free-confirm", "free_confirm"),
        ("free-receipt", "receipt"),
        ("free-empty", "free_empty"),
    ],
)
def test_free_pack_live_fixtures(startup, name, kind):
    result = FreePackVision(startup).analyze(png(name))
    assert result.kind == kind
    if name == "free-receipt":
        assert result.items == (
            {"name": "Credit Points", "quantity": 10000},
            {"name": "AP", "quantity": 10},
        )


@pytest.mark.parametrize(
    "name,kind",
    [("social", "social"), ("club-reward", "club_reward"), ("club-page", "club")],
)
def test_club_live_fixtures(startup, name, kind):
    result = ClubVision(startup).analyze(png(name))
    assert result.kind == kind
    if kind == "social":
        assert result.red_dot


def test_real_badges_and_overlay_rejection(startup):
    assert BadgeVision(startup).analyze(png("home-badges")).badges == (
        "free_pack",
        "club",
        "mail",
    )
    for name in ("social", "store", "free-confirm", "club-reward"):
        assert BadgeVision(startup).analyze(png(name)).kind == "unknown"
    frame = decode_frame(png("home-badges"))
    frame[246:263, 178:194] = 0
    frame[671:688, 575:591] = 0
    frame[9:26, 1186:1202] = 0
    encoded = cv2.imencode(".png", frame)[1].tobytes()
    assert BadgeVision(startup).analyze(encoded).badges == ()
    assert ShopVision(startup).analyze(encoded).red_dot is False


def test_joined_reward_header_over_mail_is_recognized(startup):
    result = ShopVision(startup).analyze(png("mail-receipt"))
    assert result.kind == "receipt"
    assert result.items == ({"name": "AP", "quantity": 10},)


@pytest.mark.parametrize(
    "name,required",
    [
        (
            "free-available",
            ["Free Daily Pack", "Free", "Can purchase 1 time(s) a day", "Purchase"],
        ),
        ("free-confirm", ["Free Daily Pack", "Free", "Cost", "Confirm"]),
    ],
)
def test_free_price_and_identity_are_independent_requirements(startup, name, required):
    frame = decode_frame(png(name))
    words = startup.read(frame)
    for missing in required:
        assert classify_free_pack(
            frame, [w for w in words if w.text.strip() != missing]
        ).kind not in {"free_available", "free_confirm"}
    changed = [
        replace(w, text="USD 2.99") if w.text.strip() == "Free" else w for w in words
    ]
    assert classify_free_pack(frame, changed).kind not in {
        "free_available",
        "free_confirm",
    }
    assert classify_free_pack((frame * 0.5).astype("uint8"), words).kind == "unknown"
    assert classify_free_pack(frame[:600], words).kind == "unknown"


def test_dimmed_club_and_missing_receipt_details_cannot_confirm(startup):
    f = decode_frame(png("club-reward"))
    words = startup.read(f)
    for missing in (
        "club attendance reward",
        "x10",
        "claim rewards from mailbox",
        "confirm",
    ):
        assert (
            classify_club(f, [w for w in words if w.normalized != missing]).kind
            == "unknown"
        )
    assert classify_club((f * 0.5).astype("uint8"), words).kind == "unknown"


@pytest.fixture
def config(tmp_path):
    return Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        run_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        lock_dir=tmp_path / "locks",
    )


def frame(config, screen):
    return ShopFrame(Capture(png("home-badges"), 0, config.package), screen)


def runner(cls, config):
    return cls(
        config,
        None,
        None,
        vision=object(),
        monotonic=lambda: 0,
        sleep=lambda _: None,
        wall_clock=lambda: datetime(2026, 9, 24, 23, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "cls,screen",
    [
        (FreePackRunner, FreePackScreen("home")),
        (ClubRunner, ClubScreen("home")),
        (MailRunner, ShopScreen("home")),
    ],
)
def test_no_dot_is_a_verified_no_input_success(config, cls, screen):
    r = runner(cls, config)
    observations = []
    r.wait = lambda *args, **kwargs: observations.append(1) or frame(config, screen)
    r.tap = lambda *args: pytest.fail("no badge must send no input")
    try:
        assert r.run().status == "success"
        assert len(observations) == 2
    finally:
        r.journal.close()


def test_social_badge_without_club_badge_does_not_enter_club(config):
    r = runner(ClubRunner, config)
    taps = []
    r.wait = lambda *a, **k: frame(config, ClubScreen("home", red_dot=True))
    r.navigate = lambda *a: frame(config, ClubScreen("social", (310, 383), False))
    r.tap = lambda f, t, d: taps.append(t)
    r.home = lambda: None
    try:
        r.run()
        assert taps == [(640, 575)]
        assert last_visit(config) is None
    finally:
        r.journal.close()


def test_club_checkpoints_only_verified_entry_and_suppresses_same_day(config):
    r = runner(ClubRunner, config)
    taps = []
    reward = frame(config, ClubScreen("club_reward", (640, 490)))
    r.wait = lambda *a, **k: frame(config, ClubScreen("club", (1237, 23)))
    r.tap = lambda f, t, d: taps.append(t)
    r.home = lambda: None
    try:
        r.finish_visit(reward, game_day(r.wall_clock()))
        assert last_visit(config) == "2026-09-24"
        assert json.loads(state_path(config).read_text())["reward_notice_seen"] is True
        assert (
            loot.snapshot(config)["receipt_count"] == 0
        )  # Sent to mail is not received loot.
        r.wait = lambda *a, **k: frame(config, ClubScreen("home", red_dot=True))
        r.tap = lambda *a: pytest.fail("same day must not revisit Club")
        assert r.run().status == "success"
        assert state_path(replace(config, serial="127.0.0.1:5675")) != state_path(
            config
        )
    finally:
        r.journal.close()


def test_failed_club_entry_never_records_attendance(config):
    r = runner(ClubRunner, config)
    r.wait = lambda *a, **k: r.fail("Club unavailable")
    try:
        with pytest.raises(TaskError):
            r.finish_visit(frame(config, ClubScreen("club")), "2026-09-24")
        assert last_visit(config) is None
    finally:
        r.journal.close()


def test_free_confirmation_never_accepts_a_paid_or_unknown_screen(config):
    r = runner(FreePackRunner, config)
    r.tap = lambda *a: pytest.fail("must not authorize a purchase")
    try:
        for kind in ("purchase_confirm", "checkout", "store", "unknown"):
            with pytest.raises(TaskError, match="Cost: Free"):
                r.confirm(frame(config, FreePackScreen(kind, (760, 592))))
        assert not r.allows_billing
        r.device = SimpleNamespace(foreground_package=lambda: "com.android.vending")
        with pytest.raises(TaskError, match="Unexpected foreground"):
            r.capture()
    finally:
        r.journal.close()


def test_free_claim_waits_for_receipt_without_repeating_confirmation(config):
    r = runner(FreePackRunner, config)
    taps = []
    r.tap = lambda f, t, d: taps.append(t)
    r.wait = lambda *a, **k: r.fail("Receipt unavailable")
    try:
        with pytest.raises(TaskError):
            r.confirm(frame(config, FreePackScreen("free_confirm", (760, 592))))
        assert taps == [(760, 592)]
        assert loot.snapshot(config)["receipt_count"] == 0
    finally:
        r.journal.close()


def test_scanner_requires_two_observations_and_never_taps(config):
    r = runner(RedDotsRunner, config)
    frames = iter(
        [
            frame(config, BadgeScreen("home", ("free_pack", "club"))),
            frame(config, BadgeScreen("home", ("free_pack", "mail"))),
        ]
    )
    r.wait = lambda *a, **k: next(frames)
    r.tap = lambda *a: pytest.fail("scanner must be read-only")
    try:
        result = r.run()
        assert result.actions == 0
        assert json.loads((result.run_dir / "requests.json").read_text())["tasks"] == [
            "free_pack"
        ]
    finally:
        r.journal.close()
