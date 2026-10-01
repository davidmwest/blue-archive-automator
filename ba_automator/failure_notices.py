"""Only actionable failures belong in the dashboard's attention panel.

The journal remains the complete failure record. Notification dismissal never
changes a transaction hold, schedule, or account state.
"""
from . import (ap_state, assault_state, crafting_state, drill_state, event_state,
               packs_state, tactical_state, ticket_state, treasure_state)

TASK_NAMES = {
    "spend_ap": "AP spending", "total_assault": "Total Assault", "crafting": "Crafting",
    "joint_firing_drill": "Joint Firing Drill", "clear_event": "event clearing",
    "packs": "Packs", "tactical_battles": "Tactical Challenge",
    "event_treasure": "event treasure", "bounties": "Bounty", "scrimmages": "Scrimmage",
    "cafe": "Cafe",
}


READERS = {
    "spend_ap": ap_state.read_state,
    "total_assault": assault_state.read_state,
    "crafting": crafting_state.read_state,
    "joint_firing_drill": drill_state.read_state,
    "clear_event": event_state.read_state,
    "packs": packs_state.read_state,
    "tactical_battles": tactical_state.read_state,
    "event_treasure": treasure_state.read_state,
    "bounties": lambda config: ticket_state.read_state(config, "bounties"),
    "scrimmages": lambda config: ticket_state.read_state(config, "scrimmages"),
}


def required_actions(config, task, detail, schedule):
    """Return concrete interventions, not a generic request to inspect a trace.

    Read the durable state after the runner and scheduler have finished. This
    catches holds even when the last error was just an OCR timeout, and lets a
    later successful recovery retire the corresponding notice automatically.
    """
    tasks = list(READERS) if task == "daily" else [task]
    if task == "spend_ap":
        tasks += ["clear_event", "event_treasure"]
    actions = []
    for name in tasks:
        reader = READERS.get(name)
        if reader is None:
            continue
        try:
            state = reader(config)
        except (OSError, ValueError, RuntimeError) as exc:
            actions.append(dict(task=name, reason="state", action=(
                f"Restore or repair the saved {TASK_NAMES.get(name, name)} state before retrying; "
                f"it could not be read: {exc}")))
            continue
        pending = state.get("pending") or state.get("pending_action")
        if pending:
            actions.append(dict(task=name, reason="state", action=(
                f"Check the saved {TASK_NAMES.get(name, name)} receipt against the game before retrying. "
                "A resource transaction is unresolved, so automatic spending is on hold.")))
        elif name == "crafting" and state.get("disabled_reason"):
            actions.append(dict(task=name, reason="state", action=(
                "Fix the Quick Craft preset in the game, then run Crafting from manual controls. "
                + state["disabled_reason"])))
        elif state.get("blocked_reason"):
            actions.append(dict(task=name, reason="state", action=(
                ("Check payment and purchase history in Google Play before manually retrying Packs. "
                 if name == "packs" else
                 f"Resolve the saved {TASK_NAMES.get(name, name)} hold before manually retrying. ")
                + state["blocked_reason"])))
    for name in ("cafe", "crafting"):
        if task in (name, "daily") and schedule.get(name, {}).get("retry_paused"):
            actions.append(dict(task=name, reason="schedule", action=(
                f"{TASK_NAMES[name]} stopped retrying after repeated failures. "
                "Check the game, then use Do everything to re-enable retries.")))
    lowered = detail.lower()
    # Explicit external prerequisites only. Words such as unknown, unreadable,
    # inspect, or failed do not themselves establish an action for the player.
    instructions = (
        (("configured blue archive package is not installed",),
         "Install Blue Archive in the selected emulator instance, or correct the package in Settings."),
        (("use the same adb executable as the azur lane daemon",),
         "Set the ADB executable to the one used by the other daemon; their shared server versions must match."),
        (("expected a 16:9 landscape game display",),
         "Set the emulator to a supported 16:9 landscape resolution from 1280×720 through 3840×2160, then retry."),
        (("sign in manually", "log in manually", "login required", "authentication required", "sign-in required", "account sign-in needs attention"),
         "Sign in to Blue Archive in the emulator, then use Do everything."),
        (("google play needs attention",),
         "Open Google Play in the emulator and complete payment setup or verification before retrying Packs."),
        (("could not save daily", "could not record daily", "saved schedule could not be updated"),
         "Check that the state directory is writable and the disk has free space, then retry Daily."),
        (("download requires approval because auto_download is disabled",),
         "Accept the game-data download in the emulator, or enable automatic downloads in Settings."),
    )
    for phrases, action in instructions:
        if any(phrase in lowered for phrase in phrases):
            actions.append(dict(task=task, reason="external", action=action))
    return actions
