"""Daily isolates task failures without replaying uncertain spending."""

import json
from pathlib import Path

import pytest

from ba_automator.adb import DeviceError
from ba_automator.assault_state import AssaultStateError
from ba_automator.config import Config, ConfigError
from ba_automator.locking import LockError
from ba_automator.runtime import RunResult, TaskError
from ba_automator.task_execution import run_daily_plan


def result(task, status="success"):
    return RunResult(status, Path(f"/test/runs/{task}-test"), 1.0, 1)


def harness(errors=None, statuses=None):
    errors, statuses = errors or {}, statuses or {}
    calls, events = [], []

    def run(task):
        calls.append(task)
        if task in errors:
            raise errors[task]
        return result(task, statuses.get(task, "success"))

    return run, calls, events


def test_isolated_bounty_hold_does_not_prevent_scrimmages_or_ap():
    run, calls, events = harness({"bounties": TaskError("Earlier Bounty sweep is unresolved", Path("bounty-trace"))})
    summary = run_daily_plan(("restart", "bounties", "scrimmages", "spend_ap", "tasks"), run, events.append)
    assert calls == ["restart", "bounties", "restart", "scrimmages", "spend_ap", "tasks"]
    assert summary["status"] == "partial_failure" and not summary["aborted"]
    assert summary["completed_tasks"] == ["restart", "scrimmages", "spend_ap", "tasks"]
    assert summary["failed_tasks"] == [{
        "task": "bounties", "error": "Earlier Bounty sweep is unresolved", "error_type": "TaskError",
        "run_dir": "bounty-trace", "recoverable": True, "recovery": False,
    }]
    assert [event["type"] for event in events] == [
        "task_failure", "daily_recovery_started", "daily_recovery_finished", "command_summary",
    ]


def test_multiple_failures_each_get_one_navigation_recovery_and_no_task_retry():
    run, calls, events = harness({
        "bounties": TaskError("pending ticket intent", Path("bounties")),
        "lessons": TaskError("room not recognized", Path("lessons")),
        "spend_ap": TaskError("pending AP intent", Path("spend_ap")),
    })
    summary = run_daily_plan(("restart", "bounties", "lessons", "spend_ap", "tasks"), run, events.append)
    assert calls == ["restart", "bounties", "restart", "lessons", "restart", "spend_ap", "restart", "tasks"]
    assert [item["task"] for item in summary["failed_tasks"]] == ["bounties", "lessons", "spend_ap"]
    assert summary["completed_tasks"] == ["restart", "tasks"]


def test_final_notification_failure_does_not_trigger_pointless_recovery():
    run, calls, events = harness({"red_dots": TaskError("badge unreadable", Path("scan"))})
    summary = run_daily_plan(("restart", "spend_ap", "red_dots"), run, events.append)
    assert calls == ["restart", "spend_ap", "red_dots"]
    assert summary["status"] == "partial_failure"


@pytest.mark.parametrize("error", [
    DeviceError("adb is offline"), ConfigError("invalid endpoint"), LockError("device busy"),
    OSError("disk full"), ValueError("programming bug"), RuntimeError("unclassified failure"),
])
def test_infrastructure_and_unexpected_failures_abort_instead_of_continuing(error):
    run, calls, events = harness({"bounties": error})
    with pytest.raises(type(error), match=str(error)):
        run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties"]
    assert events[-1]["status"] == "failed" and events[-1]["aborted"]
    assert events[-1]["skipped_tasks"] == ["spend_ap"]
    assert events[-1]["failed_tasks"][0]["recoverable"] is False


@pytest.mark.parametrize("cause", [DeviceError("offline"), ConfigError("bad config"), LockError("locked"), OSError("disk full")])
def test_wrapped_fatal_errors_keep_their_meaning(cause):
    error = TaskError(f"Cafe stopped: {cause}", Path("cafe"))
    error.__cause__ = cause
    run, calls, events = harness({"cafe": error})
    with pytest.raises(TaskError):
        run_daily_plan(("restart", "cafe", "spend_ap"), run, events.append)
    assert calls == ["restart", "cafe"]
    assert not events[-1]["failed_tasks"][0]["recoverable"]


def test_startup_failure_never_reaches_spending_even_if_error_is_task_scoped():
    run, calls, events = harness({"restart": TaskError("login required", Path("restart"))})
    with pytest.raises(TaskError, match="login required"):
        run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart"]
    assert events[-1]["skipped_tasks"] == ["bounties", "spend_ap"]


@pytest.mark.parametrize("status", ["failed", "deferred", "disabled"])
def test_startup_must_verify_home_before_any_work(status):
    run, calls, events = harness(statuses={"restart": status})
    with pytest.raises(TaskError, match="verify the home screen"):
        run_daily_plan(("restart", "spend_ap"), run, events.append)
    assert calls == ["restart"]


def test_failed_recovery_stops_remaining_steps():
    calls, events = [], []

    def run(task):
        calls.append(task)
        if task == "bounties":
            raise TaskError("pending receipt", Path("bounties"))
        if calls.count("restart") == 2:
            raise TaskError("startup time limit", Path("recovery"))
        return result(task)

    with pytest.raises(TaskError, match="startup time limit"):
        run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties", "restart"]
    assert events[-1]["status"] == "failed"
    assert events[-1]["skipped_tasks"] == ["spend_ap"]
    assert events[-1]["failed_tasks"][-1]["recovery"] is True


