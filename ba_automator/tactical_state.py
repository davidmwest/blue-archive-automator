"""Durable Tactical Challenge history and unreplayed battle intents.

Mutations require the caller's instance lock. Persist ``begin_battle`` before
Mobilize; only the matching verified result and one-ticket decrement can clear
it. A new game day never discards an unresolved intent.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from uuid import uuid4

from .club import game_day
from .tactical_battles import (
    BattleState,
    DEFAULT_PRESERVE_TICKETS,
    MAX_ATTEMPTS_PER_OPPONENT,
    MAX_RECORDED_ATTEMPTS,
    record_result,
    ticket_budget,
)


MAX_IDENTITIES = 1000
MAX_IDENTITY_BYTES = 8192
CONFLICTING_OUTCOMES = "Conflicting Tactical Challenge battle outcomes; inspect before retrying"


class TacticalStateError(RuntimeError):
    """An unresolved or unreadable battle state blocks further ticket use."""


def state_path(config):
    identity = hashlib.sha256(f"{config.serial}\0{config.package}".encode()).hexdigest()[:24]
    return config.state_dir / f"tactical-{identity}.json"


def empty_state():
    return {
        "version": 1,
        "day_key": None,
        "attempts": {},
        "identities": {},
        "retry_opponent_id": None,
        "pending": None,
        "last_tickets": None,
        "last_rank": None,
        "blocked_reason": None,
        "last_summary": None,
        "updated_at": None,
    }


def _text(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("Expected a nonempty identity or message")


def _count(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"An observed integer of at least {minimum} is required")


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("A timestamp must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("A timestamp must include its timezone")
    return parsed.astimezone(timezone.utc)


def _now(value):
    value = datetime.now(timezone.utc) if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("The current time must include its timezone")
    return value.astimezone(timezone.utc)


def battle_history(state):
    """Return the pure selection history from an initialized durable state."""
    return BattleState(state["day_key"], state["attempts"], state["retry_opponent_id"])


def _identities(value):
    """Keep a bounded JSON copy; perceptual matching belongs to the runner."""
    if not isinstance(value, dict) or len(value) > MAX_IDENTITIES:
        raise ValueError(f"Opponent identity storage supports at most {MAX_IDENTITIES} entries")
    copied = {}
    for identity, metadata in value.items():
        _text(identity)
        if len(identity) > 256 or not isinstance(metadata, dict):
            raise ValueError("Opponent identities need a short key and JSON object metadata")
        try:
            encoded = json.dumps(metadata, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Opponent identity metadata must be finite JSON data") from exc
        if len(encoded.encode("utf-8")) > MAX_IDENTITY_BYTES:
            raise ValueError("Opponent identity metadata exceeds its size limit")
        copied[identity] = json.loads(encoded)
    return copied


def _validate(state):
    if (not isinstance(state, dict) or set(state) != set(empty_state())
            or type(state["version"]) is not int or state["version"] != 1):
        raise ValueError("Unknown Tactical Challenge state format")
    if state["day_key"] is None:
        if state != empty_state():
            raise ValueError("An uninitialized Tactical Challenge state cannot contain history")
        return state
    history = battle_history(state)
    _identities(state["identities"])
    for key in ("blocked_reason", "last_summary"):
        if state[key] is not None:
            _text(state[key])
    if state["updated_at"] is not None:
        _timestamp(state["updated_at"])
    if state["last_tickets"] is not None:
        _count(state["last_tickets"])
    if state["last_rank"] is not None:
        _count(state["last_rank"], 1)
    pending = state["pending"]
    if pending is not None:
        keys = {"id", "opponent_id", "tickets_before", "day_key", "rank_before", "preserve", "created_at"}
        if not isinstance(pending, dict) or set(pending) not in (keys, keys | {"outcome"}):
            raise ValueError("Invalid Tactical Challenge pending intent")
        _text(pending["id"])
        _text(pending["opponent_id"])
        _count(pending["rank_before"], 1)
        _timestamp(pending["created_at"])
        if pending["day_key"] != history.day_key:
            raise ValueError("The pending intent must remain attached to its original game day")
        if ticket_budget(pending["tickets_before"], pending["preserve"]) < 1:
            raise ValueError("The pending battle exceeds its observed ticket reserve")
        # An old release may already have entered its permitted second battle.
        # Preserve that intent for reconciliation; begin_battle forbids new ones.
        if history.attempts.get(pending["opponent_id"], 0) >= MAX_RECORDED_ATTEMPTS:
            raise ValueError("The pending opponent already exhausted its attempts")
        if "outcome" in pending:
            outcome = pending["outcome"]
            if (not isinstance(outcome, dict)
                    or set(outcome) != {"won", "evidence", "observed_at"}
                    or type(outcome["won"]) is not bool):
                raise ValueError("A pending outcome requires a proven victory or defeat")
            _text(outcome["evidence"])
            if _timestamp(outcome["observed_at"]) < _timestamp(pending["created_at"]):
                raise ValueError("A proven outcome cannot predate its battle intent")
    return state


def read_state(config):
    try:
        value = json.loads(state_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        raise TacticalStateError("Cannot read Tactical Challenge state; ticket spending is blocked") from exc
    try:
        # Earlier files stored attempts without perceptual identity metadata.
        # Upgrade in memory without rewriting or discarding unresolved work.
        if isinstance(value, dict) and "identities" not in value:
            value = dict(value, identities={})
        _validate(value)
        value["retry_opponent_id"] = None
        return value
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise TacticalStateError("Invalid Tactical Challenge state; ticket spending is blocked") from exc


def ensure_restart_safe(config):
    """Preserve the game screen until an interrupted battle's result is saved.

    Call under the instance lock before automatically stopping or relaunching
    the app. A durable, uncontested outcome can survive a restart and later be
    reconciled with the ticket count; an unrecorded result cannot.
    """
    try:
        state = read_state(config)
    except TacticalStateError as exc:
        raise TacticalStateError(
            "Cannot verify Tactical Challenge recovery state; leave Blue Archive open "
            "and inspect the saved battle state before restarting"
        ) from exc
    pending = state["pending"]
    if pending and ("outcome" not in pending or state["blocked_reason"] == CONFLICTING_OUTCOMES):
        raise TacticalStateError(
            "An unresolved Tactical Challenge battle has no verified result saved; "
            "leave Blue Archive open and inspect its battle result before restarting"
        )


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


def _save(config, state, now):
    state["updated_at"] = now.isoformat()
    write_state(config, state)
    return state


def _unblocked(state):
    if state["pending"] is not None:
        raise TacticalStateError("A previous Tactical Challenge battle is unresolved; inspect it before retrying")
    if state["blocked_reason"]:
        raise TacticalStateError(state["blocked_reason"])


def state_for_day(config, day_key, *, now=None):
    """Initialize a day or clear resolved older history; unresolved work stays."""
    BattleState(day_key)
    now = _now(now)
    state = read_state(config)
    _unblocked(state)
    if state["day_key"] != day_key:
        state = empty_state()
        state["day_key"] = day_key
        state["last_summary"] = "Starting a new Tactical Challenge game day"
        return _save(config, state, now)
    return state


def observe_ladder(config, day_key, *, tickets, rank, preserve=DEFAULT_PRESERVE_TICKETS, now=None):
    """Save current counts without resetting any same-day opponent history."""
    budget = ticket_budget(tickets, preserve)
    _count(rank, 1)
    now = _now(now)
    state = state_for_day(config, day_key, now=now)
    state.update(last_tickets=tickets, last_rank=rank)
    if budget == 0:
        state["retry_opponent_id"] = None
        state["last_summary"] = f"Manual-play reserve reached; {tickets} ticket(s) remain"
    return _save(config, state, now)


def save_identities(config, day_key, identities, *, now=None):
    """Persist canonical identity evidence before entry, until the daily reset.

    The runner merges observations and handles perceptual aliases. Replacing
    its snapshot cannot erase stored evidence for an already attempted target.
    """
    identities = _identities(identities)
    now = _now(now)
    state = state_for_day(config, day_key, now=now)
    protected = set(state["attempts"]) & set(state["identities"])
    if not protected <= set(identities):
        raise TacticalStateError("Keep identity evidence for opponents already attempted today")
    state["identities"] = identities
    return _save(config, state, now)


def begin_battle(config, day_key, opponent_id, tickets_before, rank_before,
                 *, preserve=DEFAULT_PRESERVE_TICKETS, now=None):
    """Return a persisted intent ID; only then may the runner tap Mobilize."""
    _text(opponent_id)
    _count(rank_before, 1)
    if rank_before == 1:
        raise TacticalStateError("Already first in Tactical Challenge; no higher rank to challenge")
    if ticket_budget(tickets_before, preserve) < 1:
        raise TacticalStateError("Tactical Challenge manual-play ticket reserve has been reached")
    now = _now(now)
    if game_day(now) != day_key:
        raise TacticalStateError("The game day changed before battle reservation; observe refreshed tickets first")
    state = state_for_day(config, day_key, now=now)
    if state["attempts"].get(opponent_id, 0) >= MAX_ATTEMPTS_PER_OPPONENT:
        raise TacticalStateError("This opponent has already been fought today")
    pending = {
        "id": uuid4().hex,
        "opponent_id": opponent_id,
        "tickets_before": tickets_before,
        "day_key": day_key,
        "rank_before": rank_before,
        "preserve": preserve,
        "created_at": now.isoformat(),
    }
    state.update(pending=pending, last_tickets=tickets_before, last_rank=rank_before,
                 last_summary="Battle entry reserved; waiting for its verified result")
    _save(config, state, now)
    return pending["id"]


def _uncertain(config, state, reason, now):
    state.update(blocked_reason=reason, last_summary=reason)
    _save(config, state, now)
    raise TacticalStateError(reason)


def record_outcome(config, intent_id, *, won, evidence, now=None):
    """Persist an observed result before dismissing it or reading the menu.

    This never completes the expenditure by itself. Only a fresh, same-day
    ticket observation can reconcile the saved result after a process restart.
    """
    if type(won) is not bool:
        raise ValueError("Victory or defeat must be explicitly observed")
    _text(evidence)
    now = _now(now)
    state = read_state(config)
    pending = state["pending"]
    if not pending or pending["id"] != intent_id:
        raise TacticalStateError("No matching Tactical Challenge intent; do not replay a battle")
    if now < _timestamp(pending["created_at"]):
        raise ValueError("A battle result cannot predate its pending intent")
    previous = pending.get("outcome")
    if previous is not None:
        if previous["won"] != won:
            _uncertain(config, state, CONFLICTING_OUTCOMES, now)
        return state
    pending["outcome"] = {"won": won, "evidence": evidence, "observed_at": now.isoformat()}
    state["last_summary"] = "Battle outcome saved; waiting for the verified ticket decrement"
    return _save(config, state, now)


def reconcile_pending(config, day_key, *, tickets, rank,
                      preserve=DEFAULT_PRESERVE_TICKETS, now=None):
    """Recover a proven result using a fresh same-day menu, never rank alone."""
    BattleState(day_key)
    now = _now(now)
    state = read_state(config)
    pending = state["pending"]
    if pending is None:
        return observe_ladder(config, day_key, tickets=tickets, rank=rank, preserve=preserve, now=now)
    if pending["day_key"] != day_key:
        raise TacticalStateError("Unresolved Tactical Challenge battle crossed the game-day reset; inspect it manually")
    outcome = pending.get("outcome")
    if outcome is None:
        raise TacticalStateError("The pending Tactical Challenge battle has no proven outcome; inspect before retrying")
    if now < _timestamp(outcome["observed_at"]):
        raise ValueError("The current menu cannot predate the saved battle outcome")
    _count(rank, 1)
    return complete_battle(config, pending["id"], tickets_after=tickets, won=outcome["won"],
                           rank_after=rank, preserve=preserve, now=now)


def complete_battle(config, intent_id, *, tickets_after, won, rank_after=None,
                    preserve=None, now=None):
    """Reconcile only a proven outcome and an exact one-ticket decrement.

    A rank change alone is not proof of a victory: a defense can change rank
    independently. Keep an ambiguous intent even across a reset or process
    restart, until the exact pending action can be reconciled.
    """
    now = _now(now)
    state = read_state(config)
    pending = state["pending"]
    if not pending or pending["id"] != intent_id:
        raise TacticalStateError("No matching Tactical Challenge intent; do not replay a battle")
    if game_day(now) != pending["day_key"]:
        raise TacticalStateError("Unresolved Tactical Challenge battle crossed the game-day reset; inspect it manually")
    if state["blocked_reason"] == CONFLICTING_OUTCOMES:
        raise TacticalStateError(CONFLICTING_OUTCOMES)
    if (type(tickets_after) is not int or tickets_after != pending["tickets_before"] - 1
            or type(won) is not bool
            or (rank_after is not None and (type(rank_after) is not int or rank_after < 1))):
        _uncertain(config, state,
                   "Tactical Challenge result or ticket decrement is uncertain; inspect before retrying", now)
    if now < _timestamp(pending["created_at"]):
        raise ValueError("A battle result cannot predate its pending intent")
    outcome = pending.get("outcome")
    if outcome is not None:
        if outcome["won"] != won:
            _uncertain(config, state, CONFLICTING_OUTCOMES, now)
        if now < _timestamp(outcome["observed_at"]):
            raise ValueError("The current menu cannot predate the saved battle outcome")
    reserve = pending["preserve"] if preserve is None else preserve
    budget = ticket_budget(tickets_after, reserve)
    history = battle_history(state)
    if history.attempts.get(pending["opponent_id"], 0):
        # Bookkeep an already-entered legacy retry without authorizing a new
        # battle under the current one-opponent-per-day rule.
        attempts = dict(history.attempts)
        attempts[pending["opponent_id"]] += 1
        history = BattleState(history.day_key, attempts)
    else:
        history = record_result(history, pending["opponent_id"], won)
    state.update(attempts=history.attempts,
                 retry_opponent_id=history.retry_opponent_id if budget > 0 else None,
                 pending=None, blocked_reason=None, last_tickets=tickets_after,
                 last_rank=rank_after,
                 last_summary=f"Verified Tactical Challenge {'victory' if won else 'defeat'}; {tickets_after} ticket(s) remain")
    return _save(config, state, now)


def block(config, reason, *, now=None):
    """Surface an actionable hold while retaining all pending/history evidence."""
    _text(reason)
    state = read_state(config)
    if state["day_key"] is None:
        raise TacticalStateError("Initialize the Tactical Challenge game day before recording a hold")
    state.update(blocked_reason=reason, last_summary=reason)
    return _save(config, state, _now(now))
