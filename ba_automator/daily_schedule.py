"""One durable Daily occurrence per Global server day, scoped to an instance.

The dashboard claims an occurrence before launching its child process. Every
recorded outcome, including an interrupted attempt, prevents automatic replay
for that day. A manual Daily run can replace the record after user intervention.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


RESET_HOUR_UTC = 19
STATUSES = frozenset({"running", "success", "failed", "stopped", "skipped"})


class DailyScheduleError(RuntimeError):
    """Daily state cannot be trusted or safely persisted."""


def empty_state() -> dict:
    return {
        "version": 1,
        "game_day": None,
        "status": None,
        "run_id": None,
        "started_at": None,
        "completed_at": None,
    }


def state_path(config) -> Path:
    """Config canonicalizes endpoint aliases before this identity is computed."""
    identity = hashlib.sha256(f"{config.serial}\0{config.package}".encode()).hexdigest()[:24]
    return config.state_dir / f"daily-{identity}.json"


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Expected a timestamp string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Daily timestamps must include their timezone")
    return parsed.astimezone(timezone.utc)


def _validate(state: dict) -> dict:
    try:
        if (not isinstance(state, dict) or type(state.get("version")) is not int
                or state["version"] != 1 or not empty_state().keys() <= state.keys()):
            raise ValueError("Unsupported or incomplete daily state")
        if state["game_day"] is None:
            if any(state[key] is not None for key in ("status", "run_id", "started_at", "completed_at")):
                raise ValueError("An empty occurrence cannot have an outcome")
            return state
        if not isinstance(state["game_day"], str) or date.fromisoformat(state["game_day"]).isoformat() != state["game_day"]:
            raise ValueError("Expected an ISO game day")
        if state["status"] not in STATUSES:
            raise ValueError("Unsupported daily status")
        if (not isinstance(state["run_id"], str) or not state["run_id"].strip()
                or any(character in state["run_id"] for character in "\x00\r\n")):
            raise ValueError("Expected a run identity")
        started = _timestamp(state["started_at"]) if state["started_at"] is not None else None
        completed = _timestamp(state["completed_at"]) if state["completed_at"] is not None else None
        if state["status"] == "running":
            if started is None or completed is not None:
                raise ValueError("A running occurrence must have only a start time")
        elif completed is None or (started is None and state["status"] != "skipped"):
            raise ValueError("A terminal occurrence must have a completion time")
        if started is not None and completed is not None and completed < started:
            raise ValueError("Completion cannot precede the start")
        return state
    except (TypeError, ValueError, KeyError) as exc:
        raise DailyScheduleError("Daily schedule state is invalid; automatic Daily runs are paused") from exc


def read_state(config) -> dict:
    try:
        value = json.loads(state_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        raise DailyScheduleError("Daily schedule state could not be read; automatic Daily runs are paused") from exc
    return _validate(value)


def write_state(config, state: dict) -> None:
    """Persist a validated record atomically; errors must prevent dispatch.

    The caller serializes scheduling decisions. Replacement keeps concurrent
    dashboard readers from observing a partial claim or completion record.
    """
    _validate(state)
    path = state_path(config)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        raise DailyScheduleError("Daily schedule state could not be saved; the occurrence was not safely recorded") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # A cleanup failure must not hide the original persistence error.
            pass


def _utc_now(now: datetime, delay_minutes: int) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Daily scheduling requires a timezone-aware current time")
    if type(delay_minutes) is not int or not 0 <= delay_minutes <= 120:
        raise ValueError("Daily reset delay must be an integer from 0 to 120 minutes")
    return now.astimezone(timezone.utc)


def due_day(now: datetime, delay_minutes: int = 1) -> str | None:
    """Current eligible game day, without replaying days missed while offline.

    Immediately after reset, the old day is already over but the new day's
    grace period may still be active. Return None then, so fresh tickets cannot
    be consumed under a previous day's catch-up occurrence.
    """
    now = _utc_now(now, delay_minutes)
    reset = now.replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now < reset:
        reset -= timedelta(days=1)
    if now < reset + timedelta(minutes=delay_minutes):
        return None
    return reset.date().isoformat()


def next_due(now: datetime, delay_minutes: int = 1) -> datetime:
    """The next reset plus delay strictly after now, always expressed in UTC."""
    now = _utc_now(now, delay_minutes)
    scheduled = now.replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)
    scheduled += timedelta(minutes=delay_minutes)
    return scheduled if now < scheduled else scheduled + timedelta(days=1)


def is_due(state: dict, now: datetime, delay_minutes: int = 1) -> bool:
    """An attempt or terminal outcome consumes that day's automatic occurrence."""
    _validate(state)
    day = due_day(now, delay_minutes)
    return day is not None and (state["game_day"] is None or state["game_day"] < day)
