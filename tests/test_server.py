from __future__ import annotations

import http.client
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import tomllib

import cv2
import numpy as np
import pytest

from ba_automator.config import Config, ConfigError
from ba_automator.locking import InstanceLock
from ba_automator.server import ApiError, CAFE_INTERVAL, DashboardController, create_server


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "condition did not become true before the test deadline"


class FakeOutput:
    def __init__(self, process):
        self.process = process

    def __iter__(self):
        yield "12:00:00 restart: title\n"
        self.process.done.wait()
        if self.process.returncode == 0:
            yield json.dumps({"status": "success", "run_dir": str(self.process.run_dir),
                              "duration": 1.2, "actions": 2}, indent=2) + "\n"
        else:
            yield "Stopped. No further game input will be sent.\n"

    def close(self):
        pass


class FakeProcess:
    def __init__(self, factory, arguments, options):
        self.factory = factory
        self.arguments = arguments
        self.options = options
        self.done = threading.Event()
        self.returncode = None
        self.signals = []
        self.stdout = FakeOutput(self)
        config = Config.from_file(arguments[4])
        self.run_dir = config.run_dir / "restart-test"
        self.run_dir.mkdir(parents=True)

    def finish(self, code=0):
        with self.factory.lock:
            if self.returncode is None:
                self.returncode = code
                self.factory.active -= 1
                self.done.set()

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("fake child", timeout)
        return self.returncode

    def poll(self):
        return self.returncode

    def send_signal(self, signal):
        self.signals.append(signal)
        self.finish(130)

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


class ProcessFactory:
    def __init__(self):
        self.processes = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, arguments, **options):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            process = FakeProcess(self, arguments, options)
            self.processes.append(process)
            return process


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config" / "local.toml"
    path.parent.mkdir()
    path.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                    'adb_path="../tools/adb"\n[restart]\nauto_download=true\n'
                    'home_confirmations=4\naction_cooldown=2.5\n'
                    '[storage]\nrun_dir="../data/runs"\nlock_dir="../data/locks"\n', encoding="utf-8")
    return path


@pytest.fixture
def controlled(config_path):
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory)
    try:
        yield controller, factory
    finally:
        controller.close()


def test_starting_controller_does_not_launch_and_fifo_never_overlaps(controlled):
    controller, factory = controlled
    assert controller.status()["state"] == "idle"
    assert factory.processes == []
    controller.pause()
    first = controller.enqueue("restart")
    second = controller.enqueue("daily")
    assert [job["id"] for job in controller.status()["queue"]] == [first["id"], second["id"]]
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    assert factory.processes[0].arguments[-1] == "restart"
    assert controller.status()["current_job"]["id"] == first["id"]
    factory.processes[0].finish()
    eventually(lambda: len(factory.processes) == 2)
    assert factory.processes[1].arguments[-1] == "daily"
    assert factory.max_active == 1
    factory.processes[1].finish()
    eventually(lambda: controller.status()["state"] == "success")
    status = controller.status()
    assert [job["id"] for job in status["history"]] == [second["id"], first["id"]]
    assert status["result"]["actions"] == 2
    assert status["queue"] == []


def test_cancel_is_pending_only_and_serialized_with_dispatch(controlled):
    controller, factory = controlled
    first = controller.enqueue("restart")
    eventually(lambda: len(factory.processes) == 1)
    second = controller.enqueue("daily")
    controller.cancel(second["id"])
    assert controller.status()["queue"] == []
    with pytest.raises(ApiError) as error:
        controller.cancel(first["id"])
    assert error.value.status == 409
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    assert len(factory.processes) == 1


def test_pause_waits_for_current_job_and_stop_retains_pending_jobs(controlled):
    controller, factory = controlled
    controller.enqueue("restart")
    eventually(lambda: len(factory.processes) == 1)
    queued = controller.enqueue("daily")
    controller.pause()
    factory.processes[0].finish()
    eventually(lambda: controller.status()["current_job"] is None)
    assert len(factory.processes) == 1
    assert controller.status()["queue"][0]["id"] == queued["id"]
    controller.resume()
    eventually(lambda: len(factory.processes) == 2)
    remaining = controller.enqueue("restart")
    controller.stop()
    eventually(lambda: controller.status()["state"] == "stopped")
    assert factory.processes[1].signals
    assert controller.status()["queue_paused"] is True
    assert controller.status()["queue"][0]["id"] == remaining["id"]
    assert len(factory.processes) == 2


