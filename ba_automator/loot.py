"""A clearable view of confirmed rewards; clearing never deletes action history."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import os
from uuid import uuid4

RECEIVED = {
    "tactical_rewards_received",
    "free_pack_received",
    "task_rewards_received",
    "earnings_collected",
    "mail_received",
    "crafts_collected",
    "lesson_completed",
    "ap_spent",
    "tickets_spent",
}
ALIASES = {
    "ap": "AP",
    "credits": "Credits",
    "credit points": "Credits",
    "pyroxene": "Pyroxenes",
    "pyroxenes": "Pyroxenes",
}


def _source(config):
    path = config.state_dir / "important-actions.jsonl"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = b""
    # Ignore an append in progress. A clear cursor always ends at a full record.
    return data[: data.rfind(b"\n") + 1]


def _cursor(config):
    path = config.state_dir / "loot-view.json"
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {"offset": 0, "cleared_at": None}
    if (
        not isinstance(value, dict)
        or type(value.get("offset")) is not int
        or value["offset"] < 0
    ):
        raise RuntimeError("Invalid loot view cursor; action history is intact")
    return value


def clear(config):
    data = _source(config)
    value = {"offset": len(data), "cleared_at": datetime.now(timezone.utc).isoformat()}
    path = config.state_dir / "loot-view.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return value


def _events(data):
    seen = set()
    for line in data.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not re.fullmatch(
            r"[a-f0-9]{32}", str(event.get("id", ""))
        ):
            continue
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        if event.get("action") in RECEIVED:
            yield event


def receipt_path(config, event):
    value = event.get("evidence")
    if not isinstance(value, str):
        return None
    path = Path(value)
    root = config.run_dir.resolve()
    # The browser never supplies a filesystem path. Only a confirmed action's
    # own PNG beneath the configured run root can be served.
    if path.suffix.lower() != ".png" or path.is_symlink() or not path.is_file():
        return None
    if not path.resolve().is_relative_to(root):
        return None
    return path.resolve()


def quantities(event):
    counts = Counter()
    for item in event.get("items") or []:
        if not isinstance(item, dict):
            continue
        name, qty = item.get("name"), item.get("quantity")
        if (
            not isinstance(name, str)
            or not name.strip()
            or type(qty) is not int
            or qty <= 0
        ):
            continue
        name = " ".join(name.split())[:160]
        counts[ALIASES.get(name.lower(), name)] += qty
    if event["action"] == "earnings_collected":
        for key in ("ap", "credits"):
            qty = event.get(key)
            if type(qty) is int and qty > 0:
                counts[ALIASES[key]] = qty
    if event["action"] == "mail_received":
        # Prefer receipt labels; balance deltas are corroboration, not a second drop.
        gains = event.get("balance_gains") or {}
        for key in ("credits", "pyroxenes"):
            qty = gains.get(key, event.get(key))
            if type(qty) is int and qty > 0 and ALIASES[key] not in counts:
                counts[ALIASES[key]] = qty
        # AP can regenerate while opening mail; only its receipt label is counted.
    return counts


def snapshot(config):
    data = _source(config)
    cursor = _cursor(config)
    if cursor["offset"] > len(data):
        raise RuntimeError(
            "Action history changed beneath the loot cursor; clear the view to reset it"
        )
    totals = Counter()
    rows = []
    unknown = 0
    for event in _events(data[cursor["offset"] :]):
        counts = quantities(event)
        totals.update(counts)
        incomplete = (
            event["action"]
            in {"crafts_collected", "lesson_completed", "ap_spent", "tickets_spent"}
            or event.get("items_complete") is False
            or not counts
        )
        if (
            event["action"] == "earnings_collected"
            and not {"AP", "Credits"} <= counts.keys()
        ):
            incomplete = True
        unknown += int(incomplete)
        rows.append(
            {
                "id": event["id"],
                "time": event.get("time"),
                "task": event.get("task"),
                "detail": event.get("detail", ""),
                "items": [{"name": n, "quantity": q} for n, q in counts.items()],
                "unidentified": incomplete,
                "receipt_url": (
                    f'/api/loot/{event["id"]}/receipt'
                    if receipt_path(config, event)
                    else None
                ),
            }
        )
    return {
        "cleared_at": cursor["cleared_at"],
        "items": [{"name": n, "quantity": q} for n, q in sorted(totals.items())],
        "receipt_count": len(rows),
        "unidentified_receipts": unknown,
        "receipts": list(reversed(rows))[:100],
        "older_receipts": max(0, len(rows) - 100),
    }


def image_path(config, identifier):
    for event in _events(_source(config)):
        if event["id"] == identifier:
            return receipt_path(config, event)
    return None
