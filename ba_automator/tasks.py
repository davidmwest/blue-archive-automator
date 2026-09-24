"""The small set of supported jobs and their ordered task plans."""

from __future__ import annotations

from .config import Config


TASK_LABELS = {
    "restart": "Home screen ready",
    "cafe": "Cafe task complete",
    "lessons": "Lessons complete",
    "daily": "Daily tasks complete",
}
TASKS = frozenset(TASK_LABELS)
RUN_PREFIXES = ("restart-", "cafe-", "lessons-")


def task_plan(command: str, config: Config) -> tuple[str, ...]:
    """Every game job starts with restart; recurring cafe visits stay cafe-only."""
    if command == "daily":
        return ("restart", "cafe", "lessons") if config.lessons_enabled_in_daily else ("restart", "cafe")
    if command in {"cafe", "lessons"}:
        return ("restart", command)
    if command == "restart":
        return ("restart",)
    raise ValueError(f"Unknown job: {command}")
