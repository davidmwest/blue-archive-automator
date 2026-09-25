"""Durable Total Assault intents and single-use, exact-team mock qualification.

Every mutation requires the caller's instance lock. Persist an intent before
sending the ticket-consuming tap. A crash or ambiguous receipt leaves that intent
in place and blocks repeats, including after the game day or event changes.
"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from uuid import uuid4

from .assault_policy import AssaultContext, comfortable_win, team_fingerprint


PROOF_LIFETIME_SECONDS = 15 * 60


class AssaultStateError(RuntimeError):
    pass


def state_path(config):
    identity = hashlib.sha256(f"{config.serial}\0{config.package}".encode()).hexdigest()[:24]
    return config.state_dir / f"assault-{identity}.json"


def empty_state():
    return {"version": 1, "proof": None, "pending": None, "clear": None,
            "blocked_reason": None, "last_summary": None, "updated_at": None}


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("A timestamp must include its timezone")
    return parsed.astimezone(timezone.utc)


def _now(value):
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("The current time must include its timezone")
    return value.astimezone(timezone.utc)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonempty identity")


def _count(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Ticket counts must be known nonnegative integers")


def _context(value):
    if not isinstance(value, AssaultContext):
        raise ValueError("An observed assault context is required")
    return asdict(value)


def _validate(state):
    if (not isinstance(state, dict) or set(state) != set(empty_state())
            or type(state["version"]) is not int or state["version"] != 1):
        raise ValueError("Unknown Total Assault state format")
    for key in ("blocked_reason", "last_summary"):
        if state[key] is not None:
            _text(state[key])
    if state["updated_at"] is not None:
        _timestamp(state["updated_at"])
    for key in ("proof", "pending", "clear"):
        record = state[key]
        if record is None:
            continue
        if not isinstance(record, dict):
            raise ValueError("Invalid Total Assault record")
        AssaultContext(**record["context"])
        _text(record["run_id"])
        _timestamp(record["created_at"])
        if key in ("proof", "clear"):
            if not isinstance(record["team"], str) or not re.fullmatch("[0-9a-f]{64}", record["team"]):
                raise ValueError("Invalid team fingerprint")
        if key == "proof":
            _text(record["id"])
            if (_timestamp(record["expires_at"]) - _timestamp(record["created_at"])
                    != timedelta(seconds=PROOF_LIFETIME_SECONDS)):
                raise ValueError("Invalid proof lifetime")
            _count(record["min_remaining_seconds"])
        elif key == "pending":
            _text(record["id"])
            if record["kind"] not in ("entry", "sweep"):
                raise ValueError("Unknown ticket-consuming action")
            _count(record["tickets_before"], 1)
            _count(record["count"], 1)
            if record["count"] > record["tickets_before"] or (record["kind"] == "entry" and record["count"] != 1):
                raise ValueError("Invalid ticket budget")
            if not isinstance(record["team"], str) or not re.fullmatch("[0-9a-f]{64}", record["team"]):
                raise ValueError("Invalid team fingerprint")
        else:
            _count(record["tickets_after"])
    if state["pending"] is not None and state["proof"] is not None:
        raise ValueError("A spending intent must consume its mock proof")
    pending = state["pending"]
    if pending is not None and pending["kind"] == "sweep":
        clear = state["clear"]
        if (clear is None or any(clear[key] != pending[key] for key in ("context", "team", "run_id"))
                or clear["tickets_after"] != pending["tickets_before"]):
            raise ValueError("A sweep intent must match the verified real clear")
    return state


def read_state(config):
    try:
        value = json.loads(state_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        raise AssaultStateError("Cannot read Total Assault state; ticket spending is blocked") from exc
    try:
        return _validate(value)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AssaultStateError("Invalid Total Assault state; ticket spending is blocked") from exc


def write_state(config, state):
    _validate(state)
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _unblocked(state):
    if state["pending"] is not None:
        raise AssaultStateError("A previous Total Assault ticket action is unresolved; inspect it before retrying")
    if state["blocked_reason"]:
        raise AssaultStateError(state["blocked_reason"])


def _save(config, state, now):
    state["updated_at"] = now.isoformat()
    write_state(config, state)
    return state


def record_mock(config, context, team, result, run_id, *, min_remaining_seconds=30, now=None):
    """Replace the previous proof; a failed or incomplete mock clears it."""
    now = _now(now)
    _text(run_id)
    context_value = _context(context)
    fingerprint = team_fingerprint(team)
    state = read_state(config)
    _unblocked(state)
    state["proof"] = None
    if comfortable_win(team, result, min_remaining_seconds):
        state["proof"] = {"id": uuid4().hex, "context": context_value, "team": fingerprint,
                          "run_id": run_id, "created_at": now.isoformat(),
                          "expires_at": (now + timedelta(seconds=PROOF_LIFETIME_SECONDS)).isoformat(),
                          "min_remaining_seconds": min_remaining_seconds}
        state["last_summary"] = "Mock battle qualified this exact team for one real entry"
    else:
        state["last_summary"] = "Mock battle did not meet the comfort requirement"
    return _save(config, state, now)


def begin_entry(config, context, team, run_id, tickets_before, *, now=None):
    """Return the persisted intent ID; only then may the runner tap real entry."""
    now = _now(now)
    _count(tickets_before, 1)
    context_value, fingerprint = _context(context), team_fingerprint(team)
    state = read_state(config)
    _unblocked(state)
    proof = state["proof"]
    if not proof or proof["context"] != context_value or proof["team"] != fingerprint or proof["run_id"] != run_id:
        raise AssaultStateError("This exact team and difficulty need a fresh successful mock in this run")
    if not _timestamp(proof["created_at"]) <= now < _timestamp(proof["expires_at"]):
        raise AssaultStateError("The mock qualification has expired; repeat the mock before real entry")
    pending = {"id": uuid4().hex, "kind": "entry", "context": context_value,
               "team": fingerprint, "run_id": run_id, "tickets_before": tickets_before,
               "count": 1, "created_at": now.isoformat()}
    state.update(proof=None, pending=pending, clear=None)
    _save(config, state, now)
    return pending["id"]


def _pending(config, intent_id, kind):
    state = read_state(config)
    pending = state["pending"]
    if not pending or pending["id"] != intent_id or pending["kind"] != kind:
        raise AssaultStateError("No matching Total Assault intent; do not replay a ticket action")
    return state, pending


def _uncertain(config, state, reason, now):
    state["blocked_reason"] = reason
    _save(config, state, now)
    raise AssaultStateError(reason)


def complete_entry(config, intent_id, *, tickets_after, won, now=None):
    """Only a verified victory and one-ticket decrement unlock this run's sweep."""
    now = _now(now)
    state, pending = _pending(config, intent_id, "entry")
    if type(tickets_after) is not int or tickets_after != pending["tickets_before"] - 1 or type(won) is not bool:
        _uncertain(config, state, "Total Assault result or ticket decrement is uncertain; inspect before retrying", now)
    state["pending"] = None
    state["blocked_reason"] = None
    if won:
        state["clear"] = {key: pending[key] for key in ("context", "team", "run_id")}
        state["clear"].update(created_at=now.isoformat(), tickets_after=tickets_after)
        state["last_summary"] = "Real Total Assault victory and ticket receipt verified"
    else:
        state["clear"] = None
        # A reconciled defeat is not an uncertain ticket action. End this job,
        # but let an explicit later visit qualify a fresh mock at a lower tier.
        state["last_summary"] = "The real Total Assault battle failed; use a lower difficulty or play manually"
    return _save(config, state, now)


