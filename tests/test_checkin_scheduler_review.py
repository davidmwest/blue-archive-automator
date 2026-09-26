"""Independent lifecycle and resource-hold checks for periodic home visits."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from ba_automator import ap_state, checkin_schedule
from ba_automator.config import Config
from ba_automator.locking import InstanceLock
from ba_automator.server import DashboardController
from test_server import ProcessFactory, eventually


NOW = datetime(2026, 9, 26, 16, tzinfo=timezone.utc)
INTERVAL = timedelta(minutes=5)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "local.toml"
    path.write_text(
        '[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
        '[checkin]\nschedule_enabled=true\ninterval_minutes=5\n',
        encoding="utf-8",
    )
    return path


def tick(controller):
    with controller._condition:
        controller._enqueue_scheduled()
        controller._condition.notify_all()


def state_lock(config):
    return InstanceLock(replace(config, lock_dir=config.lock_dir / "checkin-schedule"))


def test_startup_state_lock_contention_recovers_without_settings_change(config_path):
    clock = [NOW]
    factory = ProcessFactory()
    config = Config.from_file(config_path)
    # The timer already exists, as with another controller finishing a scan.
    checkin_schedule.write_state(config, checkin_schedule.initial_state(NOW, 5))
    with state_lock(config):
        controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
        assert not factory.processes
    try:
        clock[0] += INTERVAL
        tick(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert controller.status()["schedule"]["checkin"]["blocked_reason"] is None
        assert factory.processes[0].arguments[-1] == "red_dots"
    finally:
        controller.close()


def test_completion_state_lock_contention_retries_the_saved_scan(config_path):
    clock = [NOW]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        clock[0] += timedelta(minutes=4)
        with state_lock(controller.config), controller._condition:
            controller._record_checkin("success")
        clock[0] += timedelta(minutes=1)
        tick(controller)
        eventually(lambda: checkin_schedule.read_state(controller.config)["status"] == "success")
        assert controller.status()["schedule"]["checkin"]["blocked_reason"] is None
        assert not factory.processes
        saved = checkin_schedule.read_state(controller.config)
        assert checkin_schedule.parse_time(saved["next_due_at"]) > clock[0]
    finally:
        controller.close()


@pytest.mark.parametrize("record_while_busy", [False, True])
def test_scan_before_deferred_first_initialization_does_not_hold_timer(config_path, record_while_busy):
    clock = [NOW]
    factory = ProcessFactory()
    config = Config.from_file(config_path)
    with state_lock(config):
        controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
        if record_while_busy:
            with controller._condition:
                controller._record_checkin("success")
    try:
        # Another ordinary job can finish before the initialization retry is due.
        if not record_while_busy:
            with controller._condition:
                controller._record_checkin("success")
        clock[0] += timedelta(minutes=1)
        tick(controller)
        eventually(lambda: (checkin_schedule.read_state(config) or {}).get("status") == "success")
        assert controller.status()["schedule"]["checkin"]["blocked_reason"] is None
        assert not factory.processes
    finally:
        controller.close()


def test_canceling_due_checkin_persists_one_interval_skip_across_restart(config_path):
    clock = [NOW]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        with controller._condition:
            clock[0] += INTERVAL
            controller._enqueue_checkin()
            assert len(controller._queue) == 1
            job = controller._queue[0]
            controller.pause()
            controller.cancel(job["id"])
        saved = checkin_schedule.read_state(controller.config)
        assert saved["status"] == "skipped"
        assert saved["next_due_at"] == (clock[0] + INTERVAL).isoformat()
        assert not factory.processes
    finally:
        controller.close()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        tick(controller)
        assert not factory.processes
        clock[0] += INTERVAL
        tick(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert controller.status()["queue"] == []
    finally:
        controller.close()


def test_pause_keeps_overdue_checkin_idle_until_resume(config_path):
    clock = [NOW]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        controller.pause()
        clock[0] += timedelta(days=2)
        tick(controller)
        assert controller.status()["queue"] == []
        assert not factory.processes
        controller.resume()
        eventually(lambda: len(factory.processes) == 1)
        tick(controller)
        assert controller.status()["queue"] == []
        assert factory.max_active == 1
    finally:
        controller.close()


def test_disabling_timer_rejects_an_already_prepared_claim(config_path):
    clock = [NOW]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        controller.pause()
        clock[0] += INTERVAL
        controller.update_settings({"checkin_schedule_enabled": False})
        saved = checkin_schedule.read_state(controller.config)
        with controller._condition:
            assert not controller._claim_checkin({"id": "stale-checkin", "task": "red_dots", "source": "checkin"})
        controller.resume()
        tick(controller)
        assert not factory.processes
        assert checkin_schedule.read_state(controller.config) == saved
    finally:
        controller.close()


def test_concurrent_controllers_cannot_claim_the_same_due_interval(config_path):
    clock = [NOW]
    factory = ProcessFactory()
    controllers = [DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
                   for _ in range(2)]
    try:
        for controller in controllers:
            controller.pause()
        clock[0] += INTERVAL
        jobs = [{"id": f"checkin-{index}", "task": "red_dots", "source": "checkin"}
                for index in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda pair: pair[0]._claim_checkin(pair[1]), zip(controllers, jobs)))
        assert sorted(claims) == [False, True]
        saved = checkin_schedule.read_state(controllers[0].config)
        assert saved["run_id"] == jobs[claims.index(True)]["id"]
        assert saved["status"] == "running"
        assert saved["next_due_at"] == (clock[0] + INTERVAL).isoformat()
        assert not factory.processes
    finally:
        for controller in controllers:
            controller.close()


def test_restart_and_clock_rollback_do_not_replay_a_claimed_interval(config_path):
    clock = [NOW]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        controller.pause()
        clock[0] += INTERVAL
        assert controller._claim_checkin({"id": "interrupted-claim", "task": "red_dots", "source": "checkin"})
    finally:
        controller.close()
    clock[0] = NOW - timedelta(hours=1)
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        tick(controller)
        assert not factory.processes
        clock[0] = NOW + INTERVAL
        tick(controller)
        assert not factory.processes
        clock[0] += INTERVAL
        tick(controller)
        eventually(lambda: len(factory.processes) == 1)
        assert controller.status()["queue"] == []
    finally:
        controller.close()


@pytest.mark.parametrize("hold", ["pending", "blocked", "pending_and_blocked"])
def test_periodic_scan_can_queue_mail_without_releasing_ap_spending_hold(config_path, hold):
    clock = [NOW]
    factory = ProcessFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: clock[0])
    try:
        controller.pause()
        controller.update_settings({"ap_schedule_enabled": True, "ap_floor": 100})
        saved = ap_state.empty_state()
        if "pending" in hold:
            saved["pending"] = {"strategy": "elephs", "stage": "8-3", "ap_before": 647,
                                "cost": 20, "count": 1, "floor": 100}
        if "blocked" in hold:
            saved["blocked_reason"] = "Review the previous sweep receipt before another spend"
        ap_state.write_state(controller.config, saved)
        original = ap_state.state_path(controller.config).read_bytes()
        run_root = controller.config.run_dir / "checkin-review"
        run = run_root / "red_dots-verified"
        run.mkdir(parents=True)
        (run / "requests.json").write_text(json.dumps({"version": 1, "tasks": ["mail"], "ap": 627}))
        output = json.dumps({"status": "success", "run_dir": str(run), "duration": 1, "actions": 0})
        with controller._condition:
            assert controller._enqueue_badges(output, run_root)
            assert controller._enqueue_badges(output, run_root)
        queue = controller.status()["queue"]
        assert [(job["task"], job["source"]) for job in queue] == [("mail", "red_dot")]
        assert ap_state.state_path(controller.config).read_bytes() == original
        assert checkin_schedule.read_state(controller.config)["status"] == "success"
        assert not factory.processes
    finally:
        controller.close()
