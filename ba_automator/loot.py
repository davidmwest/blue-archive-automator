"""A clearable view of confirmed rewards; clearing never deletes action history."""

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import os
from uuid import uuid4

from .gifts import is_gift

RECEIVED = {
    "loot_received",
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
    "total assault coin": "Total Assault Coin",
    "total assault coins": "Total Assault Coin",
    "advanced total assault coin": "Advanced Total Assault Coin",
    "advanced total assault coins": "Advanced Total Assault Coin",
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


def _events(data, seen=None):
    seen = set() if seen is None else seen
    for line in data.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", event["id"])
        ):
            continue
        if event["id"] in seen:
            continue
        seen.add(event["id"])
        if event.get("action") in RECEIVED:
            yield event


def _receipt_key(config, event):
    """Stable identity survives evidence pruning, but never trusts an outside path."""
    value = event.get("evidence")
    if not isinstance(value, str) or not value:
        return None
    try:
        path = Path(value)
        if path.suffix.lower() != ".png" or path.is_symlink():
            return None
        resolved = path.resolve()
        return resolved if resolved.is_relative_to(config.run_dir.resolve()) else None
    except (OSError, ValueError, RuntimeError):
        return None


def receipt_path(config, event):
    path = _receipt_key(config, event)
    try:
        return path if path is not None and path.is_file() else None
    except OSError:
        return None


def _name(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())[:160]
    return ALIASES.get(value.casefold(), value) if value else None


def _items(event):
    value = event.get("items")
    return value if isinstance(value, list) else []


def _quantity(value):
    return type(value) is int and value > 0


def quantities(event):
    counts = Counter()
    for item in _items(event):
        if (
            isinstance(item, dict)
            and (name := _name(item.get("name")))
            and _quantity(item.get("quantity"))
        ):
            counts[name] += item["quantity"]
    if event.get("action") == "earnings_collected":
        for key in ("ap", "credits"):
            qty = event.get(key)
            if _quantity(qty) and ALIASES[key] not in counts:
                counts[ALIASES[key]] = qty
    if event.get("action") == "mail_received":
        # Balance deltas corroborate receipt labels; AP can regenerate meanwhile.
        gains = event.get("balance_gains")
        gains = gains if isinstance(gains, dict) else {}
        for key in ("credits", "pyroxenes"):
            qty = gains.get(key, event.get(key))
            if _quantity(qty) and ALIASES[key] not in counts:
                counts[ALIASES[key]] = qty
    return counts


def _sidecar(path):
    if path is None:
        return None
    sidecar = path.with_suffix(".loot.json")
    try:
        if (
            sidecar.is_symlink()
            or not sidecar.is_file()
            or sidecar.stat().st_size >= 262144
        ):
            return None
        value = json.loads(sidecar.read_text(encoding="utf-8"))
        if (
            isinstance(value, dict)
            and type(value.get("version")) is int
            and value["version"] == 1
            and isinstance(value.get("items"), list)
        ):
            return value
    except (ValueError, OSError):
        pass
    return None


def _catalog_name(config, identifier):
    """Only exact icon bytes can inherit a locally learned tooltip name."""
    icon = icon_path(config, identifier)
    if icon is None:
        return None
    metadata = icon.with_suffix(".json")
    try:
        if (
            metadata.is_symlink()
            or not metadata.is_file()
            or metadata.stat().st_size > 4096
        ):
            return None
        if sha256(icon.read_bytes()).hexdigest() != identifier:
            return None
        value = json.loads(metadata.read_text(encoding="utf-8"))
        name = value.get("name") if isinstance(value, dict) else None
        if (
            not isinstance(name, str)
            or not 1 < len(name) <= 160
            or not name.isprintable()
            or not any(character.isalpha() for character in name)
            or re.match(r"^\s*(?:owned\s*:|[xX×]\s*\d)", name, re.I)
        ):
            return None
        return _name(name)
    except (ValueError, OSError):
        return None


def _catalog_items(config, source, cache):
    items = []
    for value in _items(source):
        if not isinstance(value, dict) or _name(value.get("name")):
            items.append(value)
            continue
        item = dict(value)
        identifier = item.get("icon_id")
        if isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}", identifier):
            if identifier not in cache:
                cache[identifier] = _catalog_name(config, identifier)
            item["name"] = cache[identifier]
        items.append(item)
    return {**source, "items": items}


