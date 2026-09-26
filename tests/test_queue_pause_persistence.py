"""Operator pause survives daemon restart without resetting task schedules."""

from datetime import datetime, timezone
import json

import pytest

from ba_automator.config import Config
from ba_automator.server import ApiError, DashboardController
from test_server import ProcessFactory, eventually


NOW = datetime(2026, 9, 26, 16, tzinfo=timezone.utc)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "local.toml"
    path.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                    '[checkin]\nschedule_enabled=false\n', encoding="utf-8")
    return path


def controller_for(config_path, factory=None):
    return DashboardController(config_path, process_factory=factory or ProcessFactory(),
                               wall_clock=lambda: NOW)


@pytest.mark.parametrize("control", ["pause", "stop"])
def test_explicit_pause_or_stop_survives_restart_and_holds_due_cafe(config_path, control):
    first = controller_for(config_path)
    try:
        getattr(first, control)()
        first.update_settings({"cafe_schedule_enabled": True})
        before = json.loads((first.config.state_dir / "schedule.json").read_text())
        assert before["queue_paused"] is True
    finally:
        first.close()

    factory = ProcessFactory()
    second = controller_for(config_path, factory)
    try:
        assert second.status()["queue_paused"] is True
        with second._condition:
            second._enqueue_scheduled()
            assert not second._queue
            assert not factory.processes
        saved = json.loads((second.config.state_dir / "schedule.json").read_text())
        assert saved == before
        second.resume()
        eventually(lambda: len(factory.processes) == 1)
        assert factory.processes[0].arguments[-1] == "cafe"
    finally:
        second.close()


def test_resume_persists_and_shutdown_preserves_running_choice(config_path):
    first = controller_for(config_path)
    try:
        first.pause()
        first.resume()
    finally:
        first.close()
    factory = ProcessFactory()
    second = controller_for(config_path, factory)
    try:
        assert second.status()["queue_paused"] is False
        second.enqueue("restart")
        eventually(lambda: len(factory.processes) == 1)
    finally:
        second.close()


def test_legacy_schedule_without_pause_flag_remains_unpaused(config_path):
    config = Config.from_file(config_path)
    config.state_dir.mkdir(parents=True)
    schedule = {"cafe": {"retry_paused": True, "next_due_at": NOW.isoformat()},
                "crafting": {"retry_paused": True, "consecutive_failures": 3}}
    (config.state_dir / "schedule.json").write_text(json.dumps(schedule))
    controller = controller_for(config_path)
    try:
        assert controller.status()["queue_paused"] is False
        controller.pause()
        saved = json.loads((config.state_dir / "schedule.json").read_text())
        assert saved == {**schedule, "queue_paused": True}
    finally:
        controller.close()


@pytest.mark.parametrize("value", [None, 0, 1, "false"])
def test_invalid_saved_pause_value_never_resumes_dispatch(config_path, value):
    config = Config.from_file(config_path)
    config.state_dir.mkdir(parents=True)
    (config.state_dir / "schedule.json").write_text(json.dumps({"cafe": {}, "queue_paused": value}))
    controller = controller_for(config_path)
    try:
        assert controller.status()["queue_paused"] is True
    finally:
        controller.close()


def test_unreadable_schedule_starts_paused(config_path):
    config = Config.from_file(config_path)
    config.state_dir.mkdir(parents=True)
    (config.state_dir / "schedule.json").write_text("{broken")
    controller = controller_for(config_path)
    try:
        assert controller.status()["queue_paused"] is True
        assert any("Could not restore task schedules" in item["message"] for item in controller._logs)
    finally:
        controller.close()


@pytest.mark.parametrize("control", ["pause", "resume", "stop"])
def test_pause_storage_failure_never_enables_dispatch(config_path, monkeypatch, control):
    controller = controller_for(config_path)
    controller.pause()

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("ba_automator.server._write_atomic", fail_write)
    try:
        with pytest.raises(ApiError, match="dispatch remains paused") as error:
            getattr(controller, control)()
        assert error.value.status == 500
        assert controller.status()["queue_paused"] is True
    finally:
        controller.close()


def test_stop_still_interrupts_child_when_pause_storage_fails(config_path, monkeypatch):
    factory = ProcessFactory()
    controller = controller_for(config_path, factory)
    controller.enqueue("restart")
    eventually(lambda: len(factory.processes) == 1)

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("ba_automator.server._write_atomic", fail_write)
    try:
        with pytest.raises(ApiError, match="dispatch remains paused"):
            controller.stop()
        eventually(lambda: controller.status()["current_job"] is None)
        assert factory.processes[0].signals
        assert controller.status()["queue_paused"] is True
    finally:
        controller.close()
