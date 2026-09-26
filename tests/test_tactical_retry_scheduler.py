"""Retry dispatch preserves queue controls and durable allowance boundaries."""

from dataclasses import replace
from datetime import timedelta

import pytest

from ba_automator import tactical_retry, tactical_state
from ba_automator.club import game_day
from ba_automator.locking import InstanceLock, LockError
from test_server import eventually
from test_tactical_continuation_scheduler import controlled, enqueue_due


def schedule(controlled):
    controller, _, _, clock = controlled
    tactical_state.observe_ladder(controller.config, game_day(clock[0]),
                                  tickets=4, rank=571, now=clock[0])
    assert tactical_retry.schedule_retry(controller.config, now=clock[0].timestamp())
    clock[0] += timedelta(minutes=15)
    return controller


def test_due_retry_is_queued_once_and_consumed_before_child_start(controlled):
    controller = schedule(controlled)
    _, factory, _, clock = controlled
    enqueue_due(controller)
    enqueue_due(controller)
    assert [(job["task"], job["source"]) for job in controller.status()["queue"]] == [
        ("tactical_battles", "tactical_retry")]
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    assert not tactical_retry.retry_due(controller.config, now=clock[0].timestamp())
    assert factory.processes[0].arguments[-1] == "tactical_battles"
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
    assert factory.max_active == 1
    assert controller.status()["history"][0]["source"] == "tactical_retry"


@pytest.mark.parametrize("task", ["daily", "tactical_battles"])
@pytest.mark.parametrize("running", [False, True])
def test_existing_daily_or_tactical_work_suppresses_retry(controlled, task, running):
    controller = schedule(controlled)
    with controller._condition:
        if running:
            controller._current = {"id": "running", "task": task}
        else:
            controller.enqueue(task)
        enqueue_due(controller)
        assert not any(job.get("source") == "tactical_retry" for job in controller._queue)
        controller._current = None


@pytest.mark.parametrize("gate", ["paused", "capturing", "shutdown", "disabled"])
def test_retry_honors_dispatch_gates(controlled, gate):
    controller = schedule(controlled)
    _, factory, _, clock = controlled
    with controller._condition:
        controller._paused = gate == "paused"
        if gate == "disabled":
            controller.config = replace(controller.config, tactical_battles_enabled_in_daily=False)
        elif gate != "paused":
            setattr(controller, f"_{gate}", True)
        controller._enqueue_tactical_retry(clock[0].timestamp())
        assert not controller._queue
        assert not factory.processes
        if gate == "shutdown":
            controller._shutdown = False
        controller._paused = True


@pytest.mark.parametrize("change", ["disabled", "reserve", "pending", "reset"])
def test_retry_rechecks_guards_after_queueing(controlled, change):
    controller = schedule(controlled)
    _, factory, _, clock = controlled
    enqueue_due(controller)
    if change == "disabled":
        controller.config = replace(controller.config, tactical_battles_enabled_in_daily=False)
    elif change == "reserve":
        tactical_state.observe_ladder(controller.config, game_day(clock[0]),
                                      tickets=1, rank=571, now=clock[0])
    elif change == "pending":
        tactical_state.begin_battle(controller.config, game_day(clock[0]), "opponent-a",
                                    4, 571, now=clock[0])
    else:
        clock[0] += timedelta(days=1)
    controller.resume()
    eventually(lambda: not controller.status()["queue"])
    assert not factory.processes


def test_cancelling_retry_clears_due_without_refunding_allowance(controlled):
    controller = schedule(controlled)
    _, factory, _, clock = controlled
    enqueue_due(controller)
    controller.cancel(controller.status()["queue"][0]["id"])
    enqueue_due(controller)
    assert not controller.status()["queue"]
    assert not factory.processes
    for _ in range(2):
        assert tactical_retry.schedule_retry(controller.config, now=clock[0].timestamp())
        tactical_retry.clear_retry(controller.config)
    assert not tactical_retry.schedule_retry(controller.config, now=clock[0].timestamp())


def test_retry_claim_holds_instance_lock(controlled, monkeypatch):
    controller = schedule(controlled)
    _, factory, _, _ = controlled
    real_claim = tactical_retry.claim_retry
    checked = []

    def claim(config, *, now):
        with pytest.raises(LockError):
            with InstanceLock(config):
                pass
        checked.append(True)
        return real_claim(config, now=now)

    monkeypatch.setattr(tactical_retry, "claim_retry", claim)
    enqueue_due(controller)
    controller.resume()
    eventually(lambda: len(factory.processes) == 1)
    assert checked == [True]
    factory.processes[0].finish()
    eventually(lambda: controller.status()["state"] == "success")
