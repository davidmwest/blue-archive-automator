"""Loopback dashboard with an in-memory FIFO queue and one task subprocess.

Queued jobs do not survive server shutdown. Each dispatched job receives a private
configuration snapshot and run directory, so its frames cannot be confused with
earlier runs or an independently started CLI runner. Startup runs no manual job;
an explicitly enabled cafe schedule can enqueue work when due. Failed cafe jobs
retry after 15 minutes, up to three consecutive failures, then require Resume.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import ipaddress
import json
import logging
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
from .locking import InstanceLock
from .vision import decode_frame


LOGGER = logging.getLogger(__name__)
TASKS = {"restart", "daily", "cafe"}
RESTART_SETTINGS = {"auto_download", "poll_interval", "startup_timeout", "download_timeout", "unknown_timeout"}
CAFE_SETTINGS = {"cafe_schedule_enabled": "schedule_enabled", "cafe_invite_enabled": "invite_enabled",
                 "cafe_invite_student": "invite_student"}
AUTOMATION_SETTINGS = {"close_app_when_idle"}
SETTINGS = RESTART_SETTINGS | CAFE_SETTINGS.keys() | AUTOMATION_SETTINGS
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
        self._job_roots: dict[str, Path] = {}
        self._popup_cache: dict[Path, tuple[tuple[int, int], list[dict]]] = {}
        self._schedule = self._load_schedule()
        self._worker = threading.Thread(target=self._work, name="blue-archive-queue", daemon=True)
        self._worker.start()

    def _log(self, message: str, level: str = "info") -> None:
        with self._condition:
            self._logs.append({"time": _timestamp(), "message": message[:2000], "level": level})
        (LOGGER.error if level == "error" else LOGGER.info)("dashboard: %s", message[:2000])

    def _public_config(self) -> dict:
        values = {name: getattr(self.config, name) for name in ("serial", "package", *sorted(RESTART_SETTINGS))}
        values.update({name: getattr(self.config, name, "" if name.endswith("student") else False)
                       for name in CAFE_SETTINGS})
        values.update({name: getattr(self.config, name) for name in AUTOMATION_SETTINGS})
        return values

    def _state_dir(self) -> Path:
        return getattr(self.config, "state_dir", self.config.run_dir.parent / "state")

    def _load_schedule(self) -> dict:
        try:
            value = json.loads((self._state_dir() / "schedule.json").read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("cafe"), dict):
                return value
        except (OSError, ValueError):
            pass
        return {"cafe": {"last_success_at": None, "next_due_at": None,
                         "consecutive_failures": 0, "retry_paused": False}}

    def _save_schedule(self) -> None:
        try:
            _write_atomic(self._state_dir() / "schedule.json", json.dumps(self._schedule, indent=2) + "\n")
        except OSError as exc:
            self._log(f"Could not persist cafe schedule: {exc}", "error")

    def _enqueue_scheduled(self) -> None:
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
            raise ApiError(400, "Task must be restart, cafe, or daily")
        with self._condition:
            if self._shutdown:
                raise ApiError(409, "The server is shutting down")
            if len(self._queue) >= 100:
                raise ApiError(409, "The queue already contains 100 jobs")
            job = {"id": uuid4().hex, "task": task, "created_at": _timestamp()}
            self._queue.append(job)
            self._condition.notify_all()
            return dict(job)

    def cancel(self, job_id: str) -> None:
        with self._condition:
            if self._current and self._current["id"] == job_id:
                raise ApiError(409, "This job is already running; use Stop to interrupt it")
            for job in self._queue:
                if job["id"] == job_id:
                    self._queue.remove(job)
                    if job.get("source") == "schedule" and job["task"] == "cafe":
                        # Cancel skips this occurrence; leaving it overdue would
                        # silently enqueue the same cafe visit on the next tick.
                        now = self._wall_clock()
                        self._schedule["cafe"].update({
                            "last_skipped_at": now.isoformat(),
                            "next_due_at": (now + CAFE_INTERVAL).isoformat(),
                        })
                        self._save_schedule()
                    self._history.appendleft({"id": job_id, "task": job["task"],
                                              "state": "stopped", "completed_at": _timestamp()})
                    return
            raise ApiError(404, "Queued job not found")

    def pause(self) -> None:
        with self._condition:
            self._paused = True

    def resume(self) -> None:
        with self._condition:
            if self._shutdown:
                raise ApiError(409, "The server is shutting down")
            self._paused = False
            if self._schedule["cafe"].get("retry_paused", False):
                self._schedule["cafe"].update({"retry_paused": False, "consecutive_failures": 0,
                                                "next_due_at": self._wall_clock().isoformat()})
                self._save_schedule()
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._paused = True
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
                self._started_at = _timestamp()
                self._current = {"id": job["id"], "task": job["task"], "started_at": self._started_at}
                self._task = job["task"]
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
            exit_code = 1
            output = ""
            process = None
            try:
                snapshot = job_root / "config.toml"
                _write_atomic(snapshot, _toml(_config_document(selected)))
                arguments = [sys.executable, "-m", "ba_automator", "--config", str(snapshot), job["task"]]
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
                            self._log(message, "error" if message.startswith(("Error:", "Traceback")) else "info")
                            marker = next((prefix for prefix in ("restart:", "cafe:") if prefix in message), None)
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
                    self._result = self._parse_result(output, run_root)
                    if self._stop_requested or self._shutdown:
                        self._state, self._phase = "stopped", "Stopped"
                    elif exit_code == 0 and self._result and self._result.get("status") == "success":
                        self._state = "success"
                        self._phase = {"restart": "Home screen ready", "cafe": "Cafe task complete",
                                       "daily": "Daily tasks complete"}[job["task"]]
                    else:
                        self._state, self._phase = "failed", "Task failed; check the log"
                    self._history.appendleft({"id": job["id"], "task": job["task"],
                                              "state": self._state, "completed_at": self._completed_at})
                    self._record_schedule(job["task"], self._state)
                    self._current = None
                    self._process = None
                    # Materialize any due scheduled job before deciding the queue is done.
                    self._enqueue_scheduled()
                    self._close_app_after_queue(selected, process)
                    self._condition.notify_all()

    def _close_app_after_queue(self, selected: Config, process) -> None:
        """Called once per completed job, under the dispatch/enqueue condition lock.

        Holding that lock through force-stop prevents a newly queued task from starting
        between the empty-queue check and game closure. The instance lock also excludes
        a separately launched CLI runner. No idle timer repeats this action.
        """
        if (not selected.close_app_when_idle or self._queue or self._current is not None
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
    def _parse_result(output: str, run_root: Path) -> dict | None:
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
                if not run.name.startswith(("restart-", "cafe-")) or not run.is_dir() or run.is_symlink():
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
                    "app_closed": self._app_closed,
                    "schedule": {"cafe": {**self._schedule["cafe"],
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
                              ("id", "time", "task", "action", "detail", "student", "cafe", "status")}
                    entries.append(public)
        except FileNotFoundError:
            pass
        return {"actions": list(reversed(entries))}

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
                if not run.name.startswith(("restart-", "cafe-")) or not run.is_dir() or run.is_symlink():
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
                replace(existing, **changes)  # Validate values before touching the file.
                with self.config_path.open("rb") as stream:
                    document = tomllib.load(stream)
                for name, value in changes.items():
                    section = ("cafe" if name in CAFE_SETTINGS else
                               "automation" if name in AUTOMATION_SETTINGS else "restart")
                    key = CAFE_SETTINGS.get(name, name)
                    document.setdefault(section, {})[key] = value
                _write_atomic(self.config_path, _toml(document))
                self.config = Config.from_file(self.config_path)
                self._condition.notify_all()
            except (ConfigError, ValueError, TypeError) as exc:
                raise ApiError(400, str(exc)) from exc
            return {"config": self._public_config()}

    def close(self) -> None:
        with self._condition:
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

        def _send(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
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
                elif target.path == "/api/actions":
                    self._json(200, controller.actions())
                elif match := re.fullmatch(r"/api/popups/([a-f0-9]{32})/(before|after)", target.path):
                    if parse_qs(target.query).keys() - {"v"}:
                        raise ApiError(400, "Popup images accept only a version parameter")
                    self._send(200, controller.popup_image(*match.groups()), "image/png")
                elif target.path in {"/", "/index.html", "/app.js", "/style.css"}:
                    name = "index.html" if target.path == "/" else target.path[1:]
                    resource = files("ba_automator").joinpath("web", name)
                    if not resource.is_file():
                        raise ApiError(404, "Dashboard asset not found")
                    types = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                             "style.css": "text/css; charset=utf-8"}
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
                            "/api/resume": set(), "/api/stop": set(), "/api/capture": set(),
                            "/api/settings": SETTINGS}
                if target.path not in expected:
                    raise ApiError(404, "Not found")
                if body.keys() - expected[target.path]:
                    raise ApiError(400, "Unexpected request fields")
                result = {"ok": True}
                if target.path == "/api/run":
                    result = {"job": controller.enqueue(body.get("task"))}
                elif target.path == "/api/cancel":
                    if not isinstance(body.get("id"), str):
                        raise ApiError(400, "A queued job id is required")
                    controller.cancel(body["id"])
                elif target.path == "/api/pause":
                    controller.pause()
                elif target.path == "/api/resume":
                    controller.resume()
                elif target.path == "/api/stop":
                    controller.stop()
                elif target.path == "/api/capture":
                    result = controller.capture()
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
