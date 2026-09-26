"""Daily occurrence persistence around the real serial dispatcher (offline child)."""

from datetime import datetime, timedelta, timezone
import tomllib

import pytest

from ba_automator import daily_schedule
from ba_automator.config import Config
from ba_automator.server import ApiError, DashboardController
from test_server import ProcessFactory, config_path, eventually  # shared offline process harness


NOW = datetime(2026, 9, 25, 20, tzinfo=timezone.utc)


def enable(config_path, extra=""):
    with config_path.open("a") as stream:
        stream.write('\n[daily]\nschedule_enabled=true\nreset_delay_minutes=1\n' + extra)


def wake(controller):
    with controller._condition:
        controller._condition.notify_all()


def seed(config, status="success", day="2026-09-24"):
    state = {"version": 1, "game_day": day, "status": status, "run_id": "previous-run",
             "started_at": (NOW - timedelta(days=1)).isoformat(),
             "completed_at": None if status == "running" else (NOW - timedelta(hours=23)).isoformat()}
    daily_schedule.write_state(config, state)


def test_daily_setting_roundtrip_and_validation(config_path):
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory)
    try:
        controller.pause()
        controller.update_settings({"daily_schedule_enabled": True, "daily_reset_delay_minutes": 12})
        assert not factory.processes
        stored = tomllib.loads(config_path.read_text())
        assert stored["daily"] == {"schedule_enabled": True, "reset_delay_minutes": 12}
        assert controller.status()["config"]["daily_schedule_enabled"] is True
        original = config_path.read_bytes()
        for changes in ({"daily_schedule_enabled": "yes"}, {"daily_reset_delay_minutes": True},
                        {"daily_reset_delay_minutes": -1}, {"daily_reset_delay_minutes": 121},
                        {"daily_reset_delay_minutes": 1.5}):
            with pytest.raises(ApiError):
                controller.update_settings(changes)
            assert config_path.read_bytes() == original
    finally:
        controller.close()


def test_daily_catches_up_once_and_survives_server_restart(config_path):
    enable(config_path)
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: NOW)
    try:
        eventually(lambda: len(factory.processes) == 1)
        assert factory.processes[0].arguments[-1] == "daily"
        claim = daily_schedule.read_state(controller.config)
        assert claim["game_day"] == "2026-09-25"
        assert claim["status"] == "running"  # Written before the child is allowed to act.
        snapshot = Config.from_file(factory.processes[0].arguments[4])
        assert snapshot.daily_schedule_enabled is True
        assert snapshot.daily_reset_delay_minutes == 1
        factory.processes[0].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        assert daily_schedule.read_state(controller.config)["status"] == "success"
        assert controller.status()["schedule"]["daily"]["next_due_at"] == "2026-09-26T19:01:00+00:00"
    finally:
        controller.close()
    second = ProcessFactory()
    controller = DashboardController(config_path, process_factory=second, wall_clock=lambda: NOW)
    try:
        with controller._condition:
            controller._enqueue_scheduled()
        assert not second.processes
        assert not controller.status()["queue"]
    finally:
        controller.close()