def _receipt_event(config, events, path, catalog):
    """One receipt may have an early loot event and a later task postcondition.

    Sidecar cards supersede legacy labels, which may be truncated. Exact-name
    quantity fallbacks and verified currency fields remain usable. Never sum
    separate descriptions of the same receipt or resurrect it after a clear.
    """
    event = dict(events[0])
    fallback = {}
    icons = {}
    explicit = {}
    preferred = None
    for source in sorted(events, key=lambda e: e["action"] == "loot_received"):
        source = _catalog_items(config, source, catalog)
        fallback.update(quantities(source))
        for item in _items(source):
            if isinstance(item, dict) and (name := _name(item.get("name"))):
                icons[name] = item.get("icon_id")
        if _items(source):
            preferred = source
        # Keep fields that are independent of OCR's item-name recognition.
        explicit.update(quantities({**source, "items": []}))
    metadata = _sidecar(path)
    if metadata is not None:
        metadata = _catalog_items(config, metadata, catalog)
        preferred = metadata if metadata["items"] else preferred
        event["items_complete"] = metadata.get("items_complete") is True
    elif preferred is not None:
        event["items_complete"] = preferred.get("items_complete")
    elif any(source.get("items_complete") is False for source in events):
        event["items_complete"] = False
    items = []
    if preferred is not None:
        for value in _items(preferred):
            if not isinstance(value, dict):
                event["items_complete"] = False
                continue
            item = dict(value)
            name = _name(item.get("name"))
            item["name"] = name
            if name and not _quantity(item.get("quantity")) and name in fallback:
                item["quantity"] = fallback[name]
            if name and not item.get("icon_id"):
                item["icon_id"] = icons.get(name)
            items.append(item)
    else:
        items = [
            {"name": name, "quantity": quantity, "icon_id": icons.get(name)}
            for name, quantity in fallback.items()
        ]
    present = quantities({"items": items})
    # Static backfills may cover only one viewport of a historical receipt.
    # Preserve already verified drops until a complete inspection supersedes it.
    supplements = dict(fallback) if event.get("items_complete") is not True else {}
    supplements.update(explicit)
    for name, quantity in supplements.items():
        missing = quantity - present.get(name, 0)
        if missing > 0 and (
            name not in present or event.get("items_complete") is not True
        ):
            items.append(
                {"name": name, "quantity": missing, "icon_id": icons.get(name)}
            )
    event["items"] = items
    # A completion without OCR card metadata must retain its original review flag.
    event["_legacy_incomplete"] = event.get("items_complete") is not True and any(
        source["action"]
        in {"crafts_collected", "lesson_completed", "ap_spent", "tickets_spent"}
        for source in events
    )
    event["_cafe"] = any(source["action"] == "earnings_collected" for source in events)
    return event


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
    review_counts = {
        "missing_details": 0,
        "unidentified_items": 0,
        "unverified_coverage": 0,
    }
    icon_by_name = {}
    unresolved = []
    catalog = {}
    seen_ids = set()
    before = list(_events(data[: cursor["offset"]], seen_ids))
    seen_receipts = {_receipt_key(config, event) for event in before}
    seen_receipts.discard(None)
    receipts = {}
    for event in _events(data[cursor["offset"] :], seen_ids):
        key = _receipt_key(config, event)
        if key in seen_receipts:
            continue
        receipts.setdefault(key or event["id"], []).append(event)
    for events in receipts.values():
        path = receipt_path(config, events[0])
        event = _receipt_event(config, events, path, catalog)
        invalid = False
        for item in _items(event):
            name = _name(item.get("name"))
            icon = item.get("icon_id")
            if name and icon_path(config, icon):
                icon_by_name[name] = icon
            if not name or not _quantity(item.get("quantity")):
                invalid = True
                unresolved.append({**item, "receipt_id": event["id"]})
        counts = quantities(event)
        totals.update(counts)
        incomplete = (
            event.get("_legacy_incomplete", False)
            or event.get("items_complete") is False
            or invalid
            or not counts
        )
        if event.get("_cafe") and not {"AP", "Credits"} <= counts.keys():
            incomplete = True
        review_reason = None
        if incomplete:
            if invalid:
                review_reason = "unidentified_items"
            elif not counts:
                review_reason = "missing_details"
            else:
                review_reason = "unverified_coverage"
            review_counts[review_reason] += 1
        unknown += int(incomplete)
        rows.append(
            {
                "id": event["id"],
                "time": event.get("time"),
                "task": event.get("task"),
                "detail": event.get("detail", ""),
                "items": [{"name": n, "quantity": q} for n, q in counts.items()],
                "unidentified": incomplete,
                "review_reason": review_reason,
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
        "groups": grouped(config, totals, icon_by_name),
        "unresolved_items": group_unresolved(config, unresolved),
        "receipt_count": len(rows),
        "unidentified_receipts": unknown,
        "review_counts": review_counts,
        "receipts": list(reversed(rows))[:100],
        "older_receipts": max(0, len(rows) - 100),
    }


def image_path(config, identifier):
    for event in _events(_source(config)):
        if event["id"] == identifier:
            return receipt_path(config, event)
    return None


# Presentation priority, deliberately not an economic valuation. Alphabetical
# order inside each category is stable; tiered materials prefer higher tiers.
GROUPS = (
    ("premium", "the good stuff"),
    ("students", "students"),
    ("energy", "AP + tickets"),
    ("shop", "shop currencies"),
    ("growth", "leveling + skills"),
    ("crafting", "crafting + gifts"),
    ("credits", "credits"),
    ("other", "everything else"),
    ("equipment", "equipment"),
)

# Add exact artifact names after an observed tooltip identifies a skill material.
# A broad "fragment" rule would also catch unrelated event and crafting items.
ARTIFACTS = frozenset({"aether dust", "crystal haniwa fragment"})
SHOP_CURRENCIES = frozenset(
    {
        "expert permit",
        "expert permits",
        "tactical challenge coin",
        "tactical challenge coins",
        "total assault coin",
        "total assault coins",
        "advanced total assault coin",
        "advanced total assault coins",
    }
)


def category(name):
    # Named gifts can contain words such as "ticket" or "report"; the reviewed
    # catalog must take precedence over general material-name rules.
    if is_gift(name):
        return "crafting"
    n = " ".join(name.split()).casefold()
    if "pyroxene" in n or "recruitment" in n:
        return "premium"
    if "eleph" in n or "eligma" in n:
        return "students"
    if n == "ap" or "ticket" in n:
        return "energy"
    if n in SHOP_CURRENCIES:
        return "shop"
    if n in ARTIFACTS:
        return "growth"
    if any(
        x in n
        for x in (
            "report",
            "tech note",
            "blu-ray",
            "firing pin",
            "spring",
            "barrel",
            "enhancement stone",
            "location exp",
        )
    ):
        return "growth"
    if "blueprint" in n or "equipment" in n:
        return "equipment"
    if any(x in n for x in ("keystone", "gift", "furniture", "crafting")):
        return "crafting"
    if n in ("credits", "credit points"):
        return "credits"
    return "other"


def icon_path(config, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        return None
    root = config.state_dir / "loot-icons"
    path = root / (identifier + ".png")
    try:
        if root.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        if not path.resolve().is_relative_to(config.state_dir.resolve()):
            return None
        if path.stat().st_size > 131072:
            return None
        return path
    except (OSError, RuntimeError):
        return None


def icon_url(config, identifier):
    return f"/api/loot/icons/{identifier}" if icon_path(config, identifier) else None


def grouped(config, totals, icons):
    def order(item):
        name = item["name"].casefold()
        tier = next(
            (
                i
                for i, s in enumerate(
                    ("superior", "advanced", "normal", "novice", "beginner")
                )
                if s in name
            ),
            5,
        )
        return tier, name

    return [
        {
            "id": key,
            "label": label,
            "items": sorted(
                [
                    {
                        "name": n,
                        "quantity": q,
                        "icon_url": icon_url(config, icons.get(n)),
                    }
                    for n, q in totals.items()
                    if category(n) == key
                ],
                key=order,
            ),
        }
        for key, label in GROUPS
        if any(category(n) == key for n in totals)
    ]


def group_unresolved(config, items):
    grouped_items = {}
    for index, item in enumerate(items):
        icon = item.get("icon_id")
        valid_id = isinstance(icon, str) and re.fullmatch(r"[a-f0-9]{64}", icon)
        name = _name(item.get("name"))
        # A malformed/missing icon never merges unrelated unknown cards.
        key = (icon, name) if valid_id else (item["receipt_id"], index)
        row = grouped_items.setdefault(
            key,
            {
                "name": name or "Unidentified item",
                "quantity": 0,
                "icon_url": icon_url(config, icon),
                "receipt_url": f"/api/loot/{item['receipt_id']}/receipt",
            },
        )
        if _quantity(item.get("quantity")) and row["quantity"] is not None:
            row["quantity"] += item["quantity"]
        else:
            row["quantity"] = None
    return list(grouped_items.values())
