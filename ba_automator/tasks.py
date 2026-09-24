"""The small set of supported jobs and their ordered task plans."""

from __future__ import annotations

from .config import Config


TASK_LABELS = {
    "restart": "Home screen ready",
    "club": "Club check-in complete",
    "cafe": "Cafe task complete",
    "crafting": "Crafting checked; collection visits scheduled",
    "lessons": "Lessons complete",
    "daily": "Daily tasks complete",
}
TASKS = frozenset(TASK_LABELS)
RUN_PREFIXES = ("restart-", "club-", "cafe-", "crafting-", "lessons-")


def task_plan(command: str, config: Config) -> tuple[str, ...]:
    """Club has a reserved step after restart; its current stub sends no input."""
    if command == "daily":
        plan = ("restart", "club", "cafe")
        return (*plan, "lessons") if config.lessons_enabled_in_daily else plan
    if command == "cafe":
        return ("restart", "club", "cafe")
    if command == "club":
        # A placeholder does not need to launch the game. Add restart when live.
        return ("club",)
    if command in {"lessons", "crafting"}:
        return ("restart", command)
    if command == "restart":
        return ("restart",)
    raise ValueError(f"Unknown job: {command}")
