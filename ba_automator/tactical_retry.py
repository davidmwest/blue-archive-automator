"""Bounded retries for failed opponent surveys, separate from battle history.

Callers hold the instance lock for mutations. A scheduled retry consumes its
allowance immediately; cancellation or a daemon restart cannot restore it.
An unreadable budget fails closed instead of granting a fresh allowance.
"""

from datetime import date, datetime, timezone
import json
import math
import os
import stat
from uuid import uuid4

from .club import game_day
from . import tactical_state


MAX_DAILY_RETRIES = 3
RETRY_DELAY_SECONDS = 15 * 60
_KEYS = {"version", "day_key", "attempts", "due_at", "updated_at"}


def retry_path(config):
    path = tactical_state.state_path(config)
    return path.with_name(path.name.replace("tactical-", "tactical-retry-", 1))


def _epoch(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("A finite nonnegative timestamp is required")
    return value


def _day(now):
    return game_day(datetime.fromtimestamp(_epoch(now), timezone.utc))


def _read(config):
    path = retry_path(config)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > 4096:
        raise ValueError("Retry budget must be a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096
                or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("Retry budget changed while opening")
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("Retry budget exceeds its file size limit")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != _KEYS
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["attempts"]) is not int
            or not 1 <= value["attempts"] <= MAX_DAILY_RETRIES):
        raise ValueError("Invalid Tactical survey retry budget")
    if (not isinstance(value["day_key"], str)
            or date.fromisoformat(value["day_key"]).isoformat() != value["day_key"]
            or value["day_key"] != _day(value["updated_at"])):
        raise ValueError("Retry budget day disagrees with its timestamp")
    if value["due_at"] is not None:
        if _epoch(value["due_at"]) != value["updated_at"] + RETRY_DELAY_SECONDS:
            raise ValueError("Invalid Tactical survey retry delay")
    return value


def _write(config, value):
    path = retry_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _allowed(config, now):
    if not getattr(config, "tactical_battles_enabled_in_daily", False):
        return False
    current = tactical_state.read_state(config)
    tickets = current["last_tickets"]
    return (current["day_key"] == _day(now) and not current["pending"]
            and not current["blocked_reason"] and type(tickets) is int
            and tickets > config.tactical_battles_preserve_tickets)


def _eligible(config, now):
    if not _allowed(config, now):
        return None
    saved = _read(config)
    if (saved is None or saved["day_key"] != _day(now)
            or saved["due_at"] is None or saved["due_at"] > now
            or saved["updated_at"] > now):
        return None
    return saved


_READ_ERRORS = (RuntimeError, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError)


def schedule_retry(config, *, now):
    """Reserve a delayed search retry, at most three per game day."""
    try:
        if not _allowed(config, now):
            return False
        saved = _read(config)
        if saved is not None and saved["updated_at"] > now:
            return False
        if saved is None or saved["day_key"] != _day(now):
            attempts = 0
        else:
            if saved["due_at"] is not None:
                return True
            attempts = saved["attempts"]
        if attempts >= MAX_DAILY_RETRIES:
            return False
    except _READ_ERRORS:
        return False
    _write(config, {"version": 1, "day_key": _day(now), "attempts": attempts + 1,
                    "due_at": now + RETRY_DELAY_SECONDS, "updated_at": now})
    return True


def retry_due(config, *, now):
    try:
        return _eligible(config, now) is not None
    except _READ_ERRORS:
        return False


def claim_retry(config, *, now):
    """Consume a due dispatch before launching; never replay uncertain work."""
    try:
        saved = _eligible(config, now)
    except _READ_ERRORS:
        return False
    if saved is None:
        return False
    _write(config, dict(saved, due_at=None))
    return True


def clear_retry(config):
    """Cancel scheduled dispatch without restoring any consumed allowance."""
    try:
        saved = _read(config)
    except _READ_ERRORS:
        return
    if saved is not None and saved["due_at"] is not None:
        _write(config, dict(saved, due_at=None))
