"""One-click catch-up is serial, bounded, and keeps existing spending holds."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from ba_automator import ap_state, crafting_state, daily_schedule, packs_state
from ba_automator.server import ApiError, DashboardController
from ba_automator.tasks import task_plan
from test_server import FakeOutput, ProcessFactory, config_path, controlled, eventually, http_server  # noqa: F401


NOW = datetime(2026, 9, 26, 16, tzinfo=timezone.utc)


def configure(controller, **changes):
    controller.pause()
    changes.setdefault("tactical_battles_enabled_in_daily", False)
    with controller._condition:
        controller._wall_clock = lambda: NOW
        controller.config = replace(controller.config, **changes)


def occurrence(status):
    return {"version": 1, "game_day": "2026-09-25", "status": status,
            "run_id": "previous-daily", "started_at": (NOW - timedelta(hours=1)).isoformat(),
            "completed_at": None if status == "running" else NOW.isoformat()}


def test_paused_idle_click_scans_now_with_timers_disabled_and_coalesces(controlled):
    controller, factory = controlled
    configure(controller, checkin_schedule_enabled=False)
    original_config = controller.config
    with controller._condition:
        first = controller.run_available()
        second = controller.run_available()
        assert [job["task"] for job in first["jobs"]] == ["red_dots"]
        assert first["active"] and not first["pending"]
        assert second["jobs"] == [] and second["active"]
        assert not controller._paused
    eventually(lambda: len(factory.processes) == 1)
    assert factory.processes[0].arguments[-1] == "red_dots"
    assert controller.config == original_config
    assert controller.status()["run_available_active"]
    factory.processes[0].finish()
    eventually(lambda: not controller.status()["run_available_active"])
    assert factory.max_active == 1
    assert len(factory.processes) == 1


def test_active_job_and_queued_work_finish_before_one_catchup_scan(controlled):
    controller, factory = controlled
    configure(controller)
    controller.enqueue("restart")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    controller.enqueue("tasks")
    first = controller.run_available()
    again = controller.run_available()
    assert first["pending"] and again["pending"]
    assert first["jobs"] == again["jobs"] == []
    assert controller.status()["run_available_pending"]
    factory.processes[0].finish()
    eventually(lambda: len(factory.processes) == 2)
    assert factory.processes[1].arguments[-1] == "tasks"
    assert controller.status()["run_available_pending"]
    factory.processes[1].finish()
    eventually(lambda: len(factory.processes) == 3)
    assert factory.processes[2].arguments[-1] == "red_dots"
    assert not controller.status()["run_available_pending"]
    assert controller.run_available()["jobs"] == []
    factory.processes[2].finish()
    eventually(lambda: not controller.status()["run_available_active"])
    assert factory.max_active == 1
    assert len(factory.processes) == 3


def test_available_runs_enabled_tactical_battles_before_reward_scan_once(controlled):
    controller, factory = controlled
    configure(controller, tactical_battles_enabled_in_daily=True,
              tactical_battles_preserve_tickets=1)
    with controller._condition:
        result = controller.run_available()
        assert [job["task"] for job in result["jobs"]] == ["tactical_battles", "red_dots"]
        assert all(job["source"] == "available" for job in result["jobs"])
        assert controller.run_available()["jobs"] == []
    eventually(lambda: len(factory.processes) == 1)
    assert factory.processes[0].arguments[-1] == "tactical_battles"
    assert task_plan("tactical_battles", controller.config) == (
        "restart", "tactical_battles", "tactical_rewards", "red_dots")
    factory.processes[0].finish()
    eventually(lambda: len(factory.processes) == 2)
    assert factory.processes[1].arguments[-1] == "red_dots"
    factory.processes[1].finish()
    eventually(lambda: not controller.status()["run_available_active"])
    assert factory.max_active == 1 and len(factory.processes) == 2


def test_available_daily_plan_covers_tactical_battles_without_duplicate_job(controlled):
    controller, _ = controlled
    configure(controller, daily_schedule_enabled=True, tactical_battles_enabled_in_daily=True)
    result = controller.run_available()
    assert [job["task"] for job in result["jobs"]] == ["daily", "red_dots"]
    plan = task_plan("daily", controller.config)
    assert plan.count("tactical_battles") == 1
    assert plan.index("tactical_battles") < plan.index("tactical_rewards")


def test_available_tactical_catchup_does_not_replay_completed_daily(controlled):
    controller, _ = controlled
    configure(controller, daily_schedule_enabled=True, tactical_battles_enabled_in_daily=True)
    saved = occurrence("success")
    daily_schedule.write_state(controller.config, saved)
    result = controller.run_available()
    assert [job["task"] for job in result["jobs"]] == ["tactical_battles", "red_dots"]
    assert daily_schedule.read_state(controller.config) == saved


def test_pausing_before_batch_drains_retains_request_without_dispatch(controlled):
    controller, factory = controlled
    configure(controller)
    controller.enqueue("restart")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    controller.run_available()
    controller.pause()
    factory.processes[0].finish()
    eventually(lambda: controller.status()["current_job"] is None)
    status = controller.status()
    assert status["queue_paused"] and status["run_available_pending"]
    assert status["queue"] == [] and len(factory.processes) == 1
    controller.run_available()
    eventually(lambda: len(factory.processes) == 2)
    assert factory.processes[1].arguments[-1] == "red_dots"


def test_due_daily_and_crafting_reuse_opt_ins_and_cover_cafe_ap(controlled):
    controller, factory = controlled
    configure(controller, daily_schedule_enabled=True, cafe_schedule_enabled=True,
              crafting_schedule_enabled=True, ap_schedule_enabled=True, ap_floor=123,
              lessons_enabled_in_daily=False, bounties_enabled_in_daily=False,
              scrimmages_enabled_in_daily=False, total_assault_enabled_in_daily=False)
    result = controller.run_available()
    assert [job["task"] for job in result["jobs"]] == ["daily", "crafting", "red_dots"]
    assert all(job.get("source") == "schedule" for job in result["jobs"][:2])
    plan = task_plan("daily", controller.config)
    assert "spend_ap" in plan
    assert not {"packs", "lessons", "bounties", "scrimmages", "total_assault"} & set(plan)
    assert controller.config.ap_floor == 123
    eventually(lambda: len(factory.processes) == 1)
    assert factory.processes[0].arguments[-1] == "daily"
    assert daily_schedule.read_state(controller.config)["status"] == "running"
    assert "--retry-ap" not in factory.processes[0].arguments
    assert "--retry-packs" not in factory.processes[0].arguments


@pytest.mark.parametrize("status", ["success", "failed", "stopped", "running", "skipped"])
def test_catchup_never_replays_todays_daily_occurrence(controlled, status):
    controller, _ = controlled
    configure(controller, daily_schedule_enabled=True)
    saved = occurrence(status)
    daily_schedule.write_state(controller.config, saved)
    result = controller.run_available()
    assert [job["task"] for job in result["jobs"]] == ["red_dots"]
    assert daily_schedule.read_state(controller.config) == saved


def test_pending_spending_and_disabled_crafting_stay_held(controlled):
    controller, _ = controlled
    configure(controller, ap_schedule_enabled=True, packs_monthly_enabled=True,
              crafting_schedule_enabled=True)
    ap = {**ap_state.empty_state(), "blocked_reason": "receipt unresolved",
          "pending": {"strategy": "elephs", "stage": "7-1", "ap_before": 660,
                      "floor": 100, "count": 1, "cost": 20}}
    packs = {**packs_state.read_state(controller.config), "blocked_reason": "payment failed",
             "pending": {"pack": "monthly", "cents": 699}}
    craft = {**crafting_state.empty_state(), "disabled_reason": "Quick Craft is not configured"}
    ap_state.write_state(controller.config, ap)
    packs_state.write_state(controller.config, packs)
    crafting_state.write_state(controller.config, craft)
    controller._failures = [{"id": "old-failure", "task": "spend_ap", "detail": "review receipt"}]
    result = controller.run_available()
    assert [job["task"] for job in result["jobs"]] == ["red_dots"]
    assert ap_state.read_state(controller.config) == ap
    assert packs_state.read_state(controller.config) == packs
    assert crafting_state.read_state(controller.config) == craft
    assert controller._failures[0]["id"] == "old-failure"


def test_work_not_yet_due_is_not_forced_but_scan_is(controlled):
    controller, _ = controlled
    configure(controller, cafe_schedule_enabled=True, crafting_schedule_enabled=True,
              packs_monthly_enabled=True, ap_schedule_enabled=True)
    future = (NOW + timedelta(hours=1)).isoformat()
    controller._schedule["cafe"]["next_due_at"] = future
    crafting_state.write_state(controller.config, {**crafting_state.empty_state(),
        "slots": [{"slot": 1, "due_at": future}]})
    packs_state.write_state(controller.config, {**packs_state.read_state(controller.config),
                                                "next_check_at": future})
    ap_state.write_state(controller.config, {**ap_state.empty_state(), "next_check_at": future})
    assert [job["task"] for job in controller.run_available()["jobs"]] == ["red_dots"]


def test_previous_batch_scan_dedup_does_not_hide_new_available_claims(controlled):
    controller, _ = controlled
    configure(controller)
    controller._badge_attempted.update({"spend_ap", "free_pack", "tasks"})
    controller._ap_batch_observed = 800
    with controller._condition:
        controller.run_available()
        assert controller._badge_attempted == set()
        assert controller._ap_batch_observed is None


@pytest.mark.parametrize("held", [False, True])
def test_available_scan_follows_up_excess_ap_without_bypassing_hold(config_path, held):
    class ScanOutput(FakeOutput):
        def __iter__(self):
            self.process.done.wait()
            if self.process.returncode:
                return
            run = self.process.run_dir.parent / "red_dots-available"
            run.mkdir()
            (run / "requests.json").write_text(json.dumps({"version": 1, "tasks": [], "ap": 180}))
            yield json.dumps({"status": "success", "run_dir": str(run), "duration": 1, "actions": 0}) + "\n"

    class ScanFactory(ProcessFactory):
        def __call__(self, arguments, **options):
            process = super().__call__(arguments, **options)
            if arguments[-1] == "red_dots":
                process.stdout = ScanOutput(process)
            return process

    factory = ScanFactory()
    controller = DashboardController(config_path, process_factory=factory, wall_clock=lambda: NOW)
    try:
        configure(controller, ap_schedule_enabled=True, ap_floor=100, checkin_schedule_enabled=False)
        state = {**ap_state.empty_state(), "next_check_at": (NOW + timedelta(hours=1)).isoformat(),
                 "blocked_reason": "receipt unresolved" if held else None}
        ap_state.write_state(controller.config, state)
        controller.run_available()
        eventually(lambda: len(factory.processes) == 1)
        assert factory.processes[0].arguments[-1] == "red_dots"
        factory.processes[0].finish()
        if held:
            eventually(lambda: not controller.status()["run_available_active"])
            assert len(factory.processes) == 1
            assert ap_state.read_state(controller.config) == state
        else:
            eventually(lambda: len(factory.processes) == 2)
            assert factory.processes[1].arguments[-1] == "spend_ap"
            assert "--retry-ap" not in factory.processes[1].arguments
            assert controller.status()["current_job"]["source"] == "ap_balance"
            assert factory.max_active == 1
    finally:
        controller.close()


def test_http_available_requires_csrf_and_empty_body(http_server):
    request, controller, _, port = http_server
    configure(controller)
    token = {"X-CSRF-Token": controller.csrf_token}
    assert request("POST", "/api/run-available", {})[0] == 403
    assert request("POST", "/api/run-available", {},
                   {**token, "Origin": "https://example.com"})[0] == 403
    assert request("POST", "/api/run-available?task=packs", {}, token)[0] == 400
    assert request("POST", "/api/run-available", {"task": "packs"}, token)[0] == 400
    assert request("POST", "/api/run-available", {"retry": True}, token)[0] == 400
    code, result = request("POST", "/api/run-available", {},
                           {**token, "Origin": f"http://127.0.0.1:{port}"})
    assert code == 200 and result["ok"] and result["active"]
    assert [job["task"] for job in result["jobs"]] == ["red_dots"]
    assert not result["queue_paused"]


def test_shutdown_rejects_available_request(controlled):
    controller, _ = controlled
    controller.close()
    with pytest.raises(ApiError) as error:
        controller.run_available()
    assert error.value.status == 409


@pytest.mark.parametrize("name", ["maid-arisu-checklist.png", "maid-arisu-tea.png"])
def test_new_mascot_assets_are_explicit_static_files(http_server, name):
    request, _, _, _ = http_server
    code, body = request("GET", f"/{name}")
    assert code == 200 and body.startswith(b"\x89PNG\r\n\x1a\n")
    assert request("GET", f"/{name}/../config/local.toml")[0] == 404
