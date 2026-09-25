"""Small shared contracts for bounded game tasks and their local evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .daily_log import infer_task, journal_event, reset_context, set_context

HOME_STABLE_SECONDS = 5.0
FRAME_MAX_AGE = 5.0
TRACE_LIMIT = 24


@dataclass(frozen=True)
class Capture:
    """Keep a screenshot with the time and foreground evidence that belong to it.

    ``captured_at`` is read before capture begins, not after OCR completes.
    Freshness alone does not authorize input: callers still verify ``foreground``
    and the recognized control before passing ``deadline`` to the device adapter.
    """

    png: bytes
    captured_at: float
    foreground: str | None

    @property
    def deadline(self) -> float:
        return self.captured_at + FRAME_MAX_AGE

    def is_fresh(self, now: float) -> bool:
        return self.captured_at <= now <= self.deadline


@dataclass(frozen=True)
class RunResult:
    status: str
    run_dir: Path
    duration: float
    actions: int


class TaskError(RuntimeError):
    """A task stopped safely; local diagnostics are available in run_dir."""

    def __init__(self, message: str, run_dir: Path):
        super().__init__(message)
        self.run_dir = run_dir


class Journal:
    """Persist task events and retain a bounded ring of diagnostic screenshots."""

    def __init__(
        self,
        directory: Path,
        monotonic: Callable[[], float],
        started: float,
        *,
        state_dir: Path | None = None,
    ):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.clock = monotonic
        self.started = started
        self.stream = (directory / "events.jsonl").open("a", encoding="utf-8")
        self.frame_count = 0
        self.state_dir = state_dir
        self.task = infer_task(directory.name)
        self.context_token = set_context(self.task, directory.name)

    def record(self, event: str, **fields) -> None:
        record = {
            "event_id": uuid4().hex,
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "elapsed": round(self.clock() - self.started, 3),
            **fields,
        }
        self.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        if fields.get("task"):
            self.task = fields["task"]
        journal_event(
            event,
            state_dir=self.state_dir,
            timestamp=record["time"],
            task=self.task,
            run=self.directory.name,
            level=(
                "ERROR"
                if fields.get("status") == "failed" or event == "error"
                else "INFO"
            ),
            record_id=f"journal:{record['event_id']}",
            **{
                key: value
                for key, value in record.items()
                if key not in {"event_id", "time", "event", "task"}
            },
        )

    def screenshot(self, png: bytes) -> str:
        # A fixed-size ring retains the latest frames without growing during waits.
        name = f"trace-{self.frame_count % TRACE_LIMIT:02d}.png"
        self.frame_count += 1
        self.save_image(name, png)
        return name

    def save_image(self, name: str, png: bytes) -> None:
        destination = self.directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_bytes(png)
        temporary.replace(destination)

    def close(self) -> None:
        self.stream.close()
        if self.context_token is not None:
            reset_context(self.context_token)
            self.context_token = None