def test_settings_validate_before_write_and_preserve_device_and_paths(controlled, config_path):
    controller, _ = controlled
    original = config_path.read_bytes()
    with pytest.raises(ApiError) as error:
        controller.update_settings({"poll_interval": 0})
    assert error.value.status == 400
    assert config_path.read_bytes() == original
    controller.update_settings({"auto_download": False, "startup_timeout": 90})
    saved = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert saved["device"]["adb_path"] == "../tools/adb"
    assert saved["storage"]["run_dir"] == "../data/runs"
    assert saved["restart"]["home_confirmations"] == 4
    assert saved["restart"]["action_cooldown"] == 2.5
    assert saved["restart"]["auto_download"] is False
    assert controller.status()["config"]["startup_timeout"] == 90
    controller.pause()
    controller.enqueue("restart")
    with pytest.raises(ApiError) as error:
        controller.update_settings({"auto_download": True})
    assert error.value.status == 409


def test_frames_are_scoped_to_current_job_and_clear_before_next_job(controlled, tmp_path):
    controller, factory = controlled
    old = controller.config.run_dir / "restart-old" / "home.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old unrelated screenshot")
    assert controller.status()["has_frame"] is False
    controller.enqueue("restart")
    eventually(lambda: len(factory.processes) == 1)
    assert controller.status()["has_frame"] is False
    frame = factory.processes[0].run_dir / "trace-00.png"
    frame.write_bytes(b"current job screenshot")
    assert controller.frame_bytes() == b"current job screenshot"
    controller.enqueue("daily")
    factory.processes[0].finish()
    eventually(lambda: len(factory.processes) == 2)
    assert controller.status()["has_frame"] is False
    with pytest.raises(ApiError) as error:
        controller.frame_bytes()
    assert error.value.status == 404


def test_close_stops_child_without_dispatching_remaining_queue(controlled):
    controller, factory = controlled
    controller.enqueue("restart")
    eventually(lambda: len(factory.processes) == 1)
    controller.enqueue("daily")
    controller.close()
    assert factory.processes[0].signals
    assert len(factory.processes) == 1
    assert not controller._worker.is_alive()


@pytest.fixture
def http_server(controlled):
    controller, factory = controlled
    server = create_server(controller, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        supplied = {"Content-Type": "application/json"}
        supplied.update(headers or {})
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=supplied)
        response = connection.getresponse()
        data = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        return response.status, json.loads(data) if "application/json" in content_type else data

    try:
        yield request, controller, factory, server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_mutations_require_token_and_reject_foreign_host_and_origin(http_server):
    request, controller, factory, port = http_server
    code, status = request("GET", "/api/status")
    assert code == 200
    token = {"X-CSRF-Token": status["csrf_token"]}
    assert request("POST", "/api/run", {"task": "restart"})[0] == 403
    assert request("POST", "/api/run", {"task": "restart"},
                   {**token, "Host": f"attacker.example:{port}"})[0] == 403
    assert request("POST", "/api/run", {"task": "restart"},
                   {**token, "Origin": "https://attacker.example"})[0] == 403
    assert request("GET", "/api/status", headers={"Host": "attacker.example"})[0] == 403
    assert request("POST", "/api/pause", {}, token)[0] == 200
    assert request("POST", "/api/run", {"task": "restart"},
                   {**token, "Origin": f"http://127.0.0.1:{port}"})[0] == 200
    assert factory.processes == []


def test_http_rejects_arbitrary_commands_settings_and_file_paths(http_server):
    request, controller, _, _ = http_server
    token = {"X-CSRF-Token": controller.csrf_token}
    assert request("POST", "/api/run", {"task": "restart; rm -rf /"}, token)[0] == 400
    assert request("POST", "/api/run", {"task": "restart", "path": "/tmp/config"}, token)[0] == 400
    assert request("POST", "/api/settings", {"adb_path": "other"}, token)[0] == 400
    assert request("POST", "/api/settings", {"auto_download": "yes"}, token)[0] == 400
    assert request("GET", "/api/frame?path=/etc/passwd")[0] == 400
    assert request("GET", "/../../etc/passwd")[0] == 404
    assert request("GET", "/config/local.toml")[0] == 404
    assert request("GET", "/api/frame?v=anything")[0] == 404