@pytest.mark.parametrize("failure", [
    RuntimeError("Invalid ticket state; spending is blocked"),
    RuntimeError("Invalid AP state; spending is blocked"),
    AssaultStateError("A previous Total Assault ticket action is unresolved; inspect it before retrying"),
])
def test_explicit_state_refusals_remain_local_to_the_task(failure):
    run, calls, events = harness({"bounties": failure})
    summary = run_daily_plan(("restart", "bounties", "tasks"), run, events.append)
    assert calls == ["restart", "bounties", "restart", "tasks"]
    assert summary["status"] == "partial_failure"


def test_state_refusal_caused_by_filesystem_failure_remains_fatal():
    error = RuntimeError("Cannot read ticket state; inspect it before spending")
    error.__cause__ = OSError("permission denied")
    run, calls, events = harness({"bounties": error})
    with pytest.raises(RuntimeError):
        run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties"]


def test_interrupt_does_not_get_reinterpreted_as_a_recoverable_failure():
    run, calls, events = harness({"bounties": KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties"]
    assert events[-1]["status"] == "stopped"


def test_returned_failed_result_is_not_reported_as_overall_success():
    run, calls, events = harness(statuses={"bounties": "failed"})
    summary = run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties", "restart", "spend_ap"]
    assert summary["status"] == "partial_failure"


def test_returned_stop_blocks_remaining_tasks():
    run, calls, events = harness(statuses={"bounties": "stopped"})
    summary = run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties"]
    assert summary["status"] == "stopped"


@pytest.mark.parametrize("status", ["deferred", "disabled"])
def test_skipped_setup_gets_verified_home_before_unrelated_work(status):
    run, calls, events = harness(statuses={"bounties": status})
    summary = run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties", "restart", "spend_ap"]
    assert summary["status"] == "success"
    assert summary["deferred_tasks"] == [{"task": "bounties", "status": status}]
    assert "bounties" not in summary["completed_tasks"]


@pytest.mark.parametrize("status", ["failed", "disabled", "deferred", "stopped"])
def test_recovery_without_success_never_reaches_the_next_job(status):
    calls, events = [], []

    def run(task):
        calls.append(task)
        if task == "bounties":
            raise TaskError("pending receipt", Path("bounties"))
        return result(task, status if calls.count("restart") == 2 else "success")

    if status == "stopped":
        summary = run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
        assert summary["status"] == "stopped"
    else:
        with pytest.raises(TaskError, match="verify the home screen"):
            run_daily_plan(("restart", "bounties", "spend_ap"), run, events.append)
    assert calls == ["restart", "bounties", "restart"]
    assert events[-1]["skipped_tasks"] == ["spend_ap"]


def test_successful_daily_keeps_original_order_without_extra_restarts():
    run, calls, events = harness()
    plan = ("restart", "club", "bounties", "scrimmages", "lessons", "spend_ap", "tasks", "red_dots")
    summary = run_daily_plan(plan, run, events.append)
    assert calls == list(plan)
    assert summary["status"] == "success" and summary["failed_tasks"] == []
    assert events == [summary]


def json_messages(output):
    decoder = json.JSONDecoder()
    messages = []
    while output.strip():
        value, end = decoder.raw_decode(output.lstrip())
        messages.append(value)
        output = output.lstrip()[end:]
    return messages


def test_cli_partial_daily_keeps_pending_hold_and_runs_ap_once(monkeypatch, tmp_path, capsys):
    from ba_automator import cli, restart, tickets, spend_ap, red_dots

    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    run_dir=tmp_path / "runs", state_dir=tmp_path / "state", ap_schedule_enabled=True)
    config.state_dir.mkdir()
    hold = config.state_dir / "pending-test.json"
    hold.write_text('{"pending": {"stage": "I", "count": 2}}')
    original_hold = hold.read_bytes()
    monkeypatch.setattr(cli.Config, "from_file", lambda _: config)
    monkeypatch.setattr(cli, "AdbDevice", lambda _: object())
    monkeypatch.setattr(cli, "StartupVision", object)
    monkeypatch.setattr(cli, "task_plan", lambda *_: ("restart", "bounties", "spend_ap", "red_dots"))
    calls = []

    def startup(*_):
        calls.append("restart")
        return result("restart")

    def bounties(*_):
        calls.append("bounties")
        assert hold.read_bytes() == original_hold
        raise TaskError("Earlier Bounty sweep is unresolved", tmp_path / "bounties-trace")

    def ap(*_, allow_retry):
        calls.append("spend_ap")
        assert allow_retry is False
        return result("spend_ap")

    def scan(*_):
        calls.append("red_dots")
        return result("red_dots")

    monkeypatch.setattr(restart, "run_restart", startup)
    monkeypatch.setattr(tickets, "run_bounties", bounties)
    monkeypatch.setattr(spend_ap, "run_spend_ap", ap)
    monkeypatch.setattr(red_dots, "run_red_dots", scan)
    assert cli.main(["daily"]) == 1
    assert calls == ["restart", "bounties", "restart", "spend_ap", "red_dots"]
    assert hold.read_bytes() == original_hold
    captured = capsys.readouterr()
    summary = json_messages(captured.out)[-1]
    assert summary["type"] == "command_summary" and summary["status"] == "partial_failure"
    assert summary["completed_tasks"] == ["restart", "spend_ap", "red_dots"]
    assert "Error: Daily completed with failures: bounties:" in captured.err
    log = "".join(path.read_text() for path in (config.state_dir / "logs").glob("*.log"))
    assert "task_failure" in log and "daily_recovery_finished" in log and "partial_failure" in log
    assert "command_finished" in log and 'status="failed"' in log
