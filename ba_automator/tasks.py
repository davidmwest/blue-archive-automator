"""The small set of supported jobs and their ordered task plans."""

from __future__ import annotations

from .config import Config


TASK_LABELS = {
    "tactical_rewards": "Tactical Challenge rewards collected",
    "red_dots": "Home notifications checked",
    "free_pack": "Free package checked and mail collected",
    "tasks": "Task rewards collected",
    "bounties": "Bounty tickets swept",
    "scrimmages": "Scrimmage tickets swept",
    "restart": "Home screen ready",
    "club": "Club check-in complete",
    "cafe": "Cafe task complete",
    "crafting": "Crafting checked; collection visits scheduled",
    "packs": "Packs checked and mail collected",
    "mail": "Mail collected; rewards logged",
    "spend_ap": "AP spending complete",
    "scan_ap": "Three-star stages scanned",
    "lessons": "Lessons complete",
    "daily": "Daily tasks complete",
}
TASKS = frozenset(TASK_LABELS)
RUN_PREFIXES = ("tactical_rewards-", "red_dots-", "free_pack-", "tasks-", "bounties-", "scrimmages-", "restart-", "club-", "cafe-", "crafting-", "lessons-", "packs-", "mail-", "spend_ap-", "scan_ap-")


def _task_plan(command: str, config: Config) -> tuple[str, ...]:
    """Producer jobs collect mail after their game actions."""
    if command == "daily":
        from .packs_state import enabled
        plan = ("restart", "club", "free_pack", *(("packs",) if enabled(config) else ()), "mail", "cafe")
        plan = (*plan, *(("bounties",) if config.bounties_enabled_in_daily else ()),
                *(("scrimmages",) if config.scrimmages_enabled_in_daily else ()))
        plan = (*plan, "tactical_rewards")
        plan = (*plan, "lessons") if config.lessons_enabled_in_daily else plan
        plan = (*plan, "spend_ap") if config.ap_schedule_enabled else plan
        return (*plan, "tasks")
    if command == "cafe":
        return ("restart", "club", "mail", "cafe", *(("spend_ap",) if config.ap_schedule_enabled else ()))
    if command in {"club", "free_pack"}:
        return ("restart", command, "mail")
    if command == "red_dots":
        return ("restart",)
    if command == "packs":
        return ("restart", "packs", "mail", *(("spend_ap",) if config.ap_schedule_enabled else ()))
    if command == 'mail':
        return ('restart', 'mail', *(("spend_ap",) if config.ap_schedule_enabled else ()))
    if command in {"tactical_rewards", "tasks", "bounties", "scrimmages", "lessons", "crafting", "spend_ap", "scan_ap"}:
        return ("restart", command)
    if command == "restart":
        return ("restart",)
    raise ValueError(f"Unknown job: {command}")


def task_plan(command: str, config: Config) -> tuple[str, ...]:
    """Check badges once after successful game work, before closing an idle app."""
    return (*_task_plan(command, config), "red_dots")
