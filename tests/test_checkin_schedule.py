"""Periodic home visits use the serial queue and durable, bounded pacing."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ba_automator import checkin_schedule
from ba_automator.config import Config, ConfigError
from ba_automator.server import ApiError, DashboardController
from test_server import FakeOutput, ProcessFactory, eventually


NOW = datetime(2026, 9, 26, 16, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive", state_dir=tmp_path)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "local.toml"
    path.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n')
    return path


class ScanOutput(FakeOutput):
    def __iter__(self):
        self.process.done.wait()
        if self.process.returncode:
            yield "Error: No stable home appeared before the startup time limit\n"
            return
        yield json.dumps({"status": "success", "run_dir": str(self.process.run_dir),
                          "duration": 1, "actions": 0}) + "\n"
        run = self.process.run_dir.parent / "red_dots-verified"
        run.mkdir()
        (run / "requests.json").write_text(json.dumps({"version": 1, "tasks": [], "ap": 110}))
        yield json.dumps({"status": "success", "run_dir": str(run), "duration": 1, "actions": 0}) + "\n"


class ScanFactory(ProcessFactory):
    def __call__(self, arguments, **options):
        process = super().__call__(arguments, **options)
        process.stdout = ScanOutput(process)
        return process


def tick(controller):
    with controller._condition:
        controller._enqueue_scheduled()
        controller._condition.notify_all()


def test_defaults_and_toml_roundtrip(config_path):
    config = Config.from_file(config_path)
    assert config.checkin_schedule_enabled is True
    assert config.checkin_interval_minutes == 30
    config_path.write_text(config_path.read_text() + '\n[checkin]\nschedule_enabled=false\ninterval_minutes=60\n')
    config = Config.from_file(config_path)
    assert config.checkin_schedule_enabled is False
    assert config.checkin_interval_minutes == 60


@pytest.mark.parametrize("value", [True, False, 4, 1441, 30.5, "30", None])
def test_invalid_interval_is_rejected(config, value):
    with pytest.raises(ConfigError, match="checkin.interval_minutes"):
        replace(config, checkin_interval_minutes=value)


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_enabled_flag_requires_bool(config, value):
    with pytest.raises(ConfigError, match="checkin_schedule_enabled"):
        replace(config, checkin_schedule_enabled=value)


def test_state_roundtrip_is_scoped_to_instance(config):
    state = checkin_schedule.initial_state(NOW, 30)
    checkin_schedule.write_state(config, state)
    assert checkin_schedule.read_state(config) == state
    assert checkin_schedule.read_state(replace(config, serial="localhost:05695")) == state
    assert checkin_schedule.read_state(replace(config, serial="127.0.0.1:5697")) is None


@pytest.mark.parametrize("raw", ["{broken", "null", "[]", "{}", '{"version":2}'])
def test_invalid_state_does_not_get_silently_discarded(config, raw):
    path = checkin_schedule.state_path(config)
    path.write_text(raw)
    with pytest.raises(checkin_schedule.CheckinScheduleError):
        checkin_schedule.read_state(config)
    assert path.read_text() == raw


@pytest.mark.parametrize("field,value", [
    ("version", True), ("next_due_at", "2026-09-26T16:30:00"), ("status", []),
    ("status", "running"), ("status", "success"), ("run_id", "bad\nidentity"),
    ("last_success_at", 13),
])
def test_invalid_record_is_rejected(config, field, value):
    state = {**checkin_schedule.initial_state(NOW, 30), field: value}
    with pytest.raises(checkin_schedule.CheckinScheduleError):
        checkin_schedule.write_state(config, state)
    assert not checkin_schedule.state_path(config).exists()


def test_failed_atomic_write_preserves_deadline(config, monkeypatch):
    original = checkin_schedule.initial_state(NOW, 30)
    checkin_schedule.write_state(config, original)

    def denied(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(checkin_schedule.CheckinScheduleError, match="could not be saved"):
        checkin_schedule.write_state(config, checkin_schedule.initial_state(NOW, 60))
    assert checkin_schedule.read_state(config) == original
    assert not list(config.state_dir.glob(".*.tmp"))


def test_first_start_waits_then_claim_is_saved_before_dispatch(config_path):
    clock = [NOW]
    factory = ScanFactory()

    def launch(arguments, **options):
        state = checkin_schedule.read_state(Config.from_file(arguments[4]))
        assert state["status"] == "running"
        assert state["next_due_at"] == (clock[0] + timedelta(minutes=30)).isoformat()
        return factory(arguments, **options)

    controller = DashboardController(config_path, process_factory=launch, wall_clock=lambda: clock[0])
    try:
        assert factory.processes == []
        assert controller.status()["schedule"]["checkin"]["next_due_at"] == (NOW + timedelta(minutes=30)).isoformat()
        clock[0] += timedelta(minutes=30)
        tick(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert controller.status()["current_job"]["source"] == "checkin"
        assert factory.processes[0].arguments[-1] == "red_dots"
        factory.processes[0].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        assert controller.status()["schedule"]["checkin"]["status"] == "success"
        assert factory.max_active == 1
    finally:
        controller.close()


def test_failure_waits_new_interval_and_keeps_future_checks(config_path):
    clock = [NOW]
    factory = ScanFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        # More than three failures must not permanently pause this timer.
        for index in range(4):
            clock[0] += timedelta(minutes=30)
            tick(controller)
            eventually(lambda: len(factory.processes) == index + 1)
            clock[0] += timedelta(minutes=2)
            factory.processes[-1].finish(1)
            eventually(lambda: controller.status()["current_job"] is None)
            status = controller.status()["schedule"]["checkin"]
            assert status["status"] == "failed"
            assert status["blocked_reason"] is None
            assert status["next_due_at"] == (clock[0] + timedelta(minutes=30)).isoformat()
            tick(controller)
            assert len(factory.processes) == index + 1
            assert controller.status()["queue"] == []
        assert controller.status()["queue_paused"] is False
    finally:
        controller.close()


def test_successful_manual_scan_postpones_periodic_check(config_path):
    clock = [NOW]
    factory = ScanFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        clock[0] += timedelta(minutes=29)
        controller.enqueue("restart")
        eventually(lambda: len(factory.processes) == 1)
        clock[0] += timedelta(minutes=3)
        tick(controller)
        assert controller.status()["queue"] == []
        factory.processes[0].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        saved = checkin_schedule.read_state(controller.config)
        assert saved["last_success_at"] == clock[0].isoformat()
        assert saved["next_due_at"] == (clock[0] + timedelta(minutes=30)).isoformat()
        assert len(factory.processes) == 1
    finally:
        controller.close()


def test_restart_respects_persisted_deadline_and_collapses_downtime(config_path):
    clock = [NOW]
    factory = ScanFactory()
    original = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    original.close()
    clock[0] += timedelta(minutes=10)
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        assert controller.status()["schedule"]["checkin"]["next_due_at"] == (NOW + timedelta(minutes=30)).isoformat()
        assert not factory.processes
        clock[0] += timedelta(days=3)
        tick(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert controller.status()["queue"] == []
        factory.processes[0].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        tick(controller)
        assert len(factory.processes) == 1
    finally:
        controller.close()


def test_bad_timer_state_does_not_pause_manual_jobs_and_settings_repair_it(config_path):
    config = Config.from_file(config_path)
    path = checkin_schedule.state_path(config)
    path.parent.mkdir(parents=True)
    path.write_text("broken")
    factory = ScanFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: NOW)
    try:
        assert controller.status()["schedule"]["checkin"]["blocked_reason"]
        assert not controller.status()["queue_paused"]
        controller.enqueue("restart")
        eventually(lambda: len(factory.processes) == 1)
        factory.processes[0].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        assert path.read_text() == "broken"
        controller.update_settings({"checkin_schedule_enabled": True, "checkin_interval_minutes": 60})
        status = controller.status()["schedule"]["checkin"]
        assert status["blocked_reason"] is None
        assert status["next_due_at"] == (NOW + timedelta(hours=1)).isoformat()
        assert Config.from_file(config_path).checkin_interval_minutes == 60
        assert controller.status()["config"]["checkin_interval_minutes"] == 60
        original = config_path.read_bytes()
        with pytest.raises(ApiError, match="integer from 5 to 1440"):
            controller.update_settings({"checkin_interval_minutes": True})
        assert config_path.read_bytes() == original
    finally:
        controller.close()


def test_unpersisted_claim_never_launches_and_does_not_pause_queue(config_path, monkeypatch):
    clock = [NOW]
    factory = ScanFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        def denied(*args, **kwargs):
            raise checkin_schedule.CheckinScheduleError("disk full")

        monkeypatch.setattr(checkin_schedule, "write_state", denied)
        clock[0] += timedelta(minutes=30)
        tick(controller)
        eventually(lambda: controller.status()["schedule"]["checkin"]["blocked_reason"])
        assert not factory.processes
        assert not controller.status()["queue_paused"]
        assert checkin_schedule.read_state(controller.config)["status"] is None
    finally:
        controller.close()


def test_unchanged_settings_preserve_timer_and_interval_change_keeps_scan_history(config_path):
    clock = [NOW]
    factory = ScanFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        controller.enqueue("restart")
        eventually(lambda: len(factory.processes) == 1)
        factory.processes[0].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        saved = checkin_schedule.read_state(controller.config)
        clock[0] += timedelta(minutes=10)
        controller.update_settings({"checkin_schedule_enabled": True, "checkin_interval_minutes": 30,
                                    "close_app_when_idle": True})
        assert checkin_schedule.read_state(controller.config) == saved
        controller.update_settings({"checkin_interval_minutes": 60})
        updated = checkin_schedule.read_state(controller.config)
        assert updated["next_due_at"] == (clock[0] + timedelta(minutes=60)).isoformat()
        assert updated["last_success_at"] == saved["last_success_at"]
        assert updated["status"] == saved["status"]
    finally:
        controller.close()