def begin_sweep(config, context, run_id, tickets_before, count, *, now=None):
    """Require this run's real clear plus an unchanged observed ticket balance."""
    now = _now(now)
    _count(tickets_before, 1)
    _count(count, 1)
    if count > tickets_before:
        raise ValueError("A sweep cannot spend more tickets than observed")
    state = read_state(config)
    _unblocked(state)
    clear = state["clear"]
    if (not clear or clear["context"] != _context(context) or clear["run_id"] != run_id
            or clear["tickets_after"] != tickets_before or _timestamp(clear["created_at"]) > now):
        raise AssaultStateError("Sweeping requires this run's verified real clear and ticket balance")
    pending = {"id": uuid4().hex, "kind": "sweep", "context": clear["context"],
               "team": clear["team"], "run_id": run_id, "tickets_before": tickets_before,
               "count": count, "created_at": now.isoformat()}
    state.update(proof=None, pending=pending)
    _save(config, state, now)
    return pending["id"]


def complete_sweep(config, intent_id, *, tickets_after, rewards_verified, now=None):
    now = _now(now)
    state, pending = _pending(config, intent_id, "sweep")
    if type(tickets_after) is not int or tickets_after != pending["tickets_before"] - pending["count"] or rewards_verified is not True:
        _uncertain(config, state, "Total Assault sweep rewards or ticket decrement are uncertain; inspect before retrying", now)
    state["pending"] = None
    # Exact receipt reconciliation resolves the same hold as a verified real
    # entry. Retaining its old uncertainty message would block every later day
    # despite having no unresolved ticket action left to inspect.
    state["blocked_reason"] = None
    state["clear"]["tickets_after"] = tickets_after
    state["last_summary"] = f"Verified {pending['count']} Total Assault sweep(s); {tickets_after} ticket(s) remain"
    return _save(config, state, now)


def block(config, reason, *, now=None):
    """Preserve unresolved intents while surfacing an actionable manual hold."""
    _text(reason)
    state = read_state(config)
    state.update(proof=None, blocked_reason=reason, last_summary=reason)
    return _save(config, state, _now(now))
