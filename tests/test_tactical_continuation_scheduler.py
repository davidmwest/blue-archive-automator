"""Tactical survey chunks yield the serial queue without replaying Daily."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from ba_automator import daily_schedule, tactical_state, tactical_survey
from ba_automator.club import game_day
from ba_automator.server import DashboardController
from ba_automator.tactical_search import SearchPolicy, SearchState
from ba_automator.tactical_survey import continuation_due as saved_continuation_due
from test_server import FakeOutput, ProcessFactory, eventually


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
    search = SearchState(SearchPolicy(config.tactical_battles_search_minutes * 60))
    search.advance(120)
    tactical_survey.save_survey(
        config, day_key=game_day(now), own_rank=500, search=search,
        identities={}, now=now.timestamp())


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
    # The real worker also polls schedules while paused. Keep this unguarded
    # observation probe inside one locked pass, restoring it before that worker
    # can wake and record a second (otherwise harmless) observation.
    with controller._condition, monkeypatch.context() as scoped:
        due[0] = True
        controller.enqueue("crafting")
        controller.config = replace(controller.config, cafe_schedule_enabled=True, ap_schedule_enabled=True)
        checkin_seen = []
        scoped.setattr(controller, "_enqueue_checkin",
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


def test_failed_continuation_blocks_survey_and_preserves_battle_intent(controlled, monkeypatch):
    controller, factory, due, _ = controlled
    tactical_state.begin_battle(controller.config, game_day(NOW), "opponent", 5, 500, now=NOW)
    intent_before = tactical_state.state_path(controller.config).read_bytes()
    blocked = []

    def block(config, *, now):
        blocked.append((config, now))
        due[0] = False

    monkeypatch.setattr(tactical_survey, "block_survey", block)
    due[0] = True
    enqueue_due(controller)
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish(1)
    eventually(lambda: controller.status()["state"] == "failed")
    with controller._condition:
        controller._enqueue_scheduled()
    assert len(blocked) == 1
    assert blocked[0][1] == NOW.timestamp()
    assert tactical_state.state_path(controller.config).read_bytes() == intent_before
    assert not controller.status()["queue"]
    assert len(factory.processes) == 1
    logs = list(controller.config.state_dir.rglob("*.log"))
    assert any("tactical_survey_continuation_blocked" in path.read_text() for path in logs)


def test_failed_survey_block_holds_continuations_without_stopping_other_work(controlled, monkeypatch):
    controller, factory, due, _ = controlled

    def fail_block(config, *, now):
        raise OSError("storage unavailable")

    monkeypatch.setattr(tactical_survey, "block_survey", fail_block)
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
    monkeypatch.setattr(tactical_survey, "block_survey", lambda config, *, now: due.__setitem__(0, False))
    due[0] = True
    enqueue_due(controller)
    identifier = controller.status()["queue"][0]["id"]
    controller.cancel(identifier)
    enqueue_due(controller)
    assert not controller.status()["queue"]
    assert not factory.processes


def test_manual_tactical_failure_does_not_change_survey_in_scheduler(controlled, monkeypatch):
    controller, factory, _, _ = controlled
    blocked = []
    monkeypatch.setattr(tactical_survey, "block_survey", lambda config, *, now: blocked.append(config))
    controller.enqueue("tactical_battles")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish(1)
    eventually(lambda: controller.status()["state"] == "failed")
    assert not blocked


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


def test_real_failed_continuation_blocks_search_and_preserves_budget(controlled, monkeypatch):
    controller, factory, _, _ = controlled
    monkeypatch.setattr(tactical_survey, "continuation_due", saved_continuation_due)
    save_survey(controller.config, NOW - timedelta(minutes=1))
    enqueue_due(controller)
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    # A failed child may leave an unresolved entry. Blocking its search must
    # preserve both the consumed allowance and the durable spending intent.
    tactical_state.begin_battle(controller.config, game_day(NOW), "opponent", 5, 500, now=NOW)
    intent_before = tactical_state.state_path(controller.config).read_bytes()
    factory.processes[0].finish(1)
    eventually(lambda: controller.status()["state"] == "failed")
    saved = tactical_survey.load_survey(
        controller.config, day_key=game_day(NOW), own_rank=500,
        time_budget_seconds=controller.config.tactical_battles_search_minutes * 60,
        now=NOW.timestamp())
    assert saved["status"] == "blocked"
    assert saved["search"].elapsed_seconds == 120
    assert not saved_continuation_due(controller.config, now=NOW.timestamp() + 900)
    assert tactical_state.state_path(controller.config).read_bytes() == intent_before
    assert not controller.status()["queue"]


@pytest.mark.parametrize("outcome, tickets, timeouts, remaining", [
    ("success", 4, 3, 0),  # All search allowances expired; the actual tickets remain.
    ("success", 1, 0, 0),  # The manual-play reserve is still available.
    ("deferred", 4, 1, 2),  # A saved search will continue after other queued work.
])
def test_tactical_completion_phase_reports_tickets_and_search_allowances(
        controlled, monkeypatch, outcome, tickets, timeouts, remaining):
    controller, factory, _, _ = controlled
    tactical_state.observe_ladder(controller.config, game_day(NOW),
                                  tickets=tickets, rank=500, preserve=1, now=NOW)
    for index in range(timeouts):
        tactical_state.record_search_timeout(controller.config, game_day(NOW),
                                            f"search-{index}", now=NOW)
    saved = tactical_state.state_path(controller.config).read_bytes()

    def output(self):
        self.process.done.wait()
        # This is the real command's order: successful reward collection must
        # not hide the battle runner's saved continuation.
        for task, status in (("tactical_battles", outcome), ("tactical_rewards", "success")):
            run = self.process.run_dir.parent / f"{task}-test"
            run.mkdir()
            yield json.dumps({"status": status, "run_dir": str(run),
                              "duration": 1.2, "actions": 2}) + "\n"

    monkeypatch.setattr(FakeOutput, "__iter__", output)
    controller.enqueue("tactical_battles")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    result = controller.status()
    phase = result["phase"]
    assert phase.startswith("Tactical Challenge search saved; queue continues"
                            if outcome == "deferred" else "Tactical Challenge visit finished")
    assert f"{tickets} ticket{'s' if tickets != 1 else ''} left, reserve 1" in phase
    assert f"{timeouts} search timeout" in phase
    assert f"{remaining} search allowances left today" in phase
    assert "battles complete" not in phase
    assert result["result"]["status"] == "success"  # Existing API semantics remain intact.
    assert tactical_state.state_path(controller.config).read_bytes() == saved


@pytest.mark.parametrize("state_kind", ["missing", "corrupt", "previous_day"])
def test_unavailable_tactical_counts_do_not_fail_a_successful_job(controlled, state_kind):
    controller, factory, _, _ = controlled
    path = tactical_state.state_path(controller.config)
    if state_kind == "corrupt":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{corrupt", encoding="utf-8")
    elif state_kind == "previous_day":
        yesterday = NOW - timedelta(days=1)
        tactical_state.observe_ladder(controller.config, game_day(yesterday),
                                      tickets=5, rank=500, now=yesterday)
    saved = path.read_bytes() if path.exists() else None
    controller.enqueue("tactical_battles")
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    assert controller.status()["phase"] == "Tactical Challenge visit finished; ticket counts unavailable"
    assert (path.read_bytes() if path.exists() else None) == saved


@pytest.mark.parametrize("task", ["mail", "daily"])
def test_other_job_completion_labels_do_not_use_tactical_summary(controlled, monkeypatch, task):
    from ba_automator.tasks import TASK_LABELS

    controller, factory, _, _ = controlled
    summaries = []

    def unexpected_summary(config, status):
        summaries.append((config, status))
        return "Unexpected Tactical Challenge summary"

    monkeypatch.setattr(controller, "_tactical_completion_phase", unexpected_summary)
    controller.enqueue(task)
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    assert controller.status()["phase"] == TASK_LABELS[task]
    assert not summaries