def test_dashboard_serves_branded_page_and_only_the_explicit_mascot_asset(http_server):
    request, _, _, _ = http_server
    code, page = request("GET", "/")
    assert code == 200 and b"Maid in Schale" in page and b"Blue Archive Automator" in page
    code, png = request("GET", "/maid-arisu.png")
    assert code == 200 and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert request("GET", "/maid-arisu.png/../config/local.toml")[0] == 404


def test_non_loopback_binding_is_rejected(controlled):
    controller, _ = controlled
    with pytest.raises(ValueError, match="loopback"):
        create_server(controller, host="0.0.0.0", port=0)


def test_read_only_capture_validates_foreground_and_is_blocked_by_running_job(config_path):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, encoded = cv2.imencode(".png", image)
    calls = []

    class Device:
        def __init__(self, config):
            self.config = config

        def connect(self):
            calls.append("connect")

        def verify_package(self):
            calls.append("verify_package")

        def foreground_package(self):
            return self.config.package

        def screenshot(self):
            calls.append("screenshot")
            return encoded.tobytes()

    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, device_factory=Device)
    try:
        assert controller.capture() == {"captured": True}
        assert calls == ["connect", "verify_package", "screenshot"]
        assert controller.status()["has_frame"] is True
        assert controller.frame_bytes().startswith(b"\x89PNG")
        controller.enqueue("restart")
        eventually(lambda: len(factory.processes) == 1)
        with pytest.raises(ApiError) as error:
            controller.capture()
        assert error.value.status == 409
    finally:
        controller.close()


def test_popup_events_upsert_before_after_and_never_serve_unmanaged_paths(http_server, tmp_path):
    request, controller, factory, _ = http_server
    job = controller.enqueue("restart")
    eventually(lambda: len(factory.processes) == 1)
    run = factory.processes[0].run_dir
    identifier = "a" * 32
    images = run / "popups"
    images.mkdir()
    (images / f"{identifier}-before.png").write_bytes(b"before screenshot")
    journal = run / "events.jsonl"
    initial = {"event": "popup_dismissal", "id": identifier, "detail": "Dismiss a known news popup",
               "detector": "notice_close", "time": "2026-09-24T01:02:03Z",
               "before": f"popups/{identifier}-before.png", "after": None, "result": "pending"}
    journal.write_text(json.dumps(initial) + "\n", encoding="utf-8")
    code, response = request("GET", "/api/popups")
    assert code == 200
    assert response["scope"] == "current_session"
    assert response["popups"][0]["job_id"] == job["id"]
    assert response["popups"][0]["after_url"] is None
    assert request("GET", response["popups"][0]["before_url"])[1] == b"before screenshot"
    (images / f"{identifier}-after.png").write_bytes(b"after screenshot")
    with journal.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "popup_dismissal", "id": identifier,
                                 "after": f"popups/{identifier}-after.png", "after_state": "home",
                                 "result": "changed"}) + "\n")
    _, response = request("GET", "/api/popups")
    assert len(response["popups"]) == 1
    record = response["popups"][0]
    assert record["result"] == "changed"
    assert record["detail"] == initial["detail"]
    assert record["before_url"] is not None
    assert request("GET", record["after_url"])[1] == b"after screenshot"
    assert request("GET", record["after_url"] + "?path=/etc/passwd")[0] == 400
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"must not be served")
    with journal.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "popup_dismissal", "id": "b" * 32,
                                 "before": str(secret)}) + "\n")
    assert request("GET", "/api/popups/" + "b" * 32 + "/before")[0] == 404
    assert request("GET", "/api/popups/../../secret.png/before")[0] == 404


def test_cafe_settings_are_persisted_in_cafe_table_and_snapshots_keep_state_dir(controlled, config_path):
    controller, factory = controlled
    controller.pause()
    controller.update_settings({"cafe_schedule_enabled": True, "cafe_invite_enabled": True,
                                "cafe_invite_student": "Hoshino"})
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert document["cafe"] == {"schedule_enabled": True, "invite_enabled": True, "invite_student": "Hoshino"}
    controller.enqueue("daily")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    assert factory.processes[0].arguments[-1] == "daily"
    assert controller.status()["queue"] == []  # A due cafe does not duplicate an existing daily plan.
    snapshot = Config.from_file(factory.processes[0].arguments[4])
    assert snapshot.state_dir == controller.config.state_dir
    assert snapshot.cafe_invite_student == "Hoshino"


