"""Durable local-day activity logs shared by the daemon and its task processes.

The JSON journals remain the machine-readable source of evidence. These files are
an append-only, human-readable view, including runtime diagnostics and identified
loot. Raw OCR, image data, authentication, and payment-method data do not belong
in this view. Dates use the computer's local timezone, not the game's reset day.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import threading
import traceback
from uuid import uuid4

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_RUN = re.compile(r"^(?P<task>[a-z_]+)-(?P<stamp>\d{8}T\d{6})-[a-zA-Z0-9]+$", re.ASCII)
_SENSITIVE = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|credential|email|"
    r"account|payment_method|card_number|card_details|billing_address|"
    r"billing_details|billing_dump|raw|ocr|screenshot|image_data|screen_text)",
    re.I,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|passwd|secret|token|authorization|cookie|api[_ -]?key)\s*[:=]\s*(?:Bearer\s+)?[^\s,;]+"
)
_CARD_NUMBER = re.compile(r"(?<!\w)(?:\d[ -]?){12,18}\d(?!\w)")
_CONTEXT: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "daily_log_context", default=(None, None)
)
_LOCK = threading.RLock()


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def today() -> str:
    return local_now().date().isoformat()


def validate_day(day: str) -> str:
    if not isinstance(day, str) or not _DATE.fullmatch(day):
        raise ValueError("Use a valid YYYY-MM-DD log date")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError("Use a valid YYYY-MM-DD log date") from None
    return day


def _directory(state_dir: Path, *, create: bool = False) -> Path:
    directory = Path(state_dir) / "logs"
    if directory.is_symlink():
        raise ValueError("Daily log directory must not be a symbolic link")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _open(path: Path, flags: int) -> int:
    if path.is_symlink():
        raise ValueError("Daily log must not be a symbolic link")
    descriptor = os.open(
        path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("Daily log must be a regular file")
    return descriptor


@contextmanager
def _locked(directory: Path):
    """Serialize complete lines across threads and independent CLI processes."""
    with _LOCK:
        descriptor = _open(directory / ".append.lock", os.O_RDWR | os.O_CREAT)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            acquired = True
            yield
        finally:
            if acquired and os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            elif acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _safe_text(value: str) -> str:
    value = _EMAIL.sub("[redacted email]", value)
    value = _SECRET_VALUE.sub(lambda match: match[1] + "=[redacted]", value)
    value = _CARD_NUMBER.sub("[redacted number]", value)
    # Keep one event per line, including errors containing terminal controls.
    return "".join(
        c if ord(c) >= 32 and ord(c) != 127 else f"\\x{ord(c):02x}" for c in value
    )


def sanitize(value):
    """Keep structured game facts; omit raw screen and private account data."""
    if isinstance(value, dict):
        return {
            _safe_text(str(key)): sanitize(item)
            for key, item in value.items()
            if not _SENSITIVE.search(str(key))
            and str(key).lower() not in {"text", "words", "png", "image", "cvv", "cvc"}
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize(item) for item in value if not isinstance(item, (bytes, bytearray))
        ]
    if isinstance(value, (bytes, bytearray)):
        return "[binary omitted]"
    if isinstance(value, Path):
        return _safe_text(str(value))
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"[{type(value).__name__}]"


def _moment(timestamp: datetime | str | None) -> datetime:
    if timestamp is None:
        return local_now()
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if not isinstance(timestamp, datetime):
        raise ValueError("Daily log timestamps need an ISO date-time or datetime")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Daily log timestamps need a timezone")
    return timestamp.astimezone()


def _line(
    event: str,
    *,
    timestamp=None,
    task=None,
    run=None,
    level="INFO",
    record_id=None,
    **fields,
) -> tuple[str, str]:
    when = _moment(timestamp)
    task, run = task or "daemon", run or "-"
    detail = fields.pop("detail", None)
    pieces = [
        f"{when.isoformat(timespec='milliseconds')} {_safe_text(str(level)).upper():7} "
        f"[{_safe_text(str(task))}/{_safe_text(str(run))}] {_safe_text(str(event))}"
    ]
    if detail:
        pieces.append(_safe_text(str(detail)))
    cleaned = sanitize(
        {key: value for key, value in fields.items() if value is not None}
    )
    if cleaned:
        pieces.append(
            " ".join(
                f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
                for key, value in cleaned.items()
            )
        )
    if record_id:
        pieces.append(f"[record={_safe_text(str(record_id))}]")
    return when.date().isoformat(), " | ".join(pieces) + "\n"


def append_event(
    state_dir: Path,
    event: str,
    *,
    timestamp=None,
    task=None,
    run=None,
    level="INFO",
    record_id=None,
    **fields,
) -> None:
    day, line = _line(
        event,
        timestamp=timestamp,
        task=task,
        run=run,
        level=level,
        record_id=record_id,
        **fields,
    )
    directory = _directory(state_dir, create=True)
    with _locked(directory):
        descriptor = _open(
            directory / f"{day}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND
        )
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())


def list_days(state_dir: Path) -> list[str]:
    directory = _directory(state_dir)
    result = []
    for path in directory.glob("*.log"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            result.append(validate_day(path.stem))
        except ValueError:
            continue
    return sorted(result, reverse=True)


def _read_unlocked(path: Path) -> str:
    try:
        descriptor = _open(path, os.O_RDONLY)
    except FileNotFoundError:
        raise FileNotFoundError("No daily log for that date") from None
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        return stream.read()


def read_day(state_dir: Path, day: str) -> str:
    day = validate_day(day)
    directory = _directory(state_dir)
    if not directory.is_dir():
        raise FileNotFoundError("No daily log for that date")
    # An HTTP read sees complete UTF-8 records and closes its handle before an
    # import can replace the file (required by Windows' file-sharing behavior).
    with _locked(directory):
        return _read_unlocked(directory / f"{day}.log")


def set_context(task: str | None, run: str | None):
    return _CONTEXT.set((task, run))


def reset_context(token) -> None:
    _CONTEXT.reset(token)


def infer_task(run: str) -> str | None:
    match = _RUN.fullmatch(run)
    return match["task"] if match else None


class DailyLogHandler(logging.Handler):
    def __init__(self, state_dir: Path):
        super().__init__(logging.INFO)
        self.state_dir = Path(state_dir).resolve()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "skip_daily", False) or not record.name.startswith(
            "ba_automator"
        ):
            return
        task, run = _CONTEXT.get()
        message = record.getMessage()
        stack = None
        if record.exc_info and record.exc_info[1]:
            message += f"; {type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            # File/function/line locate the fault without copying source lines,
            # local variables, screenshots, or a third-party billing dump.
            stack = [
                {
                    "file": Path(frame.filename).name,
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        try:
            append_event(
                self.state_dir,
                "diagnostic",
                timestamp=datetime.fromtimestamp(record.created, timezone.utc),
                task=task,
                run=run,
                level=record.levelname,
                detail=message,
                logger=record.name,
                stack=stack,
            )
        except Exception:
            # A diagnostics disk error must not recursively fail the CLI's error
            # reporting. Structured pre-input journals still fail closed.
            self.handleError(record)


def configure_daily_logging(state_dir: Path) -> DailyLogHandler:
    """Install once per process; subprocesses call this with their saved config."""
    root = logging.getLogger()
    selected = Path(state_dir).resolve()
    for handler in root.handlers[:]:
        if isinstance(handler, DailyLogHandler):
            if handler.state_dir == selected:
                return handler
            root.removeHandler(handler)
            handler.close()
    _directory(selected, create=True)
    handler = DailyLogHandler(selected)
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return handler


def close_daily_logging(handler: DailyLogHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()


def journal_event(event: str, *, state_dir=None, **fields) -> None:
    """Mirror a journal without sending verbose per-input records to stdout."""
    destinations = (
        {Path(state_dir).resolve()}
        if state_dir is not None
        else {
            handler.state_dir
            for handler in logging.getLogger().handlers
            if isinstance(handler, DailyLogHandler)
        }
    )
    for destination in destinations:
        append_event(destination, event, **fields)


def _records(path: Path):
    if path.is_symlink() or not path.is_file():
        return
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            # A concurrent writer may have emitted valid JSON before its final
            # newline. Wait for that record to commit so its import identity is
            # stable and a partial last write cannot enter the daily history.
            if not line.endswith("\n"):
                continue
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(record, dict):
                yield number, line, record


def backfill_history(state_dir: Path, run_dir: Path, *, day: str | None = None) -> int:
    """Import a local day's old journals/actions once, retaining source evidence.

    Old journals only have monotonic elapsed time and UTC names. Their reconstructed
    timestamps are explicitly marked estimated. New journal/action IDs match live
    daily records, so re-running the importer never duplicates those events either.
    """
    selected_day = validate_day(day or today())
    imported = []
    for _, raw, record in _records(Path(state_dir) / "important-actions.jsonl"):
        try:
            when = _moment(record.get("time"))
            if not record.get("time") or when.date().isoformat() != selected_day:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        fields = {
            key: value
            for key, value in record.items()
            if key not in {"id", "time", "task", "action"}
        }
        source = (
            f"action:{record['id']}"
            if record.get("id")
            else "legacy:" + hashlib.sha256(raw.encode()).hexdigest()
        )
        imported.append(
            _line(
                "important_action",
                timestamp=when,
                task=record.get("task"),
                action=record.get("action"),
                record_id=source,
                **fields,
            )[1]
        )
    for path in sorted(Path(run_dir).rglob("events.jsonl")):
        match = _RUN.fullmatch(path.parent.name)
        if not match or path.is_symlink():
            continue
        try:
            started = datetime.strptime(match["stamp"], "%Y%m%dT%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        for number, raw, record in _records(path):
            try:
                estimated = "time" not in record
                if not estimated and not isinstance(record["time"], str):
                    raise ValueError("Invalid historical timestamp")
                when = (
                    _moment(
                        started + timedelta(seconds=float(record.get("elapsed", 0)))
                    )
                    if estimated
                    else _moment(record["time"])
                )
                if when.date().isoformat() != selected_day:
                    continue
            except (ValueError, TypeError, OverflowError):
                continue
            fields = {
                key: value
                for key, value in record.items()
                if key not in {"event_id", "time", "task", "event"}
            }
            source = (
                f"journal:{record['event_id']}"
                if record.get("event_id")
                else "legacy:"
                + hashlib.sha256(
                    f"{path.resolve()}:{number}:{raw}".encode()
                ).hexdigest()
            )
            imported.append(
                _line(
                    record.get("event", "journal"),
                    timestamp=when,
                    task=record.get("task") or match["task"],
                    run=path.parent.name,
                    level="ERROR" if record.get("status") == "failed" else "INFO",
                    record_id=source,
                    timestamp_estimated=True if estimated else None,
                    **fields,
                )[1]
            )
    if not imported:
        return 0
    directory = _directory(state_dir, create=True)
    with _locked(directory):
        try:
            existing = _read_unlocked(directory / f"{selected_day}.log")
        except FileNotFoundError:
            existing = ""
        known = set(re.findall(r"\[record=([^\]\r\n]+)\]", existing))
        additions = []
        for line in imported:
            source = re.search(r"\[record=([^\]\r\n]+)\]", line)[1]
            if source not in known:
                known.add(source)
                additions.append(line)
        if not additions:
            return 0
        # The migration may be run after today's daemon has already logged events.
        # Merge under the same cross-process lock, preserving chronological display.
        lines = existing.splitlines(keepends=True) + additions

        # Compare instants, not displayed wall time: the local hour repeats at
        # the autumn DST change with a different UTC offset.
        def chronology(line):
            try:
                return datetime.fromisoformat(line.split(" ", 1)[0]).astimezone(
                    timezone.utc
                )
            except ValueError:
                return datetime.max.replace(tzinfo=timezone.utc)

        lines.sort(key=chronology)
        path = directory / f"{selected_day}.log"
        if path.is_symlink():
            raise ValueError("Daily log must not be a symbolic link")
        temporary = directory / f".{selected_day}.{uuid4().hex}.tmp"
        descriptor = _open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.writelines(lines)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return len(additions)
