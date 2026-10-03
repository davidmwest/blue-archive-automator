"""Replay fleeting login rewards without another login, claim, or device tap."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json

import cv2
import pytest

from ba_automator import loot, startup_loot as sl
from ba_automator.config import Config
from ba_automator.locking import InstanceLock, LockError
from ba_automator.vision import StartupVision, Word, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"
ATTENDANCE = SimpleNamespace(text=("Daily", "Arona's Attendance!"))
REWARDS = SimpleNamespace(text=("REWARD ACQUIRED!", "Touch to Continue"))
HOME = SimpleNamespace(text=("Cafe", "Campaign"))
NOW = datetime(2026, 10, 2, 21, 42, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def vision():
    return StartupVision()


@pytest.fixture
def config(tmp_path):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                  run_dir=tmp_path / "runs", state_dir=tmp_path / "state",
                  lock_dir=tmp_path / "locks")


def observer(config, vision, name="first", now=NOW):
    return sl.StartupLoot(config, config.run_dir / name, vision, Mock(), now=lambda: now)


def attendance_frames(day):
    return [FIXTURES / f"login-calendar-{prefix}day{day}.png" for prefix in ("before-", "")]


@pytest.mark.parametrize("name,kind", [
    ("login-calendar-before-day2", "attendance"),
    ("login-calendar-day2", "attendance"),
    ("login-daily-packs-entering", "rewards"),
    ("login-daily-packs", "rewards"),
])
@pytest.mark.parametrize("height", [720, 1440])
def test_real_startup_observations_trigger_passive_capture(vision, name, kind, height):
    png = (FIXTURES / f"{name}.png").read_bytes()
    if height == 720:
        png = sl.encode(decode_frame(png))
    assert sl.candidate_kind(vision.analyze(png)) == kind


@pytest.mark.parametrize("day,name,quantity", [
    (1, "Credits", 20000), (2, "Normal Activity Report", 3),
    (7, "Advanced Activity Report", 1), (8, "AP", 100),
])
@pytest.mark.parametrize("height", [720, 1440])
def test_only_new_stamped_day_counts(vision, tmp_path, day, name, quantity, height):
    frames = attendance_frames(day)
    if height == 720:
        resized = []
        for index, source in enumerate(frames):
            target = tmp_path / f"{index}.png"
            target.write_bytes(sl.encode(decode_frame(source.read_bytes())))
            resized.append(target)
        frames = resized
    items, complete, evidence, note = sl.parse_attendance(frames, vision)
    assert complete and [item for item, _ in items] == [{"name": name, "quantity": quantity}]
    assert evidence == frames[-1] and f"day {day}" in note
    assert all(icon.startswith(b"\x89PNG") for _, icon in items)


@pytest.mark.parametrize("indices", [(0,), (1,), (0, 0), (1, 0)])
def test_old_stamp_future_rewards_and_reverse_animation_are_not_loot(vision, indices):
    frames = attendance_frames(2)
    items, complete, _, _ = sl.parse_attendance([frames[i] for i in indices], vision)
    assert not complete and items == []


def test_calendar_requires_positioned_day_labels_and_recognized_art(vision):
    png = attendance_frames(2)[0].read_bytes()
    words = sl.read_game_words(png, vision)
    wrong = SimpleNamespace(read=lambda image: [w for w in words if sl.compact(w.text) != 'day10'])
    # Canonical copy keeps the supplied word coordinates in the same system.
    assert sl.attendance_state(sl.encode(decode_frame(png)), wrong) is None
    image = decode_frame(png)
    x, y, w, _ = sl.cell_box(1)
    image[y+20:y+112, x:x+w] = (30, 40, 50)
    assert sl.attendance_item(sl.encode(image), 1, vision) is None


@pytest.mark.parametrize("values", [("3", "8"), ("3", ""), ("0", "0"), ("3,0", "3,0")])
def test_unreadable_or_conflicting_amount_is_never_filled_from_calendar_defaults(vision, monkeypatch, values):
    readings = iter([[Word(value, .999, (1, 1, 10, 10))] if value else [] for value in values])
    monkeypatch.setattr(sl, "read_crop", lambda *args: next(readings))
    assert sl.attendance_item(attendance_frames(2)[0].read_bytes(), 1, vision) is None


def test_pack_animation_reads_actual_grants_and_keeps_separate_ap_cards(vision):
    entrance = FIXTURES / "login-daily-packs-entering.png"
    settled = FIXTURES / "login-daily-packs.png"
    items, complete, evidence, _ = sl.parse_rewards([entrance, settled, settled], vision)
    assert complete and evidence == settled
    assert [item for item, _ in items] == [
        {"name": "Pyroxenes", "quantity": 60},
        {"name": "Bounty Ticket", "quantity": 9},
        {"name": "Scrimmage Ticket", "quantity": 9},
        {"name": "AP", "quantity": 160}, {"name": "AP", "quantity": 150},
    ]
    items, complete, _, _ = sl.parse_rewards([entrance], vision)
    assert not complete and items == []


def test_capture_holds_dismissal_but_is_bounded_and_ignores_expiration(config, vision):
    capture = observer(config, vision)
    png = attendance_frames(2)[0].read_bytes()
    assert capture.observe(png, ATTENDANCE, 0)
    assert capture.observe(png, ATTENDANCE, 1)
    assert not capture.observe(png, ATTENDANCE, 2)
    for n in range(3, 30):
        capture.observe(png, ATTENDANCE, n)
    assert len(capture.sequences[0]["frames"]) == sl.MAX_FRAMES
    assert len(list(capture.root.glob("*.png"))) == sl.MAX_FRAMES
    assert not capture.observe(png, HOME, 31)
    assert not capture.observe(png, SimpleNamespace(text=("Reward Acquired", "Items have expired")), 32)
    manifest = json.loads((capture.root / "frames.json").read_text())
    assert manifest == capture.sequences


def test_restarts_daily_reset_icons_and_clear_are_idempotent(config, vision):
    png = (FIXTURES / "login-daily-packs.png").read_bytes()
    for name, now in [("first", NOW), ("retry", NOW),
                      ("after-midnight", datetime(2026, 10, 3, 18, 59, tzinfo=timezone.utc))]:
        with observer(config, vision, name, now) as capture:
            capture.observe(png, REWARDS, 0)
            capture.observe(png, REWARDS, 3)
    data = loot.snapshot(config)
    assert data["receipt_count"] == 1 and data["unidentified_receipts"] == 0
    assert {i["name"]: i["quantity"] for i in data["items"]} == {
        "Pyroxenes": 60, "Bounty Ticket": 9, "Scrimmage Ticket": 9, "AP": 310}
    assert len(list((config.state_dir / "loot-icons").glob("*.png"))) == 5
    loot.clear(config)
    with observer(config, vision, "after-clear") as capture:
        capture.observe(png, REWARDS, 0)
    assert loot.snapshot(config)["receipt_count"] == 0
    with observer(config, vision, "new-day", datetime(2026, 10, 3, 19, 1, tzinfo=timezone.utc)) as capture:
        capture.observe(png, REWARDS, 0)
    assert loot.snapshot(config)["receipt_count"] == 1


@pytest.mark.parametrize("clear_first", [False, True])
def test_partial_receipt_can_be_enriched_without_reappearing_after_clear(config, vision, clear_first):
    before, after = attendance_frames(2)
    with observer(config, vision) as capture:
        capture.observe(before.read_bytes(), ATTENDANCE, 0)
    original = json.loads((config.state_dir / "important-actions.jsonl").read_text().splitlines()[0])
    assert loot.snapshot(config)["unidentified_receipts"] == 1
    if clear_first:
        loot.clear(config)
    with observer(config, vision, "complete") as capture:
        capture.observe(before.read_bytes(), ATTENDANCE, 0)
        capture.observe(after.read_bytes(), ATTENDANCE, 3)
    actions = [json.loads(line) for line in (config.state_dir / "important-actions.jsonl").read_text().splitlines()]
    assert actions[-1]["evidence"] == original["evidence"]
    assert actions[-1]["time"] == original["time"]
    data = loot.snapshot(config)
    assert data["receipt_count"] == (0 if clear_first else 1)
    assert data["unidentified_receipts"] == 0
    if not clear_first:
        assert data["items"][0]["quantity"] == 3


def test_same_popup_across_gap_and_startup_failure_still_records_once(config, vision):
    before, after = attendance_frames(2)
    capture = observer(config, vision)
    with pytest.raises(RuntimeError, match="startup failed"), capture:
        capture.observe(before.read_bytes(), ATTENDANCE, 0)
        capture.observe(b"unrelated screen", HOME, 2)
        capture.observe(after.read_bytes(), ATTENDANCE, 4)
        raise RuntimeError("startup failed after reward arrived")
    capture.finish()
    assert loot.snapshot(config)["receipt_count"] == 1


def test_optional_capture_or_ocr_failure_does_not_block_startup(config, vision, monkeypatch):
    capture = observer(config, vision)
    capture.observe(attendance_frames(2)[0].read_bytes(), ATTENDANCE, 0)
    def fail(*args):
        raise OSError("test unavailable OCR")
    monkeypatch.setattr(sl, "parse_attendance", fail)
    capture.finish()
    assert (capture.root / "frames.json").exists()
    assert loot.snapshot(config)["items"] == []
    capture = observer(config, vision, "disk-error")
    monkeypatch.setattr(Path, "write_bytes", fail)
    assert not capture.observe(b"frame", ATTENDANCE, 0)


def test_restart_finalizes_after_navigation_while_instance_lock_is_held(config, monkeypatch):
    from dataclasses import replace
    from test_restart import FakeClock, FakeDevice, FakeVision, Observation
    from ba_automator.restart import run_restart
    clock, device = FakeClock(), FakeDevice()
    observations = [Observation("popup", target=(1190, 675), text=ATTENDANCE.text)] * 3
    observations.append(Observation("home"))
    parser_calls = []
    def parse(frames, vision):
        with pytest.raises(LockError):
            with InstanceLock(config):
                pass
        assert vision.index >= 4  # No expensive receipt parsing before popup input.
        parser_calls.append(frames)
        return [], False, frames[-1], "Test unreadable calendar"
    monkeypatch.setattr(sl, "parse_attendance", parse)
    result = run_restart(replace(config, poll_interval=1, action_cooldown=.1), device,
                         FakeVision(observations, clock), monotonic=clock.monotonic, sleep=clock.sleep)
    assert result.status == "success"
    assert device.taps == [("tap", 1190, 675)]
    assert len(parser_calls) == 1
    # The runner released the lock normally, despite optional incomplete loot.
    with InstanceLock(config):
        pass