def test_schedule_persists_success_and_waits_three_hours_fifteen_seconds(config_path):
    now = [datetime(2026, 9, 24, 12, tzinfo=timezone.utc)]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        controller.update_settings({"cafe_schedule_enabled": True})
        eventually(lambda: len(factory.processes) == 1)
        assert factory.processes[0].arguments[-1] == "cafe"
        factory.processes[0].finish()
        eventually(lambda: controller.status()["state"] == "success")
        schedule = controller.status()["schedule"]["cafe"]
        assert datetime.fromisoformat(schedule["next_due_at"]) == now[0] + CAFE_INTERVAL
    finally:
        controller.close()
    # Scheduling survives a server restart and does not immediately run the cafe again.
    second_factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=second_factory, wall_clock=lambda: now[0])
    try:
        controller.resume()
        assert second_factory.processes == []
        now[0] += CAFE_INTERVAL
        controller.resume()
        eventually(lambda: len(second_factory.processes) == 1)
        assert second_factory.processes[0].arguments[-1] == "cafe"
    finally:
        controller.close()


def test_scheduled_failures_back_off_and_stop_retrying_until_resume(config_path):
    now = [datetime(2026, 9, 24, 12, tzinfo=timezone.utc)]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        controller.update_settings({"cafe_schedule_enabled": True})
        for attempt in range(3):
            eventually(lambda: len(factory.processes) == attempt + 1)
            factory.processes[attempt].finish(1)
            eventually(lambda: controller.status()["current_job"] is None)
            state = controller.status()["schedule"]["cafe"]
            assert state["consecutive_failures"] == attempt + 1
            assert datetime.fromisoformat(state["next_due_at"]) == now[0] + timedelta(minutes=15)
            if attempt < 2:
                now[0] += timedelta(minutes=15)
                controller.resume()
        assert controller.status()["schedule"]["cafe"]["retry_paused"] is True
        assert len(factory.processes) == 3
        controller.resume()
        eventually(lambda: len(factory.processes) == 4)
        assert controller.status()["schedule"]["cafe"]["retry_paused"] is False
    finally:
        controller.close()


def test_cancel_scheduled_cafe_skips_and_persists_only_that_occurrence(config_path):
    now = [datetime(2026, 9, 24, 12, tzinfo=timezone.utc)]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        controller.pause()
        controller.update_settings({"cafe_schedule_enabled": True})
        controller.enqueue("restart")
        controller.resume()
        eventually(lambda: len(factory.processes) == 1)
        queued = controller.status()["queue"]
        assert len(queued) == 1 and queued[0]["task"] == "cafe" and queued[0]["source"] == "schedule"
        controller.cancel(queued[0]["id"])
        assert controller.status()["queue"] == []
        schedule = controller.status()["schedule"]["cafe"]
        assert schedule["last_success_at"] is None
        assert datetime.fromisoformat(schedule["next_due_at"]) == now[0] + CAFE_INTERVAL
        saved = json.loads((controller.config.state_dir / "schedule.json").read_text(encoding="utf-8"))
        assert saved["cafe"]["next_due_at"] == schedule["next_due_at"]
        factory.processes[0].finish()
        eventually(lambda: controller.status()["current_job"] is None)
        assert len(factory.processes) == 1
        assert controller.status()["queue"] == []
        now[0] += CAFE_INTERVAL
        controller.resume()
        eventually(lambda: len(factory.processes) == 2)
        assert factory.processes[1].arguments[-1] == "cafe"
    finally:
        controller.close()


def test_cancel_manual_cafe_does_not_change_schedule(controlled):
    controller, _ = controlled
    controller.pause()
    before = controller.status()["schedule"]["cafe"]
    job = controller.enqueue("cafe")
    controller.cancel(job["id"])
    assert controller.status()["schedule"]["cafe"] == before


