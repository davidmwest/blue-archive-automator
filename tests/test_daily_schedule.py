"""Reset boundaries and durable claims must not duplicate Daily spending."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ba_automator.config import Config
from ba_automator import daily_schedule


NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive", state_dir=tmp_path)


def occurrence(status="running", *, day="2026-09-25"):
    return {
        "version": 1,
        "game_day": day,
        "status": status,
        "run_id": "daily-test-run",
        "started_at": NOW.isoformat(),
        "completed_at": None if status == "running" else (NOW + timedelta(minutes=5)).isoformat(),
    }


@pytest.mark.parametrize(("stamp", "day", "next_stamp"), [
    ("2026-09-25T18:59:59+00:00", "2026-09-24", "2026-09-25T19:01:00+00:00"),
    ("2026-09-25T19:00:00+00:00", None, "2026-09-25T19:01:00+00:00"),
    ("2026-09-25T19:00:59.999999+00:00", None, "2026-09-25T19:01:00+00:00"),
    ("2026-09-25T19:01:00+00:00", "2026-09-25", "2026-09-26T19:01:00+00:00"),
    ("2026-09-26T00:00:00+00:00", "2026-09-25", "2026-09-26T19:01:00+00:00"),
])
def test_global_reset_and_grace_period(stamp, day, next_stamp):
    now = datetime.fromisoformat(stamp)
    assert daily_schedule.due_day(now) == day
    assert daily_schedule.next_due(now) == datetime.fromisoformat(next_stamp)


@pytest.mark.parametrize("delay", [0, 1, 30, 120])
def test_configurable_delay_is_inclusive_at_deadline(delay):
    reset = NOW.replace(hour=19)
    boundary = reset + timedelta(minutes=delay)
    assert daily_schedule.due_day(boundary, delay) == "2026-09-25"
    assert daily_schedule.is_due(daily_schedule.empty_state(), boundary, delay)
    if delay:
        assert daily_schedule.due_day(boundary - timedelta(microseconds=1), delay) is None


def test_unrecorded_previous_day_does_not_run_in_new_days_grace_period():
    assert not daily_schedule.is_due(daily_schedule.empty_state(), NOW.replace(hour=19, second=30))
    assert not daily_schedule.is_due(occurrence(day="2026-09-20"), NOW.replace(hour=19, second=30))


@pytest.mark.parametrize(("month", "local_hour"), [(1, 11), (7, 12)])
def test_dst_changes_local_trigger_time_without_moving_game_reset(month, local_hour):
    local = datetime(2026, month, 25, local_hour, 1, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert local.astimezone(timezone.utc).hour == 19
    assert daily_schedule.due_day(local) == f"2026-{month:02}-25"
    assert daily_schedule.next_due(local).hour == 19
    assert daily_schedule.next_due(local).tzinfo == timezone.utc


def test_catchup_uses_current_day_after_extended_downtime():
    state = occurrence("success", day="2026-09-01")
    assert daily_schedule.is_due(state, NOW)
    assert daily_schedule.due_day(NOW) == "2026-09-25"
    state = {**state, "game_day": daily_schedule.due_day(NOW)}
    assert not daily_schedule.is_due(state, NOW)


@pytest.mark.parametrize("status", sorted(daily_schedule.STATUSES))
def test_any_persisted_attempt_blocks_same_day_after_restart(config, status):
    daily_schedule.write_state(config, occurrence(status))
    persisted = daily_schedule.read_state(config)
    assert persisted == occurrence(status)
    assert not daily_schedule.is_due(persisted, NOW + timedelta(minutes=6))
    assert daily_schedule.is_due(persisted, NOW + timedelta(days=1))


def test_clock_rollback_cannot_replay_an_earlier_game_day(config):
    daily_schedule.write_state(config, occurrence("success"))
    assert not daily_schedule.is_due(daily_schedule.read_state(config), NOW - timedelta(days=1))


def test_cancelled_queue_occurrence_may_be_skipped_without_a_start(config):
    state = {**occurrence("skipped"), "started_at": None}
    daily_schedule.write_state(config, state)
    assert not daily_schedule.is_due(daily_schedule.read_state(config), NOW)


def test_manual_retry_can_replace_failed_occurrence(config):
    daily_schedule.write_state(config, occurrence("failed"))
    replacement = {**occurrence("running"), "run_id": "manual-retry"}
    daily_schedule.write_state(config, replacement)
    assert daily_schedule.read_state(config) == replacement


def test_missing_file_is_empty_without_writing_a_claim(config):
    assert daily_schedule.read_state(config) == daily_schedule.empty_state()
    assert not daily_schedule.state_path(config).exists()
    assert daily_schedule.is_due(daily_schedule.read_state(config), NOW)


def test_state_is_scoped_to_canonical_device_and_package(config):
    alias = Config(serial="localhost:05695", package=config.package, state_dir=config.state_dir)
    other_device = Config(serial="127.0.0.1:5697", package=config.package, state_dir=config.state_dir)
    other_package = Config(serial=config.serial, package="com.other.bluearchive", state_dir=config.state_dir)
    daily_schedule.write_state(config, occurrence("success"))
    assert daily_schedule.state_path(alias) == daily_schedule.state_path(config)
    assert daily_schedule.read_state(alias)["status"] == "success"
    assert daily_schedule.read_state(other_device) == daily_schedule.empty_state()
    assert daily_schedule.read_state(other_package) == daily_schedule.empty_state()


@pytest.mark.parametrize("raw", ["{broken", "null", "[]", "{}", '{"version": 2}'])
def test_corrupt_or_unsupported_state_is_not_silently_reset(config, raw):
    daily_schedule.state_path(config).write_text(raw)
    with pytest.raises(daily_schedule.DailyScheduleError):
        daily_schedule.read_state(config)
    assert daily_schedule.state_path(config).read_text() == raw


@pytest.mark.parametrize("change", [
    {"version": True}, {"game_day": "20260925"}, {"game_day": "2026-09-31"},
    {"game_day": None}, {"status": "queued"}, {"status": []}, {"run_id": ""},
    {"run_id": "unsafe\nidentity"}, {"run_id": None}, {"started_at": None},
    {"started_at": "2026-09-25T20:00:00"}, {"started_at": 123},
    {"completed_at": NOW.isoformat()},
])
def test_invalid_occurrence_is_rejected_for_reads_and_writes(config, change):
    state = {**occurrence(), **change}
    daily_schedule.state_path(config).write_text(json.dumps(state))
    with pytest.raises(daily_schedule.DailyScheduleError):
        daily_schedule.read_state(config)
    with pytest.raises(daily_schedule.DailyScheduleError):
        daily_schedule.write_state(config, state)


@pytest.mark.parametrize("change", [
    {"completed_at": None}, {"started_at": None},
    {"completed_at": (NOW - timedelta(seconds=1)).isoformat()},
])
def test_terminal_outcome_requires_coherent_timestamps(config, change):
    with pytest.raises(daily_schedule.DailyScheduleError):
        daily_schedule.write_state(config, {**occurrence("success"), **change})


def test_empty_state_cannot_hide_an_outcome(config):
    with pytest.raises(daily_schedule.DailyScheduleError):
        daily_schedule.write_state(config, {**daily_schedule.empty_state(), "status": "success"})


def test_io_error_is_not_treated_as_a_missing_file(config, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("test read denied")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(daily_schedule.DailyScheduleError, match="could not be read"):
        daily_schedule.read_state(config)


def test_failed_atomic_replacement_preserves_previous_claim(config, monkeypatch):
    original = occurrence("running")
    daily_schedule.write_state(config, original)

    def denied(*args, **kwargs):
        raise OSError("test disk failure")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(daily_schedule.DailyScheduleError, match="could not be saved"):
        daily_schedule.write_state(config, occurrence("success"))
    assert daily_schedule.read_state(config) == original
    assert not list(config.state_dir.glob(".*.tmp"))


def test_failed_flush_does_not_publish_a_claim(config, monkeypatch):
    def denied(*args, **kwargs):
        raise OSError("test disk failure")

    monkeypatch.setattr(daily_schedule.os, "fsync", denied)
    with pytest.raises(daily_schedule.DailyScheduleError, match="could not be saved"):
        daily_schedule.write_state(config, occurrence())
    assert not daily_schedule.state_path(config).exists()
    assert not list(config.state_dir.glob(".*.tmp"))


@pytest.mark.parametrize("delay", [-1, 121, True, 1.5, "1"])
def test_invalid_delay_is_rejected(delay):
    for calculate in (daily_schedule.due_day, daily_schedule.next_due):
        with pytest.raises(ValueError, match="delay"):
            calculate(NOW, delay)


def test_naive_clock_is_rejected_instead_of_assuming_host_timezone():
    for calculate in (daily_schedule.due_day, daily_schedule.next_due):
        with pytest.raises(ValueError, match="timezone"):
            calculate(NOW.replace(tzinfo=None))
