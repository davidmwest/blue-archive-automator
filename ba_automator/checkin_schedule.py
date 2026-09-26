"""Durable pacing for optional home-screen check-ins, independent of Daily.

The next deadline is written before dispatch. Missed intervals collapse into
one visit; a failed or interrupted check never creates an immediate retry loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


class CheckinScheduleError(RuntimeError):
    """Only the periodic check-in timer must stop until its state is repaired."""


def parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Expected a timestamp string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Check-in timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def initial_state(now: datetime, interval_minutes: int) -> dict:
    now = parse_time(now.isoformat())
    return {"version": 1, "next_due_at": (now + timedelta(minutes=interval_minutes)).isoformat(),
            "status": None, "run_id": None, "last_started_at": None,
            "last_completed_at": None, "last_success_at": None}


def state_path(config) -> Path:
    identity = hashlib.sha256(f"{config.serial}\0{config.package}".encode()).hexdigest()[:24]
    return config.state_dir / f"checkin-{identity}.json"


def validate(state: dict) -> dict:
    try:
        if (not isinstance(state, dict) or type(state.get("version")) is not int
                or state["version"] != 1):
            raise ValueError("Unsupported check-in state")
        parse_time(state["next_due_at"])
        if state["status"] not in {None, "running", "success", "failed", "stopped", "skipped"}:
            raise ValueError("Invalid check-in status")
        for key in ("last_started_at", "last_completed_at", "last_success_at"):
            if state[key] is not None:
                parse_time(state[key])
        if state["run_id"] is not None and (not isinstance(state["run_id"], str)
                or not state["run_id"] or any(ord(c) < 32 for c in state["run_id"])):
            raise ValueError("Invalid check-in run identity")
        if state["status"] == "running" and (state["last_started_at"] is None or state["run_id"] is None):
            raise ValueError("A running check-in needs a recorded start")
        if state["status"] in {"success", "failed", "stopped", "skipped"} and state["last_completed_at"] is None:
            raise ValueError("A finished check-in needs a recorded completion")
        if state["status"] == "success" and state["last_success_at"] is None:
            raise ValueError("A successful check-in needs a verified scan time")
        return state
    except (TypeError, ValueError, KeyError) as exc:
        raise CheckinScheduleError("Check-in schedule is invalid; save its settings to reset the timer") from exc


def read_state(config) -> dict | None:
    try:
        value = json.loads(state_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CheckinScheduleError("Check-in schedule could not be read; save its settings to reset the timer") from exc
    return validate(value)


def write_state(config, state: dict) -> None:
    validate(state)
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
        raise CheckinScheduleError("Check-in schedule could not be saved; its timer is held until settings are saved") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
