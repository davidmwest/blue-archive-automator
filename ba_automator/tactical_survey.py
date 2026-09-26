"""Disposable, bounded Tactical Challenge survey checkpoints.

These files contain observations, never ticket-spending permission. The battle
intent remains in tactical_state. A checkpoint expires four hours after its
original creation: an unchanged own rank is only a working assumption that the
row pools remain stable, not proof that opponents have not moved. Mutating
callers hold the instance lock; the scheduler may read atomic snapshots.
"""

from datetime import datetime, timezone
from functools import lru_cache
import json
import math
import os
import re
import stat
from pathlib import Path
from uuid import uuid4

from .club import game_day
from .tactical_battles import Opponent
from .tactical_refresh import RefreshPlanner
from . import tactical_state


MAX_AGE_SECONDS = 4 * 60 * 60
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ENTRIES = 1000
_KEYS = {"version", "day_key", "own_rank", "confidence", "pilot", "planner",
         "candidates", "identities", "lookup", "created_at", "updated_at", "due_at"}


def survey_path(config):
    path = tactical_state.state_path(config)
    return path.with_name(path.name.replace("tactical-", "tactical-survey-", 1))


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("A finite nonnegative epoch timestamp is required")
    return value


def _day(now):
    return game_day(datetime.fromtimestamp(_number(now), timezone.utc))


def _opponent(value):
    if not isinstance(value, dict) or set(value) != {"opponent_id", "rank", "level", "visible_levels"}:
        raise ValueError("Invalid saved opponent")
    opponent = Opponent(**value)
    if len(opponent.opponent_id) > 256:
        raise ValueError("Opponent identity is too long")
    return opponent


def _identities(value):
    if not isinstance(value, dict) or len(value) > MAX_ENTRIES:
        raise ValueError("Too many saved opponent identities")
    for key, item in value.items():
        if not isinstance(item, dict) or set(item) != {"choice", "name", "target", "signature"}:
            raise ValueError("Invalid saved opponent identity")
        choice = _opponent(item["choice"])
        if key != choice.opponent_id:
            raise ValueError("Saved opponent identity key does not match its choice")
        if not isinstance(item["name"], str) or not item["name"].strip() or len(item["name"]) > 256:
            raise ValueError("Invalid saved opponent name")
        target = item["target"]
        if (not isinstance(target, (list, tuple)) or len(target) != 2
                or any(type(v) is not int for v in target)
                or not 0 <= target[0] < 1280 or not 0 <= target[1] < 720):
            raise ValueError("Invalid saved opponent screen position")
        signature = item["signature"]
        if not isinstance(signature, str) or re.fullmatch(r"[0-9a-fA-F]{1152}", signature) is None:
            raise ValueError("Invalid saved opponent portrait signature")
    return value