def test_important_actions_persist_and_exclude_evidence_filesystem_paths(http_server):
    request, controller, _, _ = http_server
    state = controller.config.state_dir
    state.mkdir(parents=True)
    path = state / "important-actions.jsonl"
    first = {"id": "first", "time": "2026-09-24T01:00:00Z", "task": "cafe", "action": "pat",
             "detail": "Patted a visiting student", "student": "Hoshino", "cafe": 1,
             "evidence_path": "/private/screenshot.png"}
    second = {**first, "id": "second", "action": "invite", "student": "Shiroko"}
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\npartial line", encoding="utf-8")
    code, response = request("GET", "/api/actions")
    assert code == 200
    assert [record["id"] for record in response["actions"]] == ["second", "first"]
    assert response["actions"][0]["student"] == "Shiroko"
    assert "evidence_path" not in response["actions"][0]


class ClosingDeviceFactory:
    """Never contacts ADB; checks cleanup cannot overlap the subprocess."""

    def __init__(self, processes):
        self.processes = processes
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.failure = None

    def __call__(self, config):
        factory = self

        class Device:
            def connect(self):
                assert factory.processes.active == 0, "closed the game while a task was running"
                factory.calls.append(("connect", config.serial))

            def verify_package(self):
                factory.calls.append(("verify_package", config.package))

            def force_stop(self):
                assert factory.processes.active == 0, "force-stop overlapped a task"
                factory.started.set()
                assert factory.release.wait(timeout=3), "test did not release game closure"
                if factory.failure:
                    raise factory.failure
                factory.calls.append(("force_stop", config.package))

        return Device()


@pytest.fixture
def close_controlled(config_path):
    processes = ProcessFactory()
    devices = ClosingDeviceFactory(processes)
    controller = DashboardController(config_path, process_factory=processes, device_factory=devices)
    try:
        yield controller, processes, devices
    finally:
        devices.release.set()
        controller.close()


def test_close_app_setting_defaults_off_and_validates_persists_and_snapshots(close_controlled, config_path):
    controller, processes, devices = close_controlled
    assert controller.config.close_app_when_idle is False
    assert controller.status()["config"]["close_app_when_idle"] is False
    for invalid in ("true", 1, 0, None):
        with pytest.raises(ConfigError, match="close_app_when_idle"):
            replace(controller.config, close_app_when_idle=invalid)
        original = config_path.read_bytes()
        with pytest.raises(ApiError) as error:
            controller.update_settings({"close_app_when_idle": invalid})
        assert error.value.status == 400
        assert config_path.read_bytes() == original
    controller.update_settings({"close_app_when_idle": True})
    saved = tomllib.loads(config_path.read_text())
    assert saved["automation"] == {"close_app_when_idle": True}
    assert Config.from_file(config_path).close_app_when_idle is True
    assert controller.status()["config"]["close_app_when_idle"] is True
    # Enabling the setting does not close an already-open game before a task runs.
    controller.resume()
    assert devices.calls == []
    controller.enqueue("restart")
    eventually(lambda: len(processes.processes) == 1)
    snapshot = Config.from_file(processes.processes[0].arguments[4])
    assert snapshot.close_app_when_idle is True
    assert devices.calls == []
    processes.processes[0].finish()
    eventually(lambda: controller.status()["app_closed"])


def test_default_off_never_connects_to_close_app(close_controlled):
    controller, processes, devices = close_controlled
    controller.enqueue("restart")
    eventually(lambda: len(processes.processes) == 1)
    assert devices.calls == []
    processes.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    assert controller.status()["app_closed"] is False
    assert devices.calls == []


def test_two_queued_jobs_close_only_after_last_exits_and_record_the_action(close_controlled):
    controller, processes, devices = close_controlled
    controller.update_settings({"close_app_when_idle": True})
    controller.pause()
    controller.enqueue("restart")
    controller.enqueue("cafe")
    controller.resume()
    eventually(lambda: len(processes.processes) == 1)
    assert devices.calls == []
    processes.processes[0].finish()
    eventually(lambda: len(processes.processes) == 2)
    assert devices.calls == []
    assert controller.status()["app_closed"] is False
    processes.processes[1].finish()
    eventually(lambda: controller.status()["app_closed"])
    assert devices.calls == [("connect", "127.0.0.1:5695"),
                             ("verify_package", "com.nexon.bluearchive"),
                             ("force_stop", "com.nexon.bluearchive")]
    status = controller.status()
    assert status["state"] == "success"
    assert "Blue Archive closed" in status["phase"]
    assert processes.max_active == 1
    actions = controller.actions()["actions"]
    assert len(actions) == 1
    assert actions[0]["task"] == "system"
    assert actions[0]["action"] == "app_closed"
    # Idle wakeups do not repeatedly close a game the user may reopen manually.
    controller.pause()
    controller.resume()
    assert len(devices.calls) == 3


