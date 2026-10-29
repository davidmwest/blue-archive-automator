"""Stand aside after the account is signed in on another device.

Blue Archive ends this session when the same account signs in elsewhere, for
example when the player picks up their phone. Logging straight back in would
end that session in turn, so after the game shows that notice no job starts the
game again for two hours. The manager keeps queued jobs waiting until then.

End a hold early with ``end_early``, not by deleting its record: the notice is
usually still on screen, and the record is what marks it as already handled.
A successful login clears the record.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
from uuid import uuid4

from .ap_state import state_path as ap_path
from .fileops import replace_file

LOGGER = logging.getLogger(__name__)
HOLD = timedelta(hours=2)
DETECTOR = "signed_in_elsewhere"
# Live on Global (2026-10-14) the notice read "Duplicate login detected.
# Returning to title." over the Event Shop. Other phrasings name the other
# device, which no screen the automation visits does.
PHRASES = (
    "another device", "other device", "different device", "another terminal",
    "logged in elsewhere", "signed in elsewhere", "duplicate login", "duplicate log in",
    "multiple devices", "logged in from another", "accessed from another",
)


def state_path(config):
    return ap_path(config).with_name("login-hold-" + ap_path(config).name[3:])


def signed_in_elsewhere(text: str) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return any(phrase in text for phrase in PHRASES)


def recorded(config) -> dict | None:
    """The last hold, ended or not, until a login proves the session is ours again."""
    try:
        value = json.loads(state_path(config).read_text(encoding="utf-8"))
        datetime.fromisoformat(value["until"])
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        LOGGER.warning("login hold: ignoring an unreadable record: %s", exc)
        return None


def held_until(config, now: datetime | None = None) -> datetime | None:
    """When the current hold ends, or None. An unreadable record holds nothing."""
    value = recorded(config)
    if value is None:
        return None
    until = datetime.fromisoformat(value["until"])
    now = now or datetime.now(timezone.utc)
    return until if until > now else None


def end_early(config, now: datetime | None = None) -> bool:
    """End the current hold now, keeping its record.

    The notice that started it is usually still on screen. Keeping the record
    tells the next start that this notice was already handled, so it relaunches
    the game instead of starting another hold over the same notice.
    """
    value = recorded(config)
    if value is None:
        return False
    now = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(value["until"]) > now:
        _write(config, {**value, "until": now.isoformat(), "ended_early": True})
    return True


def clear(config) -> None:
    """The game logged in again: a notice seen from now on is a new takeover."""
    state_path(config).unlink(missing_ok=True)


def _write(config, value: dict) -> None:
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def message(until: datetime) -> str:
    return (f"Waiting until {until.astimezone():%H:%M} before logging in again: "
            "the account was signed in on another device")


def begin(config, *, detail: str, evidence: str | None = None,
          now: datetime | None = None) -> datetime:
    """Record a two-hour hold from now; no input is sent to the game."""
    now = now or datetime.now(timezone.utc)
    until = now + HOLD
    _write(config, {"version": 1, "started_at": now.isoformat(), "until": until.isoformat(),
                    "detail": detail[:300], "evidence": evidence})
    from .actions import record_action
    record_action(config, "login_hold_started",
                  f"Signed out because the account was used on another device; "
                  f"no login until {until.astimezone():%H:%M}.",
                  task="system", until=until.isoformat(), evidence=evidence)
    LOGGER.warning("login hold: %s", message(until))
    return until


def inspect_after_failure(config, device, vision, run_dir=None) -> datetime | None:
    """After a failed task, start a hold if the game shows the sign-out notice.

    A failing job is the usual sign that the session was taken over. Look once,
    without input, before anything closes the game and the notice with it.
    Never raises: the original failure is what the caller reports.
    """
    try:
        # A recorded hold covers the notice already: one notice, one hold.
        if recorded(config) is not None or device.foreground_package() != config.package:
            return None
        png = device.screenshot()
        observation = vision.analyze(png)
        if getattr(observation, "detector", None) != DETECTOR:
            return None
        evidence = None
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            target = run_dir / "signed-in-elsewhere.png"
            target.write_bytes(png)
            evidence = str(target)
        return begin(config, detail=observation.detail, evidence=evidence)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not mask the failure
        LOGGER.warning("login hold: could not inspect the screen after a failure: %s", exc)
        return None