def _validate(value, now, *, fresh=True):
    if (not isinstance(value, dict) or set(value) != _KEYS
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("Invalid saved Tactical Challenge survey")
    if type(value["own_rank"]) is not int or value["own_rank"] < 1:
        raise ValueError("Invalid saved player rank")
    created, updated, due = (_number(value[key]) for key in ("created_at", "updated_at", "due_at"))
    if not created <= updated <= now or not updated <= due <= updated + MAX_AGE_SECONDS:
        raise ValueError("Invalid saved survey times")
    if value["day_key"] != _day(created):
        raise ValueError("Saved survey day does not match its observations")
    if fresh and (now - created >= MAX_AGE_SECONDS or value["day_key"] != _day(now)):
        raise ValueError("Saved survey expired or crossed daily reset")
    planner = RefreshPlanner.from_dict(value["planner"])
    if (type(value["pilot"]) is not int or not 2 <= value["pilot"] <= 100
            or value["pilot"] != planner.pilot
            or type(value["confidence"]) not in (int, float)
            or value["confidence"] != planner.confidence):
        raise ValueError("Saved survey settings disagree with its observations")
    identities = _identities(value["identities"])
    candidates = value["candidates"]
    if not isinstance(candidates, list) or len(candidates) > MAX_ENTRIES:
        raise ValueError("Too many saved candidates")
    parsed = [_opponent(candidate) for candidate in candidates]
    if len({p.opponent_id for p in parsed}) != len(parsed):
        raise ValueError("Saved survey has duplicate candidates")
    if any(p.opponent_id not in identities or p.rank >= value["own_rank"] for p in parsed):
        raise ValueError("Saved candidate lacks an identity or higher rank")
    lookup = value["lookup"]
    if lookup is not None:
        if (not isinstance(lookup, dict) or set(lookup) != {"opponent_id", "valid_draws"}
                or type(lookup["valid_draws"]) is not int or lookup["valid_draws"] < 0):
            raise ValueError("Invalid saved opponent lookup")
        candidate = next((p for p in parsed if p.opponent_id == lookup["opponent_id"]), None)
        if candidate is None:
            raise ValueError("Saved lookup target is missing")
        plan = planner.lookup_plan(candidate.rank)
        if not plan.within_limit or lookup["valid_draws"] > plan.required_refreshes:
            raise ValueError("Saved lookup exceeds its confidence budget")
    return dict(value, planner=planner)


def _read_json(path):
    """Bound reads and reject links/nonregular files on macOS and Windows."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
        raise ValueError("Saved survey must be a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("Saved survey changed while opening")
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Saved survey exceeds its file size limit")
    return json.loads(raw)


def _read(config, now, *, fresh=True):
    try:
        return _validate(_read_json(survey_path(config)), now, fresh=fresh)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return None


@lru_cache(maxsize=16)
def _continuation_metadata(path, fingerprint):
    """Cache only validated scheduling metadata, never a mutable planner."""
    try:
        raw = _read_json(Path(path))
        value = _validate(raw, _number(raw["updated_at"]), fresh=False)
        return {key: value[key] for key in (
            "day_key", "confidence", "pilot", "created_at", "updated_at", "due_at"
        )} | {"status": value["planner"].plan().status}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return None


def _matches(value, day_key, own_rank, confidence, pilot):
    return (value is not None and value["day_key"] == day_key
            and value["own_rank"] == own_rank and value["confidence"] == confidence
            and value["pilot"] == pilot)


def save_survey(config, *, day_key, own_rank, confidence, pilot, planner,
                candidates, identities, lookup=None, now, delay_seconds=60):
    """Atomically save a continuation, retaining its original four-hour age.

    Clear an abandoned checkpoint before deliberately starting a new survey.
    The delay is scheduling metadata and does not contribute statistical draws.
    """
    _number(now)
    _number(delay_seconds)
    if day_key != _day(now) or delay_seconds > MAX_AGE_SECONDS:
        raise ValueError("A survey must be saved for the current game day with a bounded delay")
    if not isinstance(planner, RefreshPlanner):
        raise ValueError("A refresh planner is required")
    previous = _read(config, now, fresh=False)
    created = (previous["created_at"] if _matches(previous, day_key, own_rank, confidence, pilot)
               else now)
    value = {"version": 1, "day_key": day_key, "own_rank": own_rank,
             "confidence": confidence, "pilot": pilot, "planner": planner.to_dict(),
             "candidates": candidates, "identities": identities, "lookup": lookup,
             "created_at": created, "updated_at": now, "due_at": now + delay_seconds}
    # A JSON roundtrip copies mutable caller data and catches NaN/non-JSON data.
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError("Saved survey exceeds its file size limit")
    value = json.loads(encoded)
    _validate(value, now, fresh=False)
    path = survey_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_survey(config, *, day_key, own_rank, confidence, pilot, now):
    """Return a replayed matching checkpoint, or None for unusable evidence."""
    _number(now)
    value = _read(config, now)
    return value if _matches(value, day_key, own_rank, confidence, pilot) else None


def clear_survey(config):
    """Remove only disposable survey progress, never battle history or intent."""
    survey_path(config).unlink(missing_ok=True)


def continuation_due(config, *, now):
    """Read-only scheduler check; no continuation while spending is unresolved."""
    _number(now)
    path = survey_path(config)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            return False
        fingerprint = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
        value = _continuation_metadata(str(path), fingerprint)
    except OSError:
        return False
    if (value is None or value["due_at"] > now or value["updated_at"] > now
            or now - value["created_at"] >= MAX_AGE_SECONDS
            or value["day_key"] != _day(now) or value["status"] == "deferred"
            or value["confidence"] != round(config.tactical_battles_confidence_percent / 100, 12)
            or value["pilot"] != config.tactical_battles_refresh_limit):
        return False
    try:
        history = tactical_state.read_state(config)
    except tactical_state.TacticalStateError:
        return False
    if history["pending"] or history["blocked_reason"]:
        return False
    tickets = history["last_tickets"]
    return tickets is None or tickets > config.tactical_battles_preserve_tickets