@pytest.mark.parametrize("result", ["failed", "stopped"])
def test_finished_failed_or_stopped_job_can_close_an_empty_queue(close_controlled, result):
    controller, processes, devices = close_controlled
    controller.update_settings({"close_app_when_idle": True})
    controller.enqueue("restart")
    eventually(lambda: len(processes.processes) == 1)
    if result == "failed":
        processes.processes[0].finish(1)
    else:
        controller.stop()
    eventually(lambda: controller.status()["app_closed"])
    assert controller.status()["state"] == result
    assert devices.calls[-1] == ("force_stop", controller.config.package)


def test_paused_pending_queue_keeps_app_open_after_current_job(close_controlled):
    controller, processes, devices = close_controlled
    controller.update_settings({"close_app_when_idle": True})
    controller.enqueue("restart")
    eventually(lambda: len(processes.processes) == 1)
    controller.enqueue("cafe")
    controller.stop()
    eventually(lambda: controller.status()["state"] == "stopped")
    assert len(controller.status()["queue"]) == 1
    assert controller.status()["app_closed"] is False
    assert devices.calls == []


def test_lesson_settings_validate_persist_and_snapshot_before_dispatch(controlled, config_path):
    controller, factory = controlled
    controller.pause()
    original = config_path.read_bytes()
    for invalid in ({"lessons_strategy": "anything"}, {"lessons_max_tickets": True},
                    {"lessons_locations": ["Gehenna", "gehenna"]},
                    {"lessons_enabled_in_daily": "true"}):
        with pytest.raises(ApiError) as error:
            controller.update_settings(invalid)
        assert error.value.status == 400
        assert config_path.read_bytes() == original
    changes = {"lessons_strategy": "school_rank", "lessons_max_tickets": 2,
               "lessons_locations": ["Gehenna Academy"], "lessons_enabled_in_daily": False}
    controller.update_settings(changes)
    document = tomllib.loads(config_path.read_text())
    assert document["lessons"] == {"strategy": "school_rank", "max_tickets": 2,
                                   "locations": ["Gehenna Academy"], "enabled_in_daily": False}
    controller.enqueue("lessons")
    with pytest.raises(ApiError) as error:
        controller.update_settings({"lessons_max_tickets": 1})
    assert error.value.status == 409
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    snapshot = Config.from_file(factory.processes[0].arguments[4])
    assert snapshot.lessons_strategy == "school_rank"
    assert snapshot.lessons_max_tickets == 2
    assert snapshot.lessons_locations == ("Gehenna Academy",)
    assert snapshot.lessons_enabled_in_daily is False
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    assert controller.status()["phase"] == "Lessons complete"


def test_lessons_obeys_fifo_stop_and_closes_only_after_remaining_job(close_controlled):
    controller, factory, devices = close_controlled
    controller.update_settings({"close_app_when_idle": True})
    controller.pause()
    controller.enqueue("lessons")
    remaining = controller.enqueue("cafe")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    assert factory.processes[0].arguments[-1] == "lessons"
    controller.stop()
    eventually(lambda: controller.status()["state"] == "stopped")
    assert controller.status()["queue"][0]["id"] == remaining["id"]
    assert devices.calls == []
    assert controller.status()["queue_paused"]
    controller.resume()
    eventually(lambda: len(factory.processes) == 2)
    assert factory.processes[1].arguments[-1] == "cafe"
    factory.processes[1].finish()
    eventually(lambda: controller.status()["app_closed"])
    assert factory.max_active == 1
    assert len([call for call in devices.calls if call[0] == "force_stop"]) == 1


def test_lesson_frame_and_action_metadata_are_visible_without_private_paths(controlled):
    controller, factory = controlled
    controller.enqueue("lessons")
    eventually(lambda: len(factory.processes) == 1)
    folder = factory.processes[0].run_dir.parent / "lessons-test"
    folder.mkdir()
    (folder / "trace-0001.png").write_bytes(b"lesson frame")
    assert controller.frame_bytes() == b"lesson frame"
    state = controller.config.state_dir
    state.mkdir(parents=True)
    action = {"task": "lessons", "action": "lesson_completed", "location": "Gehenna Academy",
              "room": "Classroom", "owned_students": 3, "student_count": 4,
              "tickets_before": 7, "tickets_after": 6, "strategy": "relationship",
              "rank_before": 5, "rank_after": 6, "xp_before": 80, "xp_after": 10,
              "evidence_path": "/private/lesson.png"}
    (state / "important-actions.jsonl").write_text(json.dumps(action) + "\n")
    public = controller.actions()["actions"][0]
    assert all(public[key] == value for key, value in action.items() if key != "evidence_path")
    assert "evidence_path" not in public
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")