def test_reset_buffer_and_new_game_day_trigger(config_path):
    enable(config_path)
    seed(Config.from_file(config_path))
    now = [NOW.replace(hour=18, minute=59)]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        for stamp in [NOW.replace(hour=18, minute=59), NOW.replace(hour=19, second=30)]:
            now[0] = stamp
            with controller._condition:
                controller._enqueue_scheduled()
            assert not controller.status()["queue"]
            assert not factory.processes
        now[0] = NOW.replace(hour=19, minute=1)
        wake(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert daily_schedule.read_state(controller.config)["game_day"] == "2026-09-25"
    finally:
        controller.close()


def test_daily_queued_behind_active_job_stays_serial_and_preempts_cafe_timer(config_path):
    enable(config_path, '[cafe]\nschedule_enabled=true\n')
    now = [NOW.replace(hour=19, second=30)]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        # Cafe may start during the reset buffer; the Daily waits for that child.
        eventually(lambda: len(factory.processes) == 1)
        assert factory.processes[0].arguments[-1] == "cafe"
        now[0] = NOW
        with controller._condition:
            controller._enqueue_scheduled()
            controller._enqueue_scheduled()
        assert [job["task"] for job in controller.status()["queue"]] == ["daily"]
        factory.processes[0].finish()
        eventually(lambda: len(factory.processes) == 2)
        assert factory.processes[1].arguments[-1] == "daily"
        assert factory.max_active == 1
        assert not controller.status()["queue"]
    finally:
        controller.close()


@pytest.mark.parametrize("status", ["running", "success", "failed", "stopped", "skipped"])
def test_saved_attempt_is_not_retried_by_resume_but_next_game_day_runs(config_path, status):
    enable(config_path)
    seed(Config.from_file(config_path), status=status, day="2026-09-25")
    now = [NOW]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        controller.resume()
        with controller._condition:
            controller._enqueue_scheduled()
        assert not factory.processes
        assert not controller.status()["queue"]
        if status == "running":
            assert "interrupted" in controller.status()["schedule"]["daily"]["blocked_reason"]
        now[0] += timedelta(days=3)  # Catch up today's routine, not three missed routines.
        wake(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert daily_schedule.read_state(controller.config)["game_day"] == "2026-09-28"
        assert not controller.status()["queue"]
    finally:
        controller.close()


def test_cancel_scheduled_daily_persists_skip_and_honors_pause(config_path):
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: NOW)
    try:
        controller.pause()
        controller.update_settings({"daily_schedule_enabled": True})
        with controller._condition:
            controller._enqueue_scheduled()
            assert not controller._queue
            # Materialize a due occurrence without releasing the dispatcher lock.
            controller._paused = False
            controller._enqueue_scheduled()
            controller._paused = True
        job = controller.status()["queue"][0]
        controller.cancel(job["id"])
        assert daily_schedule.read_state(controller.config)["status"] == "skipped"
        controller.resume()
        with controller._condition:
            controller._enqueue_scheduled()
        assert not controller.status()["queue"]
        assert not factory.processes
    finally:
        controller.close()


def test_manual_daily_failure_can_be_explicitly_retried(config_path):
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: NOW)
    try:
        controller.enqueue("daily")
        eventually(lambda: len(factory.processes) == 1)
        with pytest.raises(ApiError, match="already running or queued"):
            controller.enqueue("daily")
        factory.processes[0].finish(1)
        eventually(lambda: controller.status()["current_job"] is None)
        assert daily_schedule.read_state(controller.config)["status"] == "failed"
        controller.pause()
        controller.update_settings({"daily_schedule_enabled": True})
        controller.enqueue("daily")
        controller.resume()
        eventually(lambda: len(factory.processes) == 2)
        factory.processes[1].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        assert daily_schedule.read_state(controller.config)["status"] == "success"
        assert not controller.status()["queue"]
    finally:
        controller.close()


def test_daily_is_never_launched_when_attempt_cannot_be_persisted(config_path, monkeypatch):
    def denied(*args):
        raise daily_schedule.DailyScheduleError("disk unavailable")
    monkeypatch.setattr(daily_schedule, "write_state", denied)
    enable(config_path)
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: NOW)
    try:
        eventually(lambda: bool(controller.status()["failed_jobs"]))
        assert not factory.processes
        assert "disk unavailable" in controller.status()["schedule"]["daily"]["blocked_reason"]
        assert not daily_schedule.state_path(controller.config).exists()
    finally:
        controller.close()


def test_corrupt_daily_state_blocks_timer_without_erasing_evidence(config_path):
    config = Config.from_file(config_path)
    path = daily_schedule.state_path(config)
    path.parent.mkdir(parents=True)
    path.write_text("{invalid")
    enable(config_path)
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: NOW)
    try:
        with controller._condition:
            controller._enqueue_scheduled()
        assert not factory.processes
        assert not controller.status()["queue"]
        assert controller.status()["schedule"]["daily"]["blocked_reason"]
        assert path.read_text() == "{invalid"
    finally:
        controller.close()


def test_queue_crossing_reset_grace_waits_for_new_day(config_path):
    now = [NOW.replace(hour=18, minute=59)]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        controller.pause()
        controller.update_settings({"daily_schedule_enabled": True})
        with controller._condition:
            controller._paused = False
            controller._enqueue_scheduled()
            controller._paused = True
        assert controller.status()["queue"][0]["game_day"] == "2026-09-24"
        now[0] = NOW.replace(hour=19, second=30)
        controller.resume()
        eventually(lambda: not controller.status()["queue"])
        assert not factory.processes
        assert not daily_schedule.state_path(controller.config).exists()
        now[0] = NOW.replace(hour=19, minute=1)
        wake(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert daily_schedule.read_state(controller.config)["game_day"] == "2026-09-25"
    finally:
        controller.close()


def test_device_lock_contention_defers_daily_without_consuming_or_disabling_it(config_path):
    from ba_automator.locking import InstanceLock

    enable(config_path)
    now = [NOW]
    factory = ProcessFactory()
    config = Config.from_file(config_path)
    with InstanceLock(config):
        controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
        eventually(lambda: controller._daily_not_before is not None)
        assert not factory.processes
        assert not daily_schedule.state_path(config).exists()
        assert controller.status()["schedule"]["daily"]["blocked_reason"] is None
    try:
        now[0] += timedelta(minutes=1)
        wake(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert daily_schedule.read_state(config)["status"] == "running"
    finally:
        controller.close()
