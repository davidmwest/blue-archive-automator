"""Bounded Daily continuation without retrying failed spending operations."""

from __future__ import annotations

from .adb import DeviceError
from .assault_state import AssaultStateError
from .config import ConfigError
from .locking import LockError
from .runtime import TaskError


# These older state readers predate typed task errors. Recognize their precise
# refusal messages, rather than treating every RuntimeError as safe to ignore.
_STATE_REFUSALS = {
    "Cannot read ticket state; inspect it before spending",
    "Invalid ticket state; spending is blocked",
    "Cannot read AP state; inspect it before spending",
    "Invalid AP state; spending is blocked",
    "Cannot read paid-pack state; inspect it before allowing purchases",
    "Invalid paid-pack state; purchases are blocked",
    "Invalid pending purchase; purchases are blocked",
    "Invalid paid-pack schedule; purchases are blocked",
    "Invalid Club attendance state; inspect the local state file",
}


def _recoverable(task, error):
    if task == "restart":
        return False
    # Cafe and startup wrap lower-level failures with TaskError. A broken
    # device, lock, configuration, or filesystem must retain its fatal meaning.
    cause, seen = error, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, (DeviceError, ConfigError, LockError, OSError)):
            return False
        cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
    return isinstance(error, (TaskError, AssaultStateError)) or (
        type(error) is RuntimeError and str(error) in _STATE_REFUSALS
    )


def run_daily_plan(plan, run_task, emit):
    """Run each configured step once; navigate safely around isolated failures.

    ``run_task`` uses the ordinary locked task entry points and publishes their
    normal results. ``emit`` records structured failure, recovery, and summary
    events. Failed spending tasks are never repeated and their durable holds
    are never changed here. Before another independent step, the existing
    bounded restart must establish a verified home screen.

    Unexpected and infrastructure errors abort immediately. A failed recovery
    also aborts, preserving already completed work in the terminal summary.
    """
    plan = tuple(plan)
    completed, failed, deferred = [], [], []

    def summary(status, *, skipped=(), aborted=False):
        value = {
            "type": "command_summary", "command": "daily", "status": status,
            "completed_tasks": completed.copy(), "failed_tasks": failed.copy(),
            "deferred_tasks": deferred.copy(), "skipped_tasks": list(skipped),
            "aborted": aborted,
        }
        emit(value)
        return value

    def failure(task, error, *, recovery=False):
        run_dir = getattr(error, "run_dir", None)
        value = {
            "task": task, "error": str(error) or type(error).__name__,
            "error_type": type(error).__name__,
            "run_dir": str(run_dir) if run_dir is not None else None,
            "recoverable": not recovery and _recoverable(task, error),
            "recovery": recovery,
        }
        failed.append(value)
        emit({"type": "task_failure", "command": "daily", **value})
        return value

    def recover(task, remaining):
        if not remaining:
            return None
        emit({"type": "daily_recovery_started", "command": "daily",
              "after_task": task, "next_task": remaining[0]})
        try:
            recovery = run_task("restart")
            if recovery.status in {"stopped", "interrupted"}:
                return summary("stopped", skipped=remaining, aborted=True)
            if recovery.status != "success":
                raise TaskError("Daily recovery did not verify the home screen", recovery.run_dir)
        except KeyboardInterrupt:
            summary("stopped", skipped=remaining, aborted=True)
            raise
        except Exception as recovery_error:
            failure("restart", recovery_error, recovery=True)
            summary("failed", skipped=remaining, aborted=True)
            raise
        emit({"type": "daily_recovery_finished", "command": "daily",
              "after_task": task, "next_task": remaining[0],
              "run_dir": str(recovery.run_dir), "status": "success"})
        return None

    for index, task in enumerate(plan):
        try:
            result = run_task(task)
            if result.status in {"stopped", "interrupted"}:
                deferred.append({"task": task, "status": result.status})
                return summary("stopped", skipped=plan[index + 1:], aborted=True)
            if task == "restart" and result.status != "success":
                raise TaskError("Daily startup did not verify the home screen", result.run_dir)
            if result.status not in {"success", "disabled", "deferred"}:
                raise TaskError(f"{task} returned status {result.status}", result.run_dir)
        except KeyboardInterrupt:
            summary("stopped", skipped=plan[index + 1:], aborted=True)
            raise
        except Exception as error:
            record = failure(task, error)
            if not record["recoverable"]:
                summary("failed", skipped=plan[index + 1:], aborted=True)
                raise
            stopped = recover(task, plan[index + 1:])
            if stopped is not None:
                return stopped
            continue
        if result.status == "success":
            completed.append(task)
        else:
            deferred.append({"task": task, "status": result.status})
            # Disabled/deferred runners may stop on their setup screen. They
            # are not failures, but still must re-establish home before work.
            stopped = recover(task, plan[index + 1:])
            if stopped is not None:
                return stopped
    return summary("partial_failure" if failed else "success")