@pytest.mark.parametrize("interrupted", [False, True])
def test_later_lesson_failure_or_stop_keeps_verified_daily_cafe_success(config_path, interrupted):
    class DailyOutput(FakeOutput):
        def __iter__(self):
            yield "12:00:00 cafe: visit completed\n"
            yield json.dumps({"status": "success", "run_dir": str(self.process.run_dir.parent / "cafe-complete"),
                              "duration": 40, "actions": 3}) + "\n"
            self.process.done.wait()
            yield "Error: lesson receipt was not recognized\n"

    class DailyFactory(ProcessFactory):
        def __call__(self, arguments, **options):
            process = super().__call__(arguments, **options)
            process.stdout = DailyOutput(process)
            return process

    factory = DailyFactory()
    now = datetime(2026, 9, 24, 2, 0, tzinfo=timezone.utc)
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: now)
    try:
        controller.enqueue("daily")
        eventually(lambda: len(factory.processes) == 1)
        if interrupted:
            controller.stop()
        else:
            factory.processes[0].finish(1)
        eventually(lambda: controller.status()["state"] in {"failed", "stopped"})
        schedule = controller.status()["schedule"]["cafe"]
        assert schedule["last_success_at"] == now.isoformat()
        assert schedule["next_due_at"] == (now + CAFE_INTERVAL).isoformat()
        assert schedule["consecutive_failures"] == 0
    finally:
        controller.close()


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_or_stopped_lesson_result_uses_its_journal_not_successful_restart(config_path, interrupted):
    class MultiStepOutput(FakeOutput):
        def __iter__(self):
            (self.process.run_dir / "events.jsonl").write_text(json.dumps({
                "event": "finished", "status": "success", "elapsed": 50.0, "actions": 2}) + "\n")
            yield json.dumps({"status": "success", "run_dir": str(self.process.run_dir),
                              "duration": 50.0, "actions": 2}) + "\n"
            self.process.done.wait()
            lessons = self.process.run_dir.parent / "lessons-failed"
            lessons.mkdir()
            (lessons / "events.jsonl").write_text(json.dumps({
                "event": "finished", "status": "stopped" if interrupted else "failed",
                "elapsed": 224.059, "actions": 46, "reason": "receipt could not be verified"}) + "\n")
            yield "Error: receipt could not be verified\n"

    class MultiStepFactory(ProcessFactory):
        def __call__(self, arguments, **options):
            process = super().__call__(arguments, **options)
            process.stdout = MultiStepOutput(process)
            return process

    factory = MultiStepFactory()
    controller = DashboardController(config_path, process_factory=factory)
    try:
        controller.enqueue("lessons")
        eventually(lambda: len(factory.processes) == 1)
        if interrupted:
            controller.stop()
        else:
            factory.processes[0].finish(1)
        expected = "stopped" if interrupted else "failed"
        eventually(lambda: controller.status()["state"] == expected)
        result = controller.status()["result"]
        assert result["status"] == expected
        assert Path(result["run_dir"]).name == "lessons-failed"
        assert result["duration"] == 224.059
        assert result["actions"] == 46
        assert result["reason"] == "receipt could not be verified"
    finally:
        controller.close()


def test_incomplete_failed_task_journal_keeps_unknown_counters_null(tmp_path):
    completed = tmp_path / "restart-done"
    completed.mkdir()
    earlier = completed / "events.jsonl"
    earlier.write_text(json.dumps({"event": "finished", "status": "success", "elapsed": 10, "actions": 2}))
    current = tmp_path / "lessons-killed"
    current.mkdir()
    latest = current / "events.jsonl"
    latest.write_text(json.dumps({"event": "started", "task": "lessons", "elapsed": 0}) + '\n{"event": "fin')
    os.utime(earlier, ns=(1_000_000_000, 1_000_000_000))
    os.utime(latest, ns=(2_000_000_000, 2_000_000_000))
    result = DashboardController._unfinished_result(tmp_path, "failed")
    assert result == {"status": "failed", "run_dir": str(current.resolve()), "duration": None, "actions": None}


