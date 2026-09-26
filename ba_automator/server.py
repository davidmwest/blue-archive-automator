"""Loopback dashboard with an in-memory FIFO queue and one task subprocess.

Queued jobs do not survive server shutdown. Each dispatched job receives a private
configuration snapshot and run directory, so its frames cannot be confused with
earlier runs or an independently started CLI runner. Startup runs no manual job;
enabled schedules can enqueue work when due. Periodic check-ins default to every
30 minutes and reuse successful home scans from other jobs. Daily
attempts survive restarts and require a manual retry after failure. Failed cafe jobs
retry after 15 minutes, up to three consecutive failures, then require Resume.
The Do everything control resumes dispatch and coalesces a catch-up request
after existing work, using due schedules and a fresh notification/AP scan.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import ipaddress
import json
import hashlib
import logging
import math
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .adb import AdbDevice
from .actions import record_action
from .config import Config, ConfigError
from .crafting_state import CraftStateError, read_state, scheduled_jobs, timestamp
from .locking import InstanceLock, LockError
from .tasks import RUN_PREFIXES, TASK_LABELS, TASKS, task_plan
from .home_badges import PRIORITY
from . import packs_state
from . import ap_state, loot, daily_log, daily_schedule, checkin_schedule
from .club import game_day
from .vision import decode_frame


LOGGER = logging.getLogger(__name__)
RESTART_SETTINGS = {"auto_download", "poll_interval", "startup_timeout", "download_timeout", "unknown_timeout"}
CAFE_SETTINGS = {"cafe_schedule_enabled": "schedule_enabled", "cafe_invite_enabled": "invite_enabled",
                 "cafe_invite_student": "invite_student"}
LESSONS_SETTINGS = {"lessons_strategy": "strategy", "lessons_max_tickets": "max_tickets",
                    "lessons_locations": "locations", "lessons_enabled_in_daily": "enabled_in_daily"}
AUTOMATION_SETTINGS = {"close_app_when_idle"}
DAILY_SETTINGS = {"daily_schedule_enabled": "schedule_enabled",
                  "daily_reset_delay_minutes": "reset_delay_minutes"}
CHECKIN_SETTINGS = {"checkin_schedule_enabled": "schedule_enabled",
                    "checkin_interval_minutes": "interval_minutes"}
CRAFTING_SETTINGS = {"crafting_schedule_enabled": "schedule_enabled"}
PACKS_SETTINGS = {f'packs_{key}_{suffix}': f'{key}_{suffix}'
                  for key in packs_state.PACKS for suffix in ('enabled', 'max_cents')}
AP_SETTINGS = {f'ap_{key}': key for key in
               ('schedule_enabled', 'floor', 'strategy', 'hard_default_order', 'hard_order')}
TOTAL_ASSAULT_SETTINGS = {f"total_assault_{key}": key
                          for key in ("difficulty", "enabled_in_daily", "comfort_seconds")}
TICKET_SETTINGS = {"bounties_enabled_in_daily", "scrimmages_enabled_in_daily"}
SETTINGS = CHECKIN_SETTINGS.keys() | DAILY_SETTINGS.keys() | TOTAL_ASSAULT_SETTINGS.keys() | TICKET_SETTINGS | RESTART_SETTINGS | CAFE_SETTINGS.keys() | LESSONS_SETTINGS.keys() | AUTOMATION_SETTINGS | CRAFTING_SETTINGS.keys() | PACKS_SETTINGS.keys() | AP_SETTINGS.keys()
CAFE_INTERVAL = timedelta(hours=3, seconds=15)
CAFE_RETRY_INTERVAL = timedelta(minutes=15)
MAX_SCHEDULE_FAILURES = 3


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _toml(document: dict) -> str:
    """Serialize the small validated config schema without reinterpreting paths."""
    lines = []
    for section, values in document.items():
        lines.append(f"[{section}]")
        for name, value in values.items():
            encoded = json.dumps(value, ensure_ascii=False)
            lines.append(f"{name} = {encoded}")
        lines.append("")
    return "\n".join(lines)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _config_document(config: Config) -> dict:
    document = {
        "device": {name: getattr(config, name) for name in ("serial", "package", "adb_path")},
        "restart": {name: getattr(config, name) for name in (
            "auto_download", "poll_interval", "startup_timeout", "download_timeout",
            "unknown_timeout", "home_confirmations", "action_cooldown",
        )},
        "storage": {"run_dir": str(config.run_dir), "lock_dir": str(config.lock_dir)},
        "automation": {name: getattr(config, name) for name in AUTOMATION_SETTINGS},
        "daily": {name: getattr(config, attribute) for attribute, name in DAILY_SETTINGS.items()},
        "checkin": {name: getattr(config, attribute) for attribute, name in CHECKIN_SETTINGS.items()},
        "lessons": {name: getattr(config, attribute) for attribute, name in LESSONS_SETTINGS.items()},
        "crafting": {"schedule_enabled": config.crafting_schedule_enabled},
        "packs": {name: getattr(config, attribute) for attribute, name in PACKS_SETTINGS.items()},
        "bounties": {"enabled_in_daily": config.bounties_enabled_in_daily},
        "scrimmages": {"enabled_in_daily": config.scrimmages_enabled_in_daily},
        "total_assault": {name: getattr(config, attribute) for attribute, name in TOTAL_ASSAULT_SETTINGS.items()},
        "ap": {name: getattr(config, attribute) for attribute, name in AP_SETTINGS.items()},
    }
    if hasattr(config, "state_dir"):
        document["storage"]["state_dir"] = str(config.state_dir)
    if hasattr(config, "cafe_schedule_enabled"):
        document["cafe"] = {name: getattr(config, attribute) for attribute, name in CAFE_SETTINGS.items()}
    return document


class DashboardController:
    """Thread-safe queue state; injectable process/device factories keep tests offline."""

    def __init__(self, config_path: Path, *, process_factory=subprocess.Popen, device_factory=AdbDevice,
                 wall_clock=None):
        self.config_path = Path(config_path).expanduser().resolve()
        self.config = Config.from_file(self.config_path)
        self.csrf_token = secrets.token_urlsafe(32)
        self._process_factory = process_factory
        self._device_factory = device_factory
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[dict] = deque()
        self._history: deque[dict] = deque(maxlen=100)
        self._logs: deque[dict] = deque(maxlen=400)
        self._paused = False
        self._shutdown = False
        self._capturing = False
        self._stop_requested = False
        self._escalating = False
        self._process = None
        self._current: dict | None = None
        self._state = "idle"
        self._task: str | None = None
        self._phase = "Ready"
        self._started_at: str | None = None
        self._completed_at: str | None = None
        self._started_clock: float | None = None
        self._duration: float | None = None
        self._result: dict | None = None
        self._frame_root: Path | None = None
        self._capture_frame: Path | None = None
        self._app_closed = False
        self._batch_has_game_job = False
        self._badge_attempted = set()
        self._ap_batch_observed = None
        self._run_available_pending = False
        self._run_available_active = False
        self._job_roots: dict[str, Path] = {}
        self._popup_cache: dict[Path, tuple[tuple[int, int], list[dict]]] = {}
        self._schedule = self._load_schedule()
        self._daily_schedule_error = None
        self._daily_not_before = None
        self._checkin_schedule_error = None
        self._checkin_not_before = None
        self._checkin_initializing = True
        self._checkin_reset_pending = False
        self._checkin_pending_record = None
        self._packs_not_before = None
        self._ap_not_before = None
        self._failures = self._load_failures()
        self._daily("dashboard_started", detail="Local serial queue started")
        self._initialize_checkin()
        self._worker = threading.Thread(target=self._work, name="blue-archive-queue", daemon=True)
        self._worker.start()

    def _daily(self, event, *, level="INFO", **fields):
        try:
            daily_log.append_event(self._state_dir(), event, timestamp=self._wall_clock(),
                                   level=level, **fields)
        except (OSError, ValueError, TypeError):
            LOGGER.error("Could not append the daily log", exc_info=True, extra={"skip_daily": True})

    def _queued(self, job):
        self._daily("job_queued", task=job["task"], run=job["id"],
                    source=job.get("source", "manual"), slots=job.get("slots"))

    def _log(self, message: str, level: str = "info", *, child=False) -> None:
        with self._condition:
            self._logs.append({"time": _timestamp(), "message": message[:2000], "level": level})
        (LOGGER.error if level == "error" else LOGGER.info)("dashboard: %s", message[:2000],
                                                            extra={"skip_daily": True})
        if not child:
            self._daily("diagnostic", level=level.upper(), detail=message)

    def _public_config(self) -> dict:
        values = {name: getattr(self.config, name) for name in ("serial", "package", *sorted(RESTART_SETTINGS))}
        values.update({name: getattr(self.config, name, "" if name.endswith("student") else False)
                       for name in CAFE_SETTINGS})
        values.update({name: getattr(self.config, name) for name in AUTOMATION_SETTINGS})
        values.update({name: getattr(self.config, name) for name in DAILY_SETTINGS})
        values.update({name: getattr(self.config, name) for name in CHECKIN_SETTINGS})
        values.update({name: getattr(self.config, name) for name in LESSONS_SETTINGS})
        values.update({name: getattr(self.config, name) for name in CRAFTING_SETTINGS})
        values.update({name: getattr(self.config, name) for name in PACKS_SETTINGS})
        values.update({name: getattr(self.config, name) for name in AP_SETTINGS.keys() | TICKET_SETTINGS | TOTAL_ASSAULT_SETTINGS.keys()})
        return values

    def _state_dir(self) -> Path:
        return getattr(self.config, "state_dir", self.config.run_dir.parent / "state")

    def _failures_path(self):
        identity = hashlib.sha256(f'{self.config.serial}\0{self.config.package}'.encode()).hexdigest()[:24]
        return self._state_dir() / f'failed-jobs-{identity}.json'

    def _load_failures(self):
        try:
            records = json.loads(self._failures_path().read_text())
            if isinstance(records, list):
                return [item for item in records[-50:] if isinstance(item, dict)
                        and all(isinstance(item.get(key), str) for key in ('id', 'task', 'time', 'detail'))]
        except (OSError, ValueError):
            pass
        return []

    def _save_failures(self):
        try:
            _write_atomic(self._failures_path(), json.dumps(self._failures[-50:], indent=2))
        except OSError as exc:
            self._log(f'Could not save failed-job notices: {exc}', 'error')

    def dismiss_failure(self, identifier):
        with self._condition:
            if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
                raise ApiError(400, 'A failed job id is required')
            dismissed = next((item for item in self._failures if item['id'] == identifier), None)
            self._failures = [item for item in self._failures if item['id'] != identifier]
            self._save_failures()
            if dismissed:
                self._daily("failure_notice_dismissed", task=dismissed["task"], run=identifier,
                            detail=dismissed["detail"], failed_at=dismissed["time"])
            # Acknowledging a notice never clears schedules or purchase holds.

    def _load_schedule(self) -> dict:
        try:
            value = json.loads((self._state_dir() / "schedule.json").read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("cafe"), dict):
                value.setdefault("crafting", {"not_before": None, "consecutive_failures": 0, "retry_paused": False})
                if not isinstance(value["crafting"], dict):
                    value["crafting"] = {"not_before": None, "consecutive_failures": 0, "retry_paused": True}
                return value
        except (OSError, ValueError):
            pass
        return {"crafting": {"not_before": None, "consecutive_failures": 0, "retry_paused": False},
                "cafe": {"last_success_at": None, "next_due_at": None,
                         "consecutive_failures": 0, "retry_paused": False}}

    def _save_schedule(self) -> None:
        try:
            _write_atomic(self._state_dir() / "schedule.json", json.dumps(self._schedule, indent=2) + "\n")
            self._daily("schedule_saved", schedule=self._schedule)
        except OSError as exc:
            self._log(f"Could not persist task schedules: {exc}", "error")

    def _daily_status(self) -> dict:
        now = self._wall_clock()
        delay = self.config.daily_reset_delay_minutes
        status = {"enabled": self.config.daily_schedule_enabled, "reset_hour_utc": 19,
                  "reset_delay_minutes": delay, "catch_up": True,
                  "current_game_day": game_day(now),
                  "next_due_at": daily_schedule.next_due(now, delay).isoformat(),
                  "blocked_reason": self._daily_schedule_error}
        try:
            saved = daily_schedule.read_state(self.config)
            status.update(saved)
            if daily_schedule.is_due(saved, now, delay):
                day = daily_schedule.due_day(now, delay)
                status["next_due_at"] = (datetime.fromisoformat(day).replace(
                    hour=19, tzinfo=timezone.utc) + timedelta(minutes=delay)).isoformat()
            if (saved["game_day"] == game_day(now) and saved["status"] == "running"
                    and not (self._current and self._current["id"] == saved["run_id"])):
                status["blocked_reason"] = "Previous Daily was interrupted; inspect the log before manually retrying Daily"
            elif saved["game_day"] == game_day(now) and saved["status"] in {"failed", "stopped"}:
                status["blocked_reason"] = "Daily did not finish; inspect the log and queue Daily to retry. The next game day still runs automatically."
            if self._daily_not_before and now < self._daily_not_before:
                status["next_due_at"] = max(datetime.fromisoformat(status["next_due_at"]),
                                             self._daily_not_before).isoformat()
        except (RuntimeError, OSError) as exc:
            status["blocked_reason"] = str(exc)
        return status

    def _enqueue_daily(self) -> None:
        if (self._paused or self._shutdown or not self.config.daily_schedule_enabled
                or self._daily_schedule_error or len(self._queue) >= 100
                or (self._current and self._current["task"] == "daily")
                or any(job["task"] == "daily" for job in self._queue)):
            return
        try:
            now = self._wall_clock()
            if self._daily_not_before and now < self._daily_not_before:
                return
            if not daily_schedule.is_due(daily_schedule.read_state(self.config), now,
                                         self.config.daily_reset_delay_minutes):
                return
        except (RuntimeError, OSError) as exc:
            self._daily_schedule_error = str(exc)
            self._log(f"Daily scheduling paused: {exc}", "error")
            return
        job = {"id": uuid4().hex, "task": "daily", "created_at": _timestamp(),
               "source": "schedule", "game_day": daily_schedule.due_day(
                   now, self.config.daily_reset_delay_minutes)}
        self._queue.append(job)
        self._queued(job)

    def _daily_state_lock(self):
        # A distinct short-lived lock protects occurrence claims across dashboard
        # processes without competing with a child that owns the device lock.
        return InstanceLock(replace(self.config, lock_dir=self.config.lock_dir / "daily-schedule"))

    def _claim_daily(self, job: dict) -> bool:
        """Persist before launching a child; a crash must never replay spending."""
        try:
            with InstanceLock(self.config), self._daily_state_lock():
                now = self._wall_clock()
                state = daily_schedule.read_state(self.config)
                if job.get("source") == "schedule":
                    if not self.config.daily_schedule_enabled or not daily_schedule.is_due(
                            state, now, self.config.daily_reset_delay_minutes):
                        self._daily("daily_occurrence_superseded", run=job["id"], task="daily")
                        return False
                # Recompute at dispatch: a job waiting across reset belongs to
                # the new game day, never an old backlog entry.
                day = game_day(now)
                daily_schedule.write_state(self.config, {
                    "version": 1, "game_day": day, "status": "running", "run_id": job["id"],
                    "started_at": now.isoformat(), "completed_at": None,
                })
                self._daily_schedule_error = None
                self._daily_not_before = None
                self._daily("daily_occurrence_started", task="daily", run=job["id"], game_day=day)
                return True
        except LockError:
            # A standalone CLI job may own the device. No Daily occurrence was
            # consumed, so wait briefly and let the timer try again.
            self._daily_not_before = self._wall_clock() + timedelta(minutes=1)
            self._log("Daily deferred for one minute: another runner owns the instance")
            return False
        except (RuntimeError, OSError) as exc:
            self._daily_schedule_error = str(exc)
            detail = f"Daily did not start because its saved schedule could not be updated: {exc}"
            self._log(detail, "error")
            self._failures.append({"id": job["id"], "task": "daily",
                                   "time": _timestamp(), "detail": detail})
            self._failures = self._failures[-50:]
            self._save_failures()
            return False

    def _finish_daily(self, job: dict) -> None:
        if job["task"] != "daily":
            return
        try:
            with self._daily_state_lock():
                state = daily_schedule.read_state(self.config)
                if state["run_id"] != job["id"]:
                    raise RuntimeError("Daily occurrence changed while the job was running")
                state.update(status=self._state if self._state in {"success", "stopped"} else "failed",
                             completed_at=self._wall_clock().isoformat())
                daily_schedule.write_state(self.config, state)
                self._daily("daily_occurrence_finished", task="daily", run=job["id"],
                            game_day=state["game_day"], status=state["status"])
        except (RuntimeError, OSError) as exc:
            self._daily_schedule_error = str(exc)
            self._log(f"Could not save Daily completion; automatic retries remain held: {exc}", "error")

    def _checkin_state_lock(self):
        return InstanceLock(replace(self.config, lock_dir=self.config.lock_dir / "checkin-schedule"))

    def _checkin_error(self, exc) -> None:
        detail = str(exc)
        if detail != self._checkin_schedule_error:
            self._log(f"Periodic check-ins held: {detail}", "error")
        self._checkin_schedule_error = detail

    def _initialize_checkin(self, *, reset=False) -> None:
        reset = reset or self._checkin_reset_pending
        try:
            with self._checkin_state_lock():
                try:
                    saved = checkin_schedule.read_state(self.config)
                except checkin_schedule.CheckinScheduleError:
                    if not reset:
                        raise
                    saved = None
                if saved is None:
                    saved = checkin_schedule.initial_state(self._wall_clock(), self.config.checkin_interval_minutes)
                    checkin_schedule.write_state(self.config, saved)
                elif reset:
                    saved["next_due_at"] = (self._wall_clock() + timedelta(
                        minutes=self.config.checkin_interval_minutes)).isoformat()
                    checkin_schedule.write_state(self.config, saved)
            self._checkin_schedule_error = None
            self._checkin_not_before = None
            self._checkin_initializing = False
            self._checkin_reset_pending = False
            if reset:
                self._daily("checkin_timer_reset", next_due_at=saved["next_due_at"],
                            enabled=self.config.checkin_schedule_enabled)
        except LockError:
            self._checkin_initializing = True
            self._checkin_reset_pending = reset
            self._checkin_schedule_error = None
            self._checkin_not_before = self._wall_clock() + timedelta(minutes=1)
            self._log("Check-in timer initialization deferred for one minute: schedule state is busy")
        except (RuntimeError, OSError) as exc:
            self._checkin_error(exc)

    def _checkin_status(self) -> dict:
        status = {"enabled": self.config.checkin_schedule_enabled,
                  "interval_minutes": self.config.checkin_interval_minutes,
                  "next_due_at": None, "status": None, "last_started_at": None,
                  "last_completed_at": None, "last_success_at": None,
                  "blocked_reason": self._checkin_schedule_error}
        if self._checkin_initializing and not self._checkin_schedule_error:
            status["next_due_at"] = self._checkin_not_before.isoformat() if self._checkin_not_before else None
            return status
        try:
            saved = checkin_schedule.read_state(self.config)
            if saved is None:
                raise checkin_schedule.CheckinScheduleError("Check-in schedule is missing; save its settings to reset the timer")
            status.update(saved)
            if self._checkin_not_before:
                status["next_due_at"] = max(checkin_schedule.parse_time(saved["next_due_at"]),
                                             self._checkin_not_before).isoformat()
        except (RuntimeError, OSError) as exc:
            status["blocked_reason"] = str(exc)
        return status

    def _enqueue_checkin(self) -> None:
        # Every normal job ends in the same scan. Existing work always goes
        # first; an overdue timer creates one visit, never a backlog.
        if (not self.config.checkin_schedule_enabled or self._paused or self._shutdown
                or self._capturing or self._current or self._queue or self._checkin_schedule_error):
            return
        if self._checkin_not_before and self._wall_clock() < self._checkin_not_before:
            return
        if self._checkin_initializing:
            self._initialize_checkin()
            if self._checkin_initializing or self._checkin_schedule_error:
                return
        if self._checkin_pending_record:
            pending_status, pending_job = self._checkin_pending_record
            self._record_checkin(pending_status, job=pending_job)
            if self._checkin_pending_record or self._checkin_schedule_error:
                return
        status = self._checkin_status()
        if status["blocked_reason"]:
            self._checkin_error(status["blocked_reason"])
            return
        if self._wall_clock() < checkin_schedule.parse_time(status["next_due_at"]):
            return
        job = {"id": uuid4().hex, "task": "red_dots", "source": "checkin", "created_at": _timestamp()}
        self._queue.append(job)
        self._queued(job)

    def _claim_checkin(self, job: dict) -> bool:
        if not self.config.checkin_schedule_enabled or self._checkin_schedule_error:
            return False
        try:
            with InstanceLock(self.config), self._checkin_state_lock():
                saved = checkin_schedule.read_state(self.config)
                if saved is None:
                    raise checkin_schedule.CheckinScheduleError("Check-in schedule is missing; save its settings to reset the timer")
                now = self._wall_clock()
                if now < checkin_schedule.parse_time(saved["next_due_at"]):
                    return False
                saved.update(status="running", run_id=job["id"], last_started_at=now.isoformat(),
                             last_completed_at=None,
                             next_due_at=(now + timedelta(minutes=self.config.checkin_interval_minutes)).isoformat())
                # A process crash/startup failure still consumes this interval.
                checkin_schedule.write_state(self.config, saved)
                self._checkin_not_before = None
                self._daily("checkin_started", task=job["task"], run=job["id"],
                            next_due_at=saved["next_due_at"])
                return True
        except LockError:
            self._checkin_not_before = self._wall_clock() + timedelta(minutes=1)
            self._log("Periodic check-in deferred for one minute: another runner owns the instance")
        except (RuntimeError, OSError) as exc:
            self._checkin_error(exc)
        return False

    def _record_checkin(self, status: str, *, job=None) -> None:
        """A verified scan covers the timer even when another job produced it."""
        if self._checkin_schedule_error or (status == "success" and not self.config.checkin_schedule_enabled):
            return
        if self._checkin_initializing:
            self._initialize_checkin()
            if self._checkin_initializing or self._checkin_schedule_error:
                if not self._checkin_schedule_error:
                    self._checkin_pending_record = (status, job)
                return
        try:
            with self._checkin_state_lock():
                saved = checkin_schedule.read_state(self.config)
                if saved is None:
                    raise checkin_schedule.CheckinScheduleError("Check-in schedule is missing; save its settings to reset the timer")
                now = self._wall_clock()
                saved.update(status=status, last_completed_at=now.isoformat(),
                             next_due_at=(now + timedelta(minutes=self.config.checkin_interval_minutes)).isoformat())
                if status == "success":
                    saved["last_success_at"] = now.isoformat()
                if job is not None:
                    saved["run_id"] = job["id"]
                checkin_schedule.write_state(self.config, saved)
                self._checkin_pending_record = None
                self._checkin_not_before = None
                self._daily("checkin_finished" if job else "checkin_covered_by_scan",
                            status=status, run=job["id"] if job else None,
                            next_due_at=saved["next_due_at"])
        except LockError:
            self._checkin_pending_record = (status, job)
            self._checkin_not_before = self._wall_clock() + timedelta(minutes=1)
            self._log("Check-in timer update deferred for one minute: schedule state is busy")
        except (RuntimeError, OSError) as exc:
            self._checkin_error(exc)

    def _enqueue_scheduled(self) -> None:
        if self._run_available_active and not self._current and not self._queue:
            self._run_available_active = False
        available = self._run_available_pending
        if available:
            # Finish the existing batch before deciding what remains useful.
            # This includes badge follow-ups and avoids overlapping a composite
            # Daily with an already-running Cafe, mail collection, or AP job.
            if self._paused or self._shutdown or self._capturing or self._current or self._queue:
                return
            self._run_available_pending = False
            self._run_available_active = True
            self._badge_attempted.clear()
            self._ap_batch_observed = None
        # Daily covers cafe, packs and AP; enqueue it first so those timers defer.
        self._enqueue_daily()
        self._enqueue_crafting()
        self._enqueue_packs()
        self._enqueue_ap()
        self._enqueue_cafe()
        if available:
            # Explicit catch-up always gets a fresh final home scan, even when
            # no schedule is due or the periodic check-in timer is disabled.
            # Its ordinary badge/AP follow-ups still honor opt-ins and holds.
            job = {"id": uuid4().hex, "task": "red_dots", "source": "available",
                   "created_at": _timestamp()}
            self._queue.append(job)
            self._queued(job)
            self._daily("available_work_queued", tasks=[job["task"] for job in self._queue])
        else:
            self._enqueue_checkin()

    def _enqueue_cafe(self) -> None:
        if (self._paused or self._shutdown or not getattr(self.config, "cafe_schedule_enabled", False)
                or self._schedule["cafe"].get("retry_paused", False)):
            return
        if ((self._current and self._current["task"] in {"cafe", "daily"})
                or any(job["task"] in {"cafe", "daily"} for job in self._queue)
                or len(self._queue) >= 100):
            return
        due = self._schedule["cafe"].get("next_due_at")
        if due:
            try:
                if self._wall_clock() < datetime.fromisoformat(due.replace("Z", "+00:00")):
                    return
            except (ValueError, TypeError, AttributeError):
                self._log("Saved cafe schedule has an invalid date; scheduling is paused", "error")
                self._schedule["cafe"]["retry_paused"] = True
                return
        self._queue.append({"id": uuid4().hex, "task": "cafe", "created_at": _timestamp(), "source": "schedule"})
        self._queued(self._queue[-1])

    def _packs_status(self):
        try:
            state = packs_state.read_state(self.config)
            return {**state, 'enabled': bool(packs_state.enabled(self.config))}
        except RuntimeError as exc:
            return {'enabled': bool(packs_state.enabled(self.config)), 'blocked_reason': str(exc)}

    def _ap_status(self):
        try:
            return {**ap_state.read_state(self.config), 'enabled': self.config.ap_schedule_enabled}
        except RuntimeError as exc:
            return {'enabled': self.config.ap_schedule_enabled, 'blocked_reason': str(exc)}

    def _enqueue_ap(self):
        if self._paused or self._shutdown or not self.config.ap_schedule_enabled or len(self._queue) >= 100:
            return
        related = {'spend_ap', 'scan_ap', 'cafe', 'mail', 'packs', 'daily', 'free_pack', 'club', 'red_dots', 'tasks'}
        if ((self._current and self._current['task'] in related)
                or any(job['task'] in related for job in self._queue)):
            return
        state = self._ap_status()
        now = self._wall_clock()
        if state.get('blocked_reason') or state.get('pending'):
            return
        if self._ap_not_before and now < self._ap_not_before:
            return
        due = state.get('next_check_at')
        if due and now < datetime.fromisoformat(due):
            return
        self._queue.append({'id': uuid4().hex, 'task': 'spend_ap',
                            'created_at': _timestamp(), 'source': 'schedule'})
        self._queued(self._queue[-1])
        self._ap_not_before = now + timedelta(minutes=15)

    def _enqueue_packs(self):
        if self._paused or self._shutdown or not packs_state.enabled(self.config) or len(self._queue) >= 100:
            return
        if ((self._current and self._current['task'] in {'packs', 'daily'})
                or any(job['task'] in {'packs', 'daily'} for job in self._queue)):
            return
        state = self._packs_status()
        if state.get('blocked_reason') or state.get('pending'):
            return
        now = self._wall_clock()
        if self._packs_not_before and now < self._packs_not_before:
            return
        due = state.get('next_check_at')
        if due and now < datetime.fromisoformat(due):
            return
        self._queue.append({'id': uuid4().hex, 'task': 'packs', 'created_at': _timestamp(), 'source': 'schedule'})
        self._queued(self._queue[-1])
        # Also bounds failures that occur in restart before the pack handler runs.
        self._packs_not_before = now + timedelta(minutes=15)

    def _crafting_status(self) -> dict:
        try:
            state = read_state(self.config)
            jobs = scheduled_jobs(state)
            return {"enabled": self.config.crafting_schedule_enabled, **self._schedule["crafting"],
                    "disabled_reason": state["disabled_reason"], "jobs": jobs,
                    "updated_at": state["updated_at"]}
        except CraftStateError as exc:
            return {"enabled": self.config.crafting_schedule_enabled, "disabled_reason": str(exc), "jobs": []}

    def _enqueue_crafting(self) -> None:
        if self._paused or self._shutdown or not self.config.crafting_schedule_enabled or len(self._queue) >= 100:
            return
        if ((self._current and self._current["task"] == "crafting")
                or any(job["task"] == "crafting" for job in self._queue)):
            return
        schedule = self._crafting_status()
        if schedule.get("disabled_reason") or schedule.get("retry_paused"):
            return
        now = self._wall_clock()
        try:
            if schedule.get("not_before") and now < timestamp(schedule["not_before"]):
                return
            due = [job for job in schedule["jobs"] if timestamp(job["due_at"]) <= now]
            if schedule["jobs"] and not due:
                return
        except (ValueError, TypeError, AttributeError):
            self._schedule["crafting"]["retry_paused"] = True
            self._save_schedule()
            self._log("Crafting has an invalid retry date; scheduling is paused", "error")
            return
        self._queue.append({"id": uuid4().hex, "task": "crafting", "created_at": _timestamp(),
                            "source": "schedule", "slots": [job["slot"] for job in due if job["slot"] is not None]})
        self._queued(self._queue[-1])

    def _record_crafting(self, task: str, state: str) -> None:
        if task != "crafting" or state not in {"success", "disabled", "failed"}:
            return
        saved = self._schedule["crafting"]
        failures = saved.get("consecutive_failures", 0)
        failures = (failures + 1 if type(failures) is int else 1) if state == "failed" else 0
        delay = timedelta(minutes=15) if state == "failed" else timedelta(seconds=30)
        saved.update({"consecutive_failures": failures, "retry_paused": failures >= MAX_SCHEDULE_FAILURES,
                      "not_before": (self._wall_clock() + delay).isoformat()})
        self._save_schedule()

    def _record_schedule(self, task: str, state: str) -> None:
        if task not in {"cafe", "daily"} or state not in {"success", "failed"}:
            return
        cafe = self._schedule["cafe"]
        now = self._wall_clock()
        if state == "success":
            cafe.update({"last_success_at": now.isoformat(), "next_due_at": (now + CAFE_INTERVAL).isoformat(),
                         "consecutive_failures": 0, "retry_paused": False})
        else:
            failures = cafe.get("consecutive_failures", 0)
            failures = failures + 1 if type(failures) is int else 1
            cafe.update({"next_due_at": (now + CAFE_RETRY_INTERVAL).isoformat(),
                         "consecutive_failures": failures, "retry_paused": failures >= MAX_SCHEDULE_FAILURES})
            self._log("Cafe schedule paused after three failed attempts; Resume re-enables retries"
                      if cafe["retry_paused"] else "Cafe retry is due in 15 minutes when scheduling is enabled", "error")
        self._save_schedule()

    def enqueue(self, task: str) -> dict:
        if not isinstance(task, str) or task not in TASKS:
            raise ApiError(400, "Task must be one of the supported queue jobs")
        with self._condition:
            if self._shutdown:
                raise ApiError(409, "The server is shutting down")
            if len(self._queue) >= 100:
                raise ApiError(409, "The queue already contains 100 jobs")
            if task == "daily" and ((self._current and self._current["task"] == "daily")
                                    or any(job["task"] == "daily" for job in self._queue)):
                raise ApiError(409, "Daily is already running or queued")
            job = {"id": uuid4().hex, "task": task, "created_at": _timestamp()}
            self._queue.append(job)
            self._queued(job)
            self._condition.notify_all()
            return dict(job)

    def run_available(self) -> dict:
        """Resume and coalesce one catch-up pass, without retrying held spending.

        Due work uses its ordinary scheduler and durable Daily occurrence.
        A fresh notification/AP scan discovers additional claims. No settings,
        spending holds, or failure notices are changed by this control.
        """
        with self._condition:
            if self._shutdown:
                raise ApiError(409, "The server is shutting down")
            if self._run_available_active and not self._current and not self._queue:
                self._run_available_active = False
            before = {job["id"] for job in self._queue}
            if not self._run_available_pending and not self._run_available_active:
                self._run_available_pending = True
                self._daily("available_work_requested", waiting_for_queue=bool(self._current or self._queue))
            self.resume()
            self._enqueue_scheduled()
            self._condition.notify_all()
            return {"ok": True, "jobs": [dict(job) for job in self._queue if job["id"] not in before],
                    "pending": self._run_available_pending, "active": self._run_available_active,
                    "queue_paused": self._paused}

    def cancel(self, job_id: str) -> None:
        with self._condition:
            if self._current and self._current["id"] == job_id:
                raise ApiError(409, "This job is already running; use Stop to interrupt it")
            for job in self._queue:
                if job["id"] == job_id:
                    if job.get("source") == "schedule" and job["task"] == "daily":
                        try:
                            with self._daily_state_lock():
                                saved = daily_schedule.read_state(self.config)
                                if saved["game_day"] is None or saved["game_day"] < job["game_day"]:
                                    daily_schedule.write_state(self.config, {
                                        "version": 1, "game_day": job["game_day"], "status": "skipped",
                                        "run_id": job["id"], "started_at": None,
                                        "completed_at": self._wall_clock().isoformat(),
                                    })
                        except (RuntimeError, OSError) as exc:
                            raise ApiError(409, f"Could not save Daily cancellation: {exc}") from exc
                    self._queue.remove(job)
                    if job.get("source") == "checkin":
                        self._record_checkin("skipped", job=job)
                    if job.get("source") == "schedule" and job["task"] == "cafe":
                        # Cancel skips this occurrence; leaving it overdue would
                        # silently enqueue the same cafe visit on the next tick.
                        now = self._wall_clock()
                        self._schedule["cafe"].update({
                            "last_skipped_at": now.isoformat(),
                            "next_due_at": (now + CAFE_INTERVAL).isoformat(),
                        })
                        self._save_schedule()
                    if job.get("source") == "schedule" and job["task"] == "crafting":
                        self._schedule["crafting"]["not_before"] = (self._wall_clock() + timedelta(minutes=15)).isoformat()
                        self._save_schedule()
                    self._history.appendleft({"id": job_id, "task": job["task"],
                                              "source": job.get("source", "manual"),
                                              "state": "stopped", "completed_at": _timestamp()})
                    self._daily("job_cancelled", task=job["task"], run=job_id)
                    return
            raise ApiError(404, "Queued job not found")

    def pause(self) -> None:
        with self._condition:
            if not self._paused:
                self._daily("queue_paused")
            self._paused = True

    def resume(self) -> None:
        with self._condition:
            if self._shutdown:
                raise ApiError(409, "The server is shutting down")
            self._paused = False
            self._daily("queue_resumed")
            if self._schedule["crafting"].get("retry_paused", False):
                self._schedule["crafting"].update({"retry_paused": False, "consecutive_failures": 0,
                                                   "not_before": None})
                self._save_schedule()
            if self._schedule["cafe"].get("retry_paused", False):
                self._schedule["cafe"].update({"retry_paused": False, "consecutive_failures": 0,
                                                "next_due_at": self._wall_clock().isoformat()})
                self._save_schedule()
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._paused = True
            self._daily("queue_stop_requested", task=self._task,
                        run=self._current["id"] if self._current else None)
            if not self._current:
                return
            self._stop_requested = True
            self._phase = "Stopping"
            process = self._process
            if process is not None and not self._escalating:
                self._escalating = True
                self._interrupt(process)

    def _interrupt(self, process) -> None:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        except (OSError, ProcessLookupError):
            pass

        def escalate():
            # Termination targets only this Python child, never the shared ADB server.
            for action, timeout in ((None, 8), (process.terminate, 3), (process.kill, 3)):
                try:
                    if action is not None and process.poll() is None:
                        action()
                    process.wait(timeout=timeout)
                    return
                except subprocess.TimeoutExpired:
                    continue
                except (OSError, ProcessLookupError):
                    return

        threading.Thread(target=escalate, name="blue-archive-stop", daemon=True).start()

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._shutdown:
                    self._enqueue_scheduled()
                    if self._queue and not self._paused and not self._capturing:
                        break
                    self._condition.wait(timeout=15)
                if self._shutdown:
                    return
                job = self._queue.popleft()
                if job["task"] == "daily" and not self._claim_daily(job):
                    continue
                if job.get("source") == "checkin" and not self._claim_checkin(job):
                    continue
                self._started_at = _timestamp()
                self._current = {"id": job["id"], "task": job["task"], "started_at": self._started_at,
                                 "source": job.get("source", "manual")}
                self._task = job["task"]
                self._batch_has_game_job = True
                self._badge_attempted.update(task_plan(job["task"], self.config))
                self._state = "running"
                self._phase = "Starting"
                self._completed_at = None
                self._started_clock = time.monotonic()
                self._duration = None
                self._result = None
                self._capture_frame = None
                self._app_closed = False
                self._logs.clear()
                self._stop_requested = False
                self._escalating = False
                # Each child owns a directory that no earlier job has used.
                job_root = self.config.run_dir / f"dashboard-{job['id']}"
                run_root = job_root / "runs"
                self._frame_root = run_root
                self._job_roots[job["id"]] = run_root
                if len(self._job_roots) > 100:
                    self._job_roots.pop(next(iter(self._job_roots)))
                selected = replace(self.config, run_dir=run_root)
                self._daily("job_started", task=job["task"], run=job["id"],
                            source=job.get("source", "manual"), plan=task_plan(job["task"], selected))
            exit_code = 1
            output = ""
            process = None
            try:
                snapshot = job_root / "config.toml"
                _write_atomic(snapshot, _toml(_config_document(selected)))
                arguments = [sys.executable, "-m", "ba_automator", "--config", str(snapshot), job["task"]]
                if job['task'] == 'packs' and job.get('source') != 'schedule':
                    arguments.append('--retry-packs')
                if job['task'] == 'spend_ap' and job.get('source') not in {'schedule', 'red_dot', 'ap_balance'}:
                    arguments.append('--retry-ap')
                options = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                           "stdin": subprocess.DEVNULL, "text": True, "encoding": "utf-8",
                           "errors": "replace", "bufsize": 1}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    options["start_new_session"] = True
                environment = os.environ.copy()
                environment["PYTHONUNBUFFERED"] = "1"
                environment["PYTHONIOENCODING"] = "utf-8"
                options["env"] = environment
                with self._condition:
                    # Stop and dispatch are serialized, including the process handle handoff.
                    if not self._stop_requested and not self._shutdown:
                        self._process = self._process_factory(arguments, **options)
                    process = self._process
                if process is not None:
                    assert process.stdout is not None
                    for line in process.stdout:
                        message = line.rstrip("\r\n")
                        output = (output + line)[-100000:]
                        if message:
                            self._log(message, "error" if message.startswith(("Error:", "Traceback")) else "info", child=True)
                            marker = next((prefix for prefix in ("total_assault:", "assault_rewards:", "tactical_rewards:", "red_dots:", "free_pack:", "tasks:", "bounties:", "scrimmages:", "restart:", "club:", "cafe:", "crafting:", "lessons:", "packs:", "mail:", "spend_ap:", "scan_ap:") if prefix in message), None)
                            if marker:
                                with self._condition:
                                    if not self._stop_requested:
                                        self._phase = message.split(marker, 1)[1].strip()[:160]
                    exit_code = process.wait()
                    process.stdout.close()
            except Exception as exc:
                self._log(str(exc), "error")
                if process is not None and process.poll() is None:
                    # Do not dispatch another job if reading this child's output failed.
                    self._interrupt(process)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        with self._condition:
                            self._paused = True
                            self._shutdown = True
                        self._log("Child did not exit; dispatcher disabled to prevent overlapping jobs", "error")
            finally:
                with self._condition:
                    self._duration = max(0.0, time.monotonic() - self._started_clock)
                    self._completed_at = _timestamp()
                    summary = (self._parse_command_summary(output, run_root)
                               if job["task"] == "daily" else None)
                    self._result = ({**summary, "run_dir": str(run_root.resolve()),
                                     "duration": self._duration, "actions": None} if summary else
                                    self._parse_result(output, run_root, exclude_scan=job["task"] != "red_dots"))
                    if self._stop_requested or self._shutdown or (summary and summary["status"] == "stopped"):
                        self._state, self._phase = "stopped", "Stopped"
                    elif exit_code == 0 and self._result and self._result.get("status") == "disabled":
                        self._state, self._phase = "disabled", "Crafting disabled; check Quick Craft setup"
                    elif exit_code == 0 and self._result and self._result.get("status") in {"success", "deferred"}:
                        self._state = "success"
                        self._phase = TASK_LABELS[job["task"]]
                    else:
                        self._state = "failed"
                        self._phase = ("Daily finished with failures; check failed jobs"
                                       if summary and summary["status"] == "partial_failure"
                                       else "Task failed; check the log")
                    if self._state in {"failed", "stopped"} and not summary:
                        self._result = self._unfinished_result(run_root, self._state)
                    elif summary and self._state == "stopped":
                        self._result["status"] = "stopped"
                    elif summary and self._state == "failed" and summary["status"] == "success":
                        self._result["status"] = "failed"
                    self._daily("job_finished", task=job["task"], run=job["id"],
                                level="ERROR" if self._state == "failed" else "INFO",
                                status=self._state, duration=round(self._duration, 3),
                                exit_code=exit_code, result=self._result)
                    self._history.appendleft({"id": job["id"], "task": job["task"],
                                              "source": job.get("source", "manual"),
                                              "state": self._state, "completed_at": self._completed_at})
                    self._finish_daily(job)
                    if self._state == 'failed':
                        detail = (self._summary_failure_detail(summary) if summary else
                                  next((entry['message'] for entry in reversed(self._logs)
                                        if entry['message'].startswith('Error:')), 'Task failed; check the runner log and screenshots'))
                        self._failures.append({'id': job['id'], 'task': job['task'],
                                               'time': self._completed_at, 'detail': detail[:1500]})
                        self._failures = self._failures[-50:]
                        self._save_failures()
                        try:
                            record_action(selected, 'job_failed', detail[:1500], task=job['task'])
                        except OSError as exc:
                            self._log(f'Could not append failed-job action: {exc}', 'error')
                    cafe_result = (self._parse_result(output, run_root, task="cafe")
                                   if job["task"] in {"daily", "cafe"} else None)
                    # A later lesson failure or stop does not undo a verified cafe visit.
                    cafe_state = ("success" if (cafe_result and cafe_result.get("status") == "success")
                                  or (summary and "cafe" in summary["completed_tasks"])
                                  else self._state)
                    self._record_schedule(job["task"], cafe_state)
                    self._record_crafting(job["task"], self._state)
                    if job['task'] == 'spend_ap' and self._state in {'failed', 'stopped'}:
                        try:
                            with InstanceLock(selected):
                                state = ap_state.read_state(selected)
                                if state['pending'] or self._state == 'stopped':
                                    state['blocked_reason'] = state['blocked_reason'] or (
                                        'AP job has an unresolved spend; inspect its saved receipt'
                                        if state['pending'] else 'AP job was stopped; explicitly queue Spend AP to retry'
                                    )
                                elif not state['blocked_reason']:
                                    # Startup/navigation failures with no spend
                                    # intent must not permanently stop AP use.
                                    state['next_check_at'] = (self._wall_clock() + timedelta(minutes=15)).isoformat()
                                ap_state.write_state(selected, state)
                        except (RuntimeError, OSError) as exc:
                            self._log(f'Could not save AP failure hold: {exc}', 'error')
                    if job['task'] == 'packs' and self._state in {'failed', 'stopped'}:
                        try:
                            with InstanceLock(selected):
                                pack_state = packs_state.read_state(selected)
                                pack_state['blocked_reason'] = pack_state['blocked_reason'] or 'Pack job failed or stopped; review the log, then manually queue a pack check'
                                packs_state.write_state(selected, pack_state)
                        except (RuntimeError, OSError) as exc:
                            self._log(f'Could not save pack failure hold: {exc}', 'error')
                    scanned = False
                    if self._state == "success" or (
                        self._state == "failed" and summary
                        and summary["status"] == "partial_failure"
                        and "red_dots" in summary["completed_tasks"]
                    ):
                        # An isolated Daily failure does not discard a verified
                        # final scan. Batch deduplication still prevents retries.
                        scanned = self._enqueue_badges(output, run_root)
                    if job.get("source") == "checkin" and not scanned:
                        self._record_checkin("stopped" if self._state == "stopped" else "failed", job=job)
                    self._current = None
                    self._process = None
                    # Materialize any due scheduled job before deciding the queue is done.
                    self._enqueue_scheduled()
                    self._close_app_after_queue(selected, process)
                    if not self._queue:
                        self._batch_has_game_job = False
                        self._badge_attempted.clear()
                        self._ap_batch_observed = None
                    self._condition.notify_all()

    def _enqueue_badges(self, output, run_root) -> bool:
        """Called under the queue lock. One attempt per task per busy batch."""
        scan = self._parse_result(output, run_root, task='red_dots')
        if not scan or scan['status'] != 'success':
            return False
        root = Path(scan['run_dir'])
        path = root / 'requests.json'
        try:
            if (root.is_symlink() or path.is_symlink()
                    or not path.resolve().is_relative_to(run_root.resolve())
                    or path.stat().st_size > 4096):
                raise ValueError('Invalid badge request path')
            value = json.loads(path.read_text())
            tasks = value['tasks']
            if (value.get('version') != 1 or not isinstance(tasks, list)
                    or any(not isinstance(task, str) or task not in PRIORITY for task in tasks)):
                raise ValueError('Invalid badge request tasks')
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self._log(f'Could not read notification requests: {exc}', 'error')
            return False
        self._record_checkin("success")
        covered = set(self._badge_attempted)
        for queued in self._queue:
            covered.update(task_plan(queued['task'], self.config))
        for task in PRIORITY:
            if len(self._queue) >= 100:
                break
            if task not in tasks or task in covered:
                continue
            self._queue.append({'id': uuid4().hex, 'task': task,
                                'created_at': _timestamp(), 'source': 'red_dot'})
            self._queued(self._queue[-1])
            covered.update(task_plan(task, self.config))
            self._log(f'Queued {task}: notification detected')
        ap = value.get('ap')
        valid_ap = type(ap) is int and 0 <= ap <= 9999
        pending_ap = any('spend_ap' in task_plan(j['task'], self.config) for j in self._queue)
        spent_here = self._parse_result(output, run_root, task='spend_ap') is not None
        queued_ap = False
        new_ap = (self._ap_batch_observed is not None and valid_ap and ap >= self._ap_batch_observed + 20)
        if (len(self._queue) < 100
                and self.config.ap_schedule_enabled and valid_ap and not pending_ap and not spent_here
                and ap >= self.config.ap_floor + 20
                and ('spend_ap' not in self._badge_attempted or new_ap)):
            state = self._ap_status()
            if not state.get('blocked_reason') and not state.get('pending'):
                self._queue.append({'id': uuid4().hex, 'task': 'spend_ap',
                                    'created_at': _timestamp(), 'source': 'ap_balance'})
                self._queued(self._queue[-1])
                self._log(f'Queued Spend AP: {ap} AP observed, floor {self.config.ap_floor}')
                queued_ap = True
        if valid_ap and (spent_here or queued_ap or self._ap_batch_observed is None or ap < self._ap_batch_observed):
            self._ap_batch_observed = ap
        return True

    def _close_app_after_queue(self, selected: Config, process) -> None:
        """Called once per completed job, under the dispatch/enqueue condition lock.

        Holding that lock through force-stop prevents a newly queued task from starting
        between the empty-queue check and game closure. The instance lock also excludes
        a separately launched CLI runner. No idle timer repeats this action.
        """
        if (not selected.close_app_when_idle or self._queue or self._current is not None
                or not self._batch_has_game_job
                or self._capturing or (process is not None and process.poll() is None)):
            return
        try:
            with InstanceLock(selected):
                device = self._device_factory(selected)
                device.connect()
                device.verify_package()
                device.force_stop()
        except Exception as exc:
            self._log(f"Could not close Blue Archive after the queue finished: {exc}", "error")
            return
        self._app_closed = True
        self._phase += "; Blue Archive closed"
        detail = "Closed Blue Archive after the task queue finished."
        self._log(detail)
        try:
            record_action(selected, "app_closed", detail, task="system")
        except (OSError, ValueError, TypeError) as exc:
            self._log(f"Blue Archive closed, but its action history could not be saved: {exc}", "error")

    @staticmethod
    def _unfinished_result(run_root: Path, status: str) -> dict:
        """Use the last task's journal, never an earlier successful step's result.

        A killed child may not have written its final record. Unknown counters stay
        null, and a successful earlier task is not relabeled as the failed task.
        Only owned, nonsymlink journals directly inside this job are inspected.
        """
        result = {"status": status, "run_dir": None, "duration": None, "actions": None}
        try:
            if run_root.is_symlink() or not run_root.is_dir():
                return result
            candidates = []
            for run in run_root.iterdir():
                if not run.name.startswith(RUN_PREFIXES) or run.is_symlink() or not run.is_dir():
                    continue
                journal = run / "events.jsonl"
                if journal.is_symlink() or not journal.is_file():
                    continue
                if not journal.resolve().is_relative_to(run_root.resolve()):
                    continue
                candidates.append((journal.stat().st_mtime_ns, run.name, journal))
            if not candidates:
                return result
            newest = max(candidates)[0]
            # Filesystems can give adjacent tasks identical modification times.
            # In a serial plan, prefer its unfinished/failed step over a tied
            # successful predecessor; task-name alphabetical order is irrelevant.
            for modified, _, journal in sorted(candidates, reverse=True):
                if modified != newest:
                    return result
                # Read only a bounded tail, even after a large survey.
                with journal.open("rb") as stream:
                    stream.seek(0, os.SEEK_END)
                    stream.seek(max(0, stream.tell() - 65536))
                    tail = stream.read(65536).decode("utf-8", errors="replace")
                events = []
                for line in tail.splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        events.append(event)
                terminal = next((event for event in reversed(events) if event.get("event") == "finished"), None)
                if not terminal or terminal.get("status") != "success":
                    break
            else:
                return result  # The child failed between tasks, after success.
            result["run_dir"] = str(journal.parent.resolve())
            if terminal and terminal.get("status") not in ("failed", "stopped", "interrupted"):
                terminal = None
            if terminal:
                elapsed = terminal.get("elapsed")
                actions = terminal.get("actions")
                if type(elapsed) in (int, float) and 0 <= elapsed < math.inf:
                    result["duration"] = elapsed
                if type(actions) is int and actions >= 0:
                    result["actions"] = actions
                if isinstance(terminal.get("reason"), str):
                    result["reason"] = terminal["reason"][:2000]
            return result
        except OSError:
            return result

    @staticmethod
    def _parse_command_summary(output: str, run_root: Path) -> dict | None:
        """Keep Daily's aggregate outcome separate from its last successful step."""
        decoder = json.JSONDecoder()
        tasks = TASKS - {"daily"}
        for index in range(len(output) - 1, -1, -1):
            if output[index] != "{":
                continue
            try:
                value, _ = decoder.raw_decode(output[index:])
                if (not isinstance(value, dict) or value.get("type") != "command_summary"
                        or value.get("command") != "daily"
                        or value.get("status") not in {"success", "partial_failure", "failed", "stopped"}
                        or type(value.get("aborted")) is not bool):
                    continue
                names = {}
                for key in ("completed_tasks", "skipped_tasks"):
                    entries = value.get(key)
                    if (not isinstance(entries, list) or len(entries) > len(tasks)
                            or any(not isinstance(task, str) or task not in tasks for task in entries)
                            or len(entries) != len(set(entries))):
                        raise ValueError("Invalid task list")
                    names[key] = entries
                failures = value.get("failed_tasks")
                deferred = value.get("deferred_tasks")
                if (not isinstance(failures, list) or len(failures) > len(tasks) * 2
                        or not isinstance(deferred, list) or len(deferred) > len(tasks)):
                    continue
                clean_failures = []
                for failure in failures:
                    if (not isinstance(failure, dict) or failure.get("task") not in tasks
                            or any(not isinstance(failure.get(key), str) for key in ("error", "error_type"))
                            or any(type(failure.get(key)) is not bool for key in ("recoverable", "recovery"))):
                        raise ValueError("Invalid task failure")
                    failed_run = failure.get("run_dir")
                    if failed_run is not None:
                        if not isinstance(failed_run, str):
                            raise ValueError("Invalid task evidence")
                        resolved = Path(failed_run).resolve()
                        if not resolved.is_relative_to(run_root.resolve()):
                            raise ValueError("Task evidence is outside this job")
                        failed_run = str(resolved)
                    clean_failures.append({"task": failure["task"], "error": failure["error"][:2000],
                                           "error_type": failure["error_type"][:100], "run_dir": failed_run,
                                           "recoverable": failure["recoverable"], "recovery": failure["recovery"]})
                clean_deferred = []
                for entry in deferred:
                    if (not isinstance(entry, dict) or entry.get("task") not in tasks
                            or not isinstance(entry.get("status"), str)):
                        raise ValueError("Invalid deferred task")
                    clean_deferred.append({"task": entry["task"], "status": entry["status"][:100]})
                if ((value["status"] == "success" and (failures or value["aborted"]))
                        or (value["status"] == "partial_failure" and (not failures or value["aborted"]))):
                    continue
                return {"type": "command_summary", "command": "daily", "status": value["status"],
                        **names, "failed_tasks": clean_failures, "deferred_tasks": clean_deferred,
                        "aborted": value["aborted"]}
            except (ValueError, TypeError, OSError):
                continue
        return None

    @staticmethod
    def _summary_failure_detail(summary: dict) -> str:
        prefix = "Daily stopped early" if summary["aborted"] else "Daily finished with failures"
        completed = len(summary["completed_tasks"])
        failures = "; ".join(
            f"{item['task'].replace('_', ' ')}{' recovery' if item['recovery'] else ''}: {item['error']}"
            for item in summary["failed_tasks"]
        )
        return f"{prefix}; {completed} tasks completed. {failures or 'Check the runner log.'}"

    @staticmethod
    def _parse_result(output: str, run_root: Path, *, task: str | None = None, exclude_scan=False) -> dict | None:
        decoder = json.JSONDecoder()
        for index in range(len(output) - 1, -1, -1):
            if output[index] != "{":
                continue
            try:
                value, _ = decoder.raw_decode(output[index:])
                if not isinstance(value, dict) or not {"status", "run_dir", "duration", "actions"} <= value.keys():
                    continue
                result_path = Path(value["run_dir"]).resolve()
                if not result_path.is_relative_to(run_root.resolve()):
                    continue
                if exclude_scan and result_path.name.startswith("red_dots-"):
                    continue
                if task is not None and not result_path.name.startswith(f"{task}-"):
                    continue
                return {name: value[name] for name in ("status", "run_dir", "duration", "actions")}
            except (ValueError, TypeError, OSError):
                continue
        return None

    def _frame(self) -> Path | None:
        # Called with the condition held. Never accept a path from an HTTP parameter.
        if self._capture_frame is not None:
            return self._capture_frame if self._capture_frame.is_file() and not self._capture_frame.is_symlink() else None
        root = self._frame_root
        if root is None or not root.is_dir():
            return None
        candidates = []
        try:
            for run in root.iterdir():
                if not run.name.startswith(RUN_PREFIXES) or not run.is_dir() or run.is_symlink():
                    continue
                for frame in run.iterdir():
                    if (frame.name == "home.png" or
                            (frame.name.startswith("trace-") and frame.suffix == ".png")):
                        if frame.is_file() and not frame.is_symlink() and frame.resolve().is_relative_to(root.resolve()):
                            candidates.append(frame)
            return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None
        except OSError:
            return None

    def status(self) -> dict:
        with self._condition:
            frame = self._frame()
            version = None
            if frame is not None:
                try:
                    metadata = frame.stat()
                    version = f"{metadata.st_mtime_ns}-{metadata.st_size}"
                except OSError:
                    frame = None
            duration = (time.monotonic() - self._started_clock
                        if self._current and self._started_clock is not None else self._duration)
            return {"state": self._state, "task": self._task, "phase": self._phase,
                    "started_at": self._started_at, "completed_at": self._completed_at,
                    "duration_seconds": duration, "config": self._public_config(),
                    "logs": list(self._logs), "has_frame": frame is not None,
                    "frame_version": version, "csrf_token": self.csrf_token,
                    "result": dict(self._result) if self._result else None,
                    "queue": [dict(job) for job in self._queue],
                    "current_job": dict(self._current) if self._current else None,
                    "history": list(self._history), "queue_paused": self._paused,
                    "run_available_pending": self._run_available_pending,
                    "run_available_active": self._run_available_active,
                    "failed_jobs": list(reversed(self._failures)),
                    "app_closed": self._app_closed,
                    "schedule": {"checkin": self._checkin_status(), "daily": self._daily_status(), "ap": self._ap_status(), "packs": self._packs_status(), "crafting": self._crafting_status(), "cafe": {**self._schedule["cafe"],
                                          "enabled": getattr(self.config, "cafe_schedule_enabled", False)}}}

    def actions(self) -> dict:
        """Read persistent important actions; filesystem evidence paths remain private."""
        with self._condition:
            path = self._state_dir() / "important-actions.jsonl"
        entries = deque(maxlen=1000)
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(value, dict):
                        continue
                    public = {name: value.get(name) for name in
                              ("id", "time", "task", "action", "detail", "student", "cafe", "status",
                               "location", "room", "owned_students", "student_count", "tickets_before", "tickets_after",
                               "strategy", "rank_before", "rank_after", "xp_before", "xp_after", "slots", "count",
                               "items", "pyroxenes", "balance_gains", "pack", "currency", "price_cents",
                               "stage", "ap_spent", "ap_before", "ap_after", "ap_cost", "rewards")}
                    entries.append(public)
        except FileNotFoundError:
            pass
        return {"actions": list(reversed(entries))}

    def loot(self) -> dict:
        with self._condition:
            return loot.snapshot(self.config)

    def daily_logs(self) -> dict:
        return {"today": daily_log.today(), "dates": daily_log.list_days(self._state_dir())}

    def daily_log_text(self, day) -> bytes:
        try:
            return daily_log.read_day(self._state_dir(), day).encode("utf-8")
        except ValueError as exc:
            raise ApiError(400, "A valid YYYY-MM-DD log date is required") from exc
        except OSError as exc:
            raise ApiError(404, "Daily log unavailable") from exc

    def clear_loot(self) -> dict:
        with self._condition:
            result = loot.clear(self.config)
            self._daily("loot_view_cleared", cleared_at=result["cleared_at"])
            return result

    def loot_image(self, identifier) -> bytes:
        with self._condition:
            path = loot.image_path(self.config, identifier)
            if path is None: raise ApiError(404, "Receipt unavailable")
            return path.read_bytes()

    def loot_icon(self, identifier) -> bytes:
        path = loot.icon_path(self.config, identifier)
        if path is None: raise ApiError(404, "Item icon unavailable")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ApiError(404, "Item icon unavailable") from exc

    def frame_bytes(self) -> bytes:
        with self._condition:
            frame = self._frame()
            if frame is None:
                raise ApiError(404, "No frame is available for the current job")
            return frame.read_bytes()

    def _popup_entries(self) -> dict[str, dict]:
        """Upsert journal evidence from the last 100 jobs owned by this server session."""
        entries = {}
        active_journals = set()
        for job_id, root in self._job_roots.items():
            if not root.is_dir() or root.is_symlink():
                continue
            for run in root.iterdir():
                if not run.name.startswith(RUN_PREFIXES) or not run.is_dir() or run.is_symlink():
                    continue
                journal = run / "events.jsonl"
                if not journal.is_file() or journal.is_symlink():
                    continue
                active_journals.add(journal)
                try:
                    stat = journal.stat()
                    version = (stat.st_mtime_ns, stat.st_size)
                    cached = self._popup_cache.get(journal)
                    if cached is None or cached[0] != version:
                        updates = {}
                        # A normal bounded restart journal is far smaller than this limit.
                        if stat.st_size > 5_000_000:
                            continue
                        for line in journal.read_text(encoding="utf-8").splitlines():
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue  # The worker may currently be appending the last line.
                            if (isinstance(event, dict) and event.get("event") == "popup_dismissal"
                                    and isinstance(event.get("id"), str)
                                    and re.fullmatch(r"[a-f0-9]{32}", event["id"])):
                                identifier = event["id"]
                                updates[identifier] = {**updates.get(identifier, {}), **event}
                        events = list(updates.values())
                        self._popup_cache[journal] = version, events
                    else:
                        events = cached[1]
                    for event in events:
                        identifier = event["id"]
                        paths = {}
                        for stage in ("before", "after"):
                            expected = f"popups/{identifier}-{stage}.png"
                            image = run / expected
                            if (event.get(stage) == expected and image.is_file() and not image.is_symlink()
                                    and image.resolve().is_relative_to(run.resolve())):
                                paths[stage] = image
                        entries[identifier] = {
                            "id": identifier, "job_id": job_id,
                            "detail": str(event.get("detail", ""))[:1000],
                            "detector": str(event.get("detector", ""))[:160],
                            "time": str(event.get("time", ""))[:80],
                            "result": str(event.get("result", "pending"))[:80],
                            "after_state": event.get("after_state"),
                            "before_url": f"/api/popups/{identifier}/before" if "before" in paths else None,
                            "after_url": f"/api/popups/{identifier}/after" if "after" in paths else None,
                            "_paths": paths,
                        }
                except (OSError, UnicodeError):
                    continue
        self._popup_cache = {path: entry for path, entry in self._popup_cache.items() if path in active_journals}
        return entries

    def popups(self) -> dict:
        with self._condition:
            entries = self._popup_entries()
            records = [{key: value for key, value in entry.items() if key != "_paths"}
                       for entry in entries.values()]
            records.sort(key=lambda item: item["time"], reverse=True)
            return {"popups": records, "scope": "current_session"}

    def popup_image(self, identifier: str, stage: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier) or stage not in {"before", "after"}:
            raise ApiError(404, "Popup image not found")
        with self._condition:
            entry = self._popup_entries().get(identifier)
            image = entry.get("_paths", {}).get(stage) if entry else None
            if image is None:
                raise ApiError(404, "Popup image not found")
            return image.read_bytes()

    def capture(self) -> dict:
        with self._condition:
            if self._current or self._capturing or (self._queue and not self._paused):
                raise ApiError(409, "Capture is unavailable while a task is running or ready to start")
            self._capturing = True
            selected = self.config
        try:
            with InstanceLock(selected):
                device = self._device_factory(selected)
                device.connect()
                device.verify_package()
                if device.foreground_package() != selected.package:
                    raise ApiError(409, "Blue Archive must be in the foreground before capturing")
                png = device.screenshot()
                decode_frame(png)
                target = selected.run_dir.parent / "dashboard-capture.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
                try:
                    temporary.write_bytes(png)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                with self._condition:
                    self._capture_frame = target
                    self._app_closed = False
            return {"captured": True}
        finally:
            with self._condition:
                self._capturing = False
                self._condition.notify_all()

    def update_settings(self, changes: dict) -> dict:
        if not changes or changes.keys() - SETTINGS:
            raise ApiError(400, "Only supported task settings may be updated")
        with self._condition:
            if self._current or self._queue or self._capturing:
                raise ApiError(409, "Settings cannot change while jobs are running or queued")
            try:
                existing = Config.from_file(self.config_path)
                updated = replace(existing, **changes)  # Validate before touching the file.
                checkin_changes = changes.keys() & CHECKIN_SETTINGS.keys()
                reset_checkin = bool(checkin_changes) and (
                    any(getattr(existing, name) != getattr(updated, name) for name in checkin_changes)
                    or bool(self._checkin_status()["blocked_reason"]))
                if 'ap_hard_order' in changes and not updated.ap_hard_default_order:
                    try:
                        catalog = ap_state.read_state(updated)['hard_stages']
                    except RuntimeError as exc:
                        raise ConfigError(str(exc)) from exc
                    if set(updated.ap_hard_order) - set(catalog):
                        raise ConfigError('Rotation includes stages that were not verified with three stars; scan stages first')
                with self.config_path.open("rb") as stream:
                    document = tomllib.load(stream)
                for name, value in changes.items():
                    section = (name.split("_")[0] if name in TICKET_SETTINGS else
                               "checkin" if name in CHECKIN_SETTINGS else
                               "daily" if name in DAILY_SETTINGS else
                               "total_assault" if name in TOTAL_ASSAULT_SETTINGS else
                               "cafe" if name in CAFE_SETTINGS else
                               "ap" if name in AP_SETTINGS else
                               "packs" if name in PACKS_SETTINGS else
                               "crafting" if name in CRAFTING_SETTINGS else
                               "lessons" if name in LESSONS_SETTINGS else
                               "automation" if name in AUTOMATION_SETTINGS else "restart")
                    key = AP_SETTINGS.get(name, PACKS_SETTINGS.get(name, CAFE_SETTINGS.get(name, LESSONS_SETTINGS.get(name, CRAFTING_SETTINGS.get(name, name)))))
                    if name in TOTAL_ASSAULT_SETTINGS: key = TOTAL_ASSAULT_SETTINGS[name]
                    if name in DAILY_SETTINGS: key = DAILY_SETTINGS[name]
                    if name in CHECKIN_SETTINGS: key = CHECKIN_SETTINGS[name]
                    if name in TICKET_SETTINGS: key = "enabled_in_daily"
                    document.setdefault(section, {})[key] = value
                _write_atomic(self.config_path, _toml(document))
                self.config = Config.from_file(self.config_path)
                if reset_checkin:
                    self._initialize_checkin(reset=True)
                self._daily("settings_changed", changes=changes)
                self._condition.notify_all()
            except (ConfigError, ValueError, TypeError) as exc:
                raise ApiError(400, str(exc)) from exc
            return {"config": self._public_config()}

    def close(self) -> None:
        with self._condition:
            self._daily("dashboard_stopping", active=self._current, queued_count=len(self._queue))
            self._shutdown = True
            self.stop()
            self._condition.notify_all()
        self._worker.join(timeout=16)
        if self._worker.is_alive():
            LOGGER.error("Task worker did not finish before the shutdown deadline")


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_server(controller: DashboardController, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Construct a loopback server; caller owns serve_forever/shutdown and controller.close."""
    if not _loopback(host):
        raise ValueError("The dashboard must bind to a loopback address")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Port must be an integer from 0 to 65535")

    class Handler(BaseHTTPRequestHandler):
        server_version = "BlueArchiveDashboard/0.1"

        def log_message(self, format, *args):
            LOGGER.debug("dashboard HTTP: " + format, *args)

        def _send(self, status: int, payload: bytes, content_type: str, *, filename=None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            if filename:
                self.send_header("Content-Disposition", f'inline; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, status: int, document: dict) -> None:
            self._send(status, json.dumps(document).encode("utf-8"), "application/json; charset=utf-8")

        def _check_request(self, mutation=False) -> None:
            authority = self.headers.get("Host", "")
            try:
                parsed = urlsplit("http://" + authority)
                valid = (parsed.hostname is not None and _loopback(parsed.hostname)
                         and parsed.port == self.server.server_port and not parsed.username
                         and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise ApiError(403, "The Host header must identify this loopback dashboard")
            origin = self.headers.get("Origin")
            if origin is not None:
                try:
                    parsed_origin = urlsplit(origin)
                    same = (parsed_origin.scheme == "http" and parsed_origin.netloc.lower() == authority.lower()
                            and not parsed_origin.path and not parsed_origin.query and not parsed_origin.fragment)
                except ValueError:
                    same = False
                if not same:
                    raise ApiError(403, "Cross-origin dashboard requests are not allowed")
            if mutation and not secrets.compare_digest(self.headers.get("X-CSRF-Token", "").encode("utf-8"),
                                                       controller.csrf_token.encode("ascii")):
                raise ApiError(403, "Missing or invalid dashboard CSRF token")

        def _body(self) -> dict:
            if self.headers.get_content_type() != "application/json":
                raise ApiError(400, "Expected an application/json request")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ApiError(400, "Invalid Content-Length") from exc
            if not 0 <= length <= 8192:
                raise ApiError(413, "Request body is too large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, UnicodeError) as exc:
                raise ApiError(400, "Invalid JSON request") from exc
            if not isinstance(body, dict):
                raise ApiError(400, "The JSON request must be an object")
            return body

        def do_GET(self):
            try:
                self._check_request()
                target = urlsplit(self.path)
                if target.path == "/api/status":
                    self._json(200, controller.status())
                elif target.path == "/api/logs":
                    if target.query:
                        raise ApiError(400, "Log requests do not accept query parameters")
                    self._json(200, controller.daily_logs())
                elif match := re.fullmatch(r"/api/logs/(today|\d{4}-\d{2}-\d{2})\.log", target.path):
                    if target.query:
                        raise ApiError(400, "Log requests do not accept query parameters")
                    day = daily_log.today() if match[1] == "today" else match[1]
                    self._send(200, controller.daily_log_text(day), "text/plain; charset=utf-8", filename=f"{day}.log")
                elif target.path == "/api/frame":
                    if parse_qs(target.query).keys() - {"v"}:
                        raise ApiError(400, "Frame requests accept only a version parameter")
                    self._send(200, controller.frame_bytes(), "image/png")
                elif target.path == "/api/map":
                    resource = files("ba_automator").joinpath("assets", "home_map.json")
                    self._json(200, json.loads(resource.read_text(encoding="utf-8"))
                               if resource.is_file() else {"width": 1280, "height": 720, "buttons": []})
                elif target.path == "/api/popups":
                    self._json(200, controller.popups())
                elif match := re.fullmatch(r"/api/loot/icons/([a-f0-9]{64})", target.path):
                    self._send(200, controller.loot_icon(match[1]), "image/png")
                elif target.path == "/api/loot":
                    self._json(200, controller.loot())
                elif match := re.fullmatch(r"/api/loot/([a-f0-9]{32})/receipt", target.path):
                    self._send(200, controller.loot_image(match[1]), "image/png")
                elif target.path == "/api/actions":
                    self._json(200, controller.actions())
                elif match := re.fullmatch(r"/api/popups/([a-f0-9]{32})/(before|after)", target.path):
                    if parse_qs(target.query).keys() - {"v"}:
                        raise ApiError(400, "Popup images accept only a version parameter")
                    self._send(200, controller.popup_image(*match.groups()), "image/png")
                elif target.path in {"/", "/index.html", "/app.js", "/style.css", "/maid-arisu.png",
                                     "/maid-arisu-checklist.png", "/maid-arisu-tea.png", "/maid-arisu-loot.png",
                                     "/ap", "/ap.html", "/ap.js", "/ap.css"}:
                    name = "index.html" if target.path == "/" else "ap.html" if target.path == "/ap" else target.path[1:]
                    resource = files("ba_automator").joinpath("web", name)
                    if not resource.is_file():
                        raise ApiError(404, "Dashboard asset not found")
                    types = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                             "style.css": "text/css; charset=utf-8", "maid-arisu.png": "image/png",
                             "maid-arisu-checklist.png": "image/png", "maid-arisu-tea.png": "image/png",
                             "maid-arisu-loot.png": "image/png",
                             "ap.html":"text/html; charset=utf-8", "ap.js":"text/javascript; charset=utf-8", "ap.css":"text/css; charset=utf-8"}
                    self._send(200, resource.read_bytes(), types[name])
                else:
                    raise ApiError(404, "Not found")
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except Exception:
                LOGGER.exception("Dashboard GET failed")
                self._json(500, {"error": "The dashboard could not complete this request"})

        def do_POST(self):
            try:
                self._check_request(mutation=True)
                target = urlsplit(self.path)
                if target.query:
                    raise ApiError(400, "Mutation requests do not accept query parameters")
                body = self._body()
                expected = {"/api/run": {"task"}, "/api/cancel": {"id"}, "/api/pause": set(),
                            "/api/run-available": set(),
                            "/api/dismiss-failure": {"id"}, "/api/clear-loot": set(),
                            "/api/resume": set(), "/api/stop": set(), "/api/capture": set(),
                            "/api/settings": SETTINGS}
                if target.path not in expected:
                    raise ApiError(404, "Not found")
                if body.keys() - expected[target.path]:
                    raise ApiError(400, "Unexpected request fields")
                result = {"ok": True}
                if target.path == "/api/run":
                    result = {"job": controller.enqueue(body.get("task"))}
                elif target.path == "/api/run-available":
                    result = controller.run_available()
                elif target.path == "/api/cancel":
                    if not isinstance(body.get("id"), str):
                        raise ApiError(400, "A queued job id is required")
                    controller.cancel(body["id"])
                elif target.path == "/api/pause":
                    controller.pause()
                elif target.path == "/api/dismiss-failure":
                    controller.dismiss_failure(body.get('id'))
                elif target.path == "/api/resume":
                    controller.resume()
                elif target.path == "/api/stop":
                    controller.stop()
                elif target.path == "/api/capture":
                    result = controller.capture()
                elif target.path == "/api/clear-loot":
                    result = controller.clear_loot()
                elif target.path == "/api/settings":
                    result = controller.update_settings(body)
                self._json(200, result)
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except (RuntimeError, OSError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                LOGGER.exception("Dashboard POST failed")
                self._json(500, {"error": "The dashboard could not complete this request"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    return Server((host, port), Handler)


def serve(config_path: Path, port: int = 8765, host: str = "127.0.0.1") -> None:
    """Serve the local dashboard until interrupted; pending queue entries are in-memory."""
    controller = DashboardController(config_path)
    server = None
    try:
        server = create_server(controller, host, port)
        display_host = f"[{host}]" if ":" in host else host
        LOGGER.info("Dashboard ready at http://%s:%s (local task queue)", display_host, server.server_port)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        LOGGER.info("Stopping the dashboard and its active task")
    finally:
        if server is not None:
            server.server_close()
        controller.close()
