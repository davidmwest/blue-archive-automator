"""Tactical survey chunks yield the serial queue without replaying Daily."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ba_automator import daily_schedule, tactical_state, tactical_survey
from ba_automator.club import game_day
from ba_automator.server import DashboardController
from ba_automator.tactical_refresh import RefreshPlanner
from ba_automator.tactical_survey import continuation_due as saved_continuation_due
from test_server import ProcessFactory, eventually


NOW = datetime(2026, 9, 26, 16, tzinfo=timezone.utc)


@pytest.fixture
def controlled(tmp_path, monkeypatch):
    path = tmp_path / "local.toml"
    path.write_text(
        '[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
        '[checkin]\nschedule_enabled=false\n', encoding="utf-8")
    due = [False]
    monkeypatch.setattr(tactical_survey, "continuation_due", lambda config, *, now: due[0])
    factory = ProcessFactory()
    clock = [NOW]
    controller = DashboardController(path, process_factory=factory, wall_clock=lambda: clock[0])
    controller.pause()
    try:
        yield controller, factory, due, clock
    finally:
        controller.close()


def enqueue_due(controller):
    """Inspect enqueuing atomically, leaving the actual worker paused."""
    with controller._condition:
        controller._paused = False
        controller._enqueue_scheduled()
        controller._paused = True


def save_survey(config, now):
    confidence = config.tactical_battles_confidence_percent / 100
    pilot = config.tactical_battles_refresh_limit
    tactical_survey.save_survey(
        config, day_key=game_day(now), own_rank=500, confidence=confidence, pilot=pilot,
        planner=RefreshPlanner(pilot=pilot, confidence=confidence), candidates=[], identities={},
        now=now.timestamp())


def test_due_continuation_is_single_and_runs_as_its_own_task(controlled):
    controller, factory, due, _ = controlled
    before = daily_schedule.read_state(controller.config)
    due[0] = True
    enqueue_due(controller)
    enqueue_due(controller)
    queued = controller.status()["queue"]
    assert [(job["task"], job["source"]) for job in queued] == [
        ("tactical_battles", "tactical_continuation")]
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    with controller._condition:
        controller._enqueue_scheduled()
        assert not controller._queue
    assert factory.processes[0].arguments[-1] == "tactical_battles"
    due[0] = False  # The child saved a later continuation time.
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    assert daily_schedule.read_state(controller.config) == before
    assert controller.status()["history"][0]["source"] == "tactical_continuation"
    assert factory.max_active == 1


@pytest.mark.parametrize("task", ["daily", "tactical_battles"])
@pytest.mark.parametrize("running", [False, True])
def test_existing_daily_or_tactical_work_covers_continuation(controlled, task, running):
    controller, _, due, _ = controlled
    due[0] = True
    with controller._condition:
        if running:
            controller._current = {"id": "running", "task": task}
        else:
            controller.enqueue(task)
        enqueue_due(controller)
        assert not any(job.get("source") == "tactical_continuation" for job in controller._queue)
        controller._current = None


@pytest.mark.parametrize("gate", ["paused", "capturing", "shutdown", "disabled"])
def test_continuation_honors_dispatch_and_enable_gates(controlled, gate):
    controller, factory, due, _ = controlled
    due[0] = True
    with controller._condition:
        controller._paused = gate == "paused"
        if gate == "disabled":
            controller.config = replace(controller.config, tactical_battles_enabled_in_daily=False)
        elif gate != "paused":
            setattr(controller, f"_{gate}", True)
        controller._enqueue_tactical_continuation(NOW.timestamp())
        assert not controller._queue
        assert not factory.processes
        if gate == "shutdown":
            controller._shutdown = False
        controller._paused = True


def test_continuation_goes_after_existing_and_due_resource_jobs(controlled, monkeypatch):
    controller, _, due, _ = controlled
    due[0] = True
    controller.enqueue("crafting")
    controller.config = replace(controller.config, cafe_schedule_enabled=True, ap_schedule_enabled=True)
    checkin_seen = []
    monkeypatch.setattr(controller, "_enqueue_checkin",
                        lambda: checkin_seen.append([job["task"] for job in controller._queue]))
    enqueue_due(controller)
    assert [job["task"] for job in controller.status()["queue"]] == [
        "crafting", "spend_ap", "cafe", "tactical_battles"]
    assert checkin_seen == [["crafting", "spend_ap", "cafe", "tactical_battles"]]


@pytest.mark.parametrize("change", ["disabled", "expired"])
def test_queued_continuation_rechecks_enable_and_due_before_dispatch(controlled, change):
    controller, factory, due, clock = controlled
    due[0] = True
    enqueue_due(controller)
    if change == "disabled":
        controller.config = replace(controller.config, tactical_battles_enabled_in_daily=False)
    else:
        clock[0] += timedelta(days=1)
        due[0] = False
    controller.resume()
    eventually(lambda: not controller.status()["queue"])
    assert not factory.processes


def test_failed_continuation_clears_survey_and_preserves_battle_intent(controlled, monkeypatch):
    controller, factory, due, _ = controlled
    tactical_state.begin_battle(controller.config, game_day(NOW), "opponent", 5, 500, now=NOW)
    intent_before = tactical_state.state_path(controller.config).read_bytes()
    cleared = []

    def clear(config):
        cleared.append(config)
        due[0] = False

    monkeypatch.setattr(tactical_survey, "clear_survey", clear)
    due[0] = True
    enqueue_due(controller)
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish(1)
    eventually(lambda: controller.status()["state"] == "failed")
    with controller._condition:
        controller._enqueue_scheduled()
    assert len(cleared) == 1
    assert tactical_state.state_path(controller.config).read_bytes() == intent_before
    assert not controller.status()["queue"]
    assert len(factory.processes) == 1
    logs = list(controller.config.state_dir.rglob("*.log"))
    assert any("tactical_survey_continuation_cleared" in path.read_text() for path in logs)


def test_failed_survey_cleanup_holds_continuations_without_stopping_other_work(controlled, monkeypatch):
    controller, factory, due, _ = controlled

    def fail_clear(config):
        raise OSError("storage unavailable")

    monkeypatch.setattr(tactical_survey, "clear_survey", fail_clear)
    due[0] = True
    enqueue_due(controller)
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish(1)
    eventually(lambda: controller.status()["state"] == "failed")
    controller.enqueue("mail")
    eventually(lambda: len(factory.processes) == 2)
    assert factory.processes[1].arguments[-1] == "mail"
    assert not controller.status()["queue"]
    assert controller._tactical_continuation_error == "storage unavailable"


def test_cancelled_continuation_does_not_reappear(controlled, monkeypatch):
    controller, factory, due, _ = controlled
    monkeypatch.setattr(tactical_survey, "clear_survey", lambda config: due.__setitem__(0, False))
    due[0] = True
    enqueue_due(controller)
    identifier = controller.status()["queue"][0]["id"]
    controller.cancel(identifier)
    enqueue_due(controller)
    assert not controller.status()["queue"]
    assert not factory.processes


def test_manual_tactical_failure_does_not_clear_saved_survey(controlled, monkeypatch):
    controller, factory, _, _ = controlled
    cleared = []
    monkeypatch.setattr(tactical_survey, "clear_survey", cleared.append)
    controller.enqueue("tactical_battles")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish(1)
    eventually(lambda: controller.status()["state"] == "failed")
    assert not cleared


@pytest.mark.parametrize("elapsed, expected", [(59, False), (60, True), (4 * 3600, False)])
def test_real_checkpoint_due_time_and_absolute_expiry(controlled, monkeypatch, elapsed, expected):
    controller, _, _, clock = controlled
    monkeypatch.setattr(tactical_survey, "continuation_due", saved_continuation_due)
    started = NOW.replace(hour=9)
    save_survey(controller.config, started)
    clock[0] = started + timedelta(seconds=elapsed)
    enqueue_due(controller)
    assert bool(controller.status()["queue"]) is expected


def test_real_checkpoint_cannot_continue_after_daily_reset(controlled, monkeypatch):
    controller, factory, _, clock = controlled
    monkeypatch.setattr(tactical_survey, "continuation_due", saved_continuation_due)
    started = NOW.replace(hour=18, minute=59)
    save_survey(controller.config, started)
    clock[0] = started + timedelta(minutes=2)
    enqueue_due(controller)
    assert not controller.status()["queue"]
    assert not factory.processes


def test_real_failed_continuation_removes_only_survey_file(controlled, monkeypatch):
    controller, factory, _, _ = controlled
    monkeypatch.setattr(tactical_survey, "continuation_due", saved_continuation_due)
    save_survey(controller.config, NOW - timedelta(minutes=1))
    enqueue_due(controller)
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    # The child may fail after creating an unresolved entry. Disposable survey
    # cleanup cannot turn that into permission to use another ticket.
    tactical_state.begin_battle(controller.config, game_day(NOW), "opponent", 5, 500, now=NOW)
    intent_before = tactical_state.state_path(controller.config).read_bytes()
    factory.processes[0].finish(1)
    eventually(lambda: controller.status()["state"] == "failed")
    assert not tactical_survey.survey_path(controller.config).exists()
    assert tactical_state.state_path(controller.config).read_bytes() == intent_before
    assert not controller.status()["queue"]