def test_failure_between_tasks_does_not_relabel_last_successful_task(tmp_path):
    run = tmp_path / "restart-done"
    run.mkdir()
    (run / "events.jsonl").write_text(json.dumps({
        "event": "finished", "status": "success", "elapsed": 50, "actions": 2}))
    assert DashboardController._unfinished_result(tmp_path, "failed") == {
        "status": "failed", "run_dir": None, "duration": None, "actions": None}


def test_failed_result_reads_only_a_bounded_journal_tail_and_does_not_invent_bad_counters(tmp_path):
    run = tmp_path / "lessons-failed"
    run.mkdir()
    (run / "events.jsonl").write_text('x' * 100_000 + '\n' + json.dumps({
        "event": "finished", "status": "failed", "elapsed": float("nan"), "actions": True,
        "reason": "lesson verification failed"}) + '\n')
    result = DashboardController._unfinished_result(tmp_path, "failed")
    assert result["run_dir"] == str(run.resolve())
    assert result["duration"] is None and result["actions"] is None
    assert result["reason"] == "lesson verification failed"


def test_failed_result_does_not_read_a_journal_outside_current_job(tmp_path):
    root = tmp_path / "current"
    root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"event": "finished", "status": "failed", "elapsed": 1, "actions": 99}))
    run = root / "lessons-link"
    run.mkdir()
    try:
        (run / "events.jsonl").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    assert DashboardController._unfinished_result(root, "failed") == {
        "status": "failed", "run_dir": None, "duration": None, "actions": None}


def test_instance_lock_conflict_sends_no_close_input_and_preserves_job_success(close_controlled):
    controller, processes, devices = close_controlled
    controller.update_settings({"close_app_when_idle": True})
    with InstanceLock(controller.config):
        controller.enqueue("restart")
        eventually(lambda: len(processes.processes) == 1)
        processes.processes[0].finish()
        eventually(lambda: controller.status()["state"] == "success")
        assert devices.calls == []
        assert controller.status()["app_closed"] is False
        assert any("Could not close Blue Archive" in entry["message"]
                   for entry in controller.status()["logs"])
    controller.resume()
    assert devices.calls == []


def test_close_failure_is_logged_without_changing_completed_job_status(close_controlled):
    controller, processes, devices = close_controlled
    devices.failure = RuntimeError("fake disconnected instance")
    controller.update_settings({"close_app_when_idle": True})
    controller.enqueue("restart")
    eventually(lambda: len(processes.processes) == 1)
    processes.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    status = controller.status()
    assert status["history"][0]["state"] == "success"
    assert status["app_closed"] is False
    assert controller.actions()["actions"] == []
    assert any("fake disconnected instance" in entry["message"] for entry in status["logs"])


def test_enqueue_waits_for_close_before_dispatch_and_clears_closed_status(close_controlled):
    controller, processes, devices = close_controlled
    controller.update_settings({"close_app_when_idle": True})
    devices.release.clear()
    controller.enqueue("restart")
    eventually(lambda: len(processes.processes) == 1)
    processes.processes[0].finish()
    assert devices.started.wait(timeout=2)
    enqueue_started = threading.Event()
    enqueue_done = threading.Event()

    def enqueue_during_close():
        enqueue_started.set()
        controller.enqueue("cafe")
        enqueue_done.set()

    thread = threading.Thread(target=enqueue_during_close)
    thread.start()
    try:
        assert enqueue_started.wait(timeout=1)
        assert not enqueue_done.wait(timeout=0.05)
        assert len(processes.processes) == 1
        devices.release.set()
        thread.join(timeout=2)
        assert enqueue_done.is_set()
        eventually(lambda: len(processes.processes) == 2)
        assert controller.status()["app_closed"] is False
        assert processes.max_active == 1
        assert [name for name, _ in devices.calls].count("force_stop") == 1
        processes.processes[1].finish()
        eventually(lambda: controller.status()["app_closed"])
        assert [name for name, _ in devices.calls].count("force_stop") == 2
    finally:
        devices.release.set()
        thread.join(timeout=2)
