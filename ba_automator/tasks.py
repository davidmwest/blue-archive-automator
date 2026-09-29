"""The small set of supported jobs and their ordered task plans."""

from __future__ import annotations

from .config import Config


TASK_LABELS = {
    "joint_firing_drill": "Joint Firing Drill checked",
    "total_assault": "Total Assault complete",
    "assault_rewards": "Total Assault rank and points rewards checked",
    "tactical_battles": "Tactical Challenge battles complete",
    "tactical_rewards": "Tactical Challenge rewards collected",
    "red_dots": "Home and Campaign notifications checked",
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
RUN_PREFIXES = ("joint_firing_drill-", "total_assault-", "assault_rewards-", "tactical_rewards-", "tactical_battles-", "red_dots-", "free_pack-", "tasks-", "bounties-", "scrimmages-", "restart-", "club-", "cafe-", "crafting-", "lessons-", "packs-", "mail-", "spend_ap-", "scan_ap-")


def _task_plan(command: str, config: Config) -> tuple[str, ...]:
    """Producer jobs collect mail after their game actions."""
    if command == "daily":
        from .packs_state import enabled
        plan = ("restart", "club", "free_pack", *(("packs",) if enabled(config) else ()), "mail", "cafe")
        plan = (*plan, *(("bounties",) if config.bounties_enabled_in_daily else ()),
                *(("scrimmages",) if config.scrimmages_enabled_in_daily else ()))
        plan = (*plan, "tactical_battles") if config.tactical_battles_enabled_in_daily else plan
        plan = (*plan, "tactical_rewards")
        plan = (*plan, "lessons") if config.lessons_enabled_in_daily else plan
        plan = (*plan, "total_assault") if config.total_assault_enabled_in_daily else plan
        plan = (*plan, "joint_firing_drill") if config.drill_enabled_in_daily else plan
        plan = (*plan, "assault_rewards")
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
    if command == "tactical_battles":
        from .tactical_state import TacticalStateError, read_state

        # A result still on screen must reach the battle runner before any
        # restart can erase it. Recovery verifies the pending intent again
        # under the instance lock before deciding whether input is safe.
        try:
            if read_state(config)["pending"] is not None:
                return ("tactical_battles", "tactical_rewards")
        except TacticalStateError:
            # Plans also appear in dashboard previews. Keep those readable;
            # Restart's guarded execution rejects corrupt state without input.
            pass
        return ("restart", "tactical_battles", "tactical_rewards")
    if command == "joint_firing_drill":
        from .drill_state import DrillStateError, read_state
        try:
            if read_state(config)["pending"]:
                return (command,)
        except DrillStateError:
            pass
        return ("restart", command)
    if command == "total_assault":
        return ("restart", "total_assault", "assault_rewards")
    if command in {"assault_rewards", "tactical_rewards", "tasks", "bounties", "scrimmages", "lessons", "crafting", "spend_ap", "scan_ap"}:
        return ("restart", command)
    if command == "restart":
        return ("restart",)
    raise ValueError(f"Unknown job: {command}")


def task_plan(command: str, config: Config) -> tuple[str, ...]:
    """Check badges once after successful game work, before closing an idle app."""
    plan = _task_plan(command, config)
    if command == 'daily':
        from .drill_state import DrillStateError, read_state
        try:
            if read_state(config)['pending']:
                # Preserve the open receipt/battle before daily startup closes
                # the game, even if daily Drill was disabled after entry.
                plan = ('joint_firing_drill', *(task for task in plan if task != 'joint_firing_drill'))
        except DrillStateError:
            pass  # Restart's state guard prevents input on corrupt state.
    return (*plan, "red_dots")
