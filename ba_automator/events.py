"""Researched event profiles and deterministic, read-only recognition helpers.

Profiles describe candidates and expected screens. They do not execute navigation,
approve popups, spend resources, or contact an AI service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Mapping, Sequence
from urllib.parse import urlparse

from .vision import Word, decode_frame


class EventProfileError(RuntimeError):
    pass


Region = tuple[int, int, int, int]
Point = tuple[int, int]


@dataclass(frozen=True)
class EventEntry:
    id: str
    screen: str
    region: Region
    tap: Point
    ocr_keywords: tuple[str, ...]
    template_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteCheck:
    id: str
    kind: str
    region: Region
    required_ocr: tuple[str, ...]
    any_ocr: tuple[str, ...] = ()
    dismiss_control: str | None = None


@dataclass(frozen=True)
class EventProfile:
    schema_version: int
    id: str
    title: str
    server: str
    entries: tuple[EventEntry, ...]
    route_checks: tuple[RouteCheck, ...]
    starts_at: datetime | None = None
    playable_until: datetime | None = None
    rewards_until: datetime | None = None
    sources: tuple[str, ...] = ()

    def phase(self, at: datetime | None = None) -> str:
        now = at if at is not None else datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise EventProfileError("Event availability requires a timezone-aware datetime")
        if self.starts_at is not None and now < self.starts_at:
            return "not_started"
        if self.playable_until is not None and now >= self.playable_until:
            if self.rewards_until is not None and now < self.rewards_until:
                return "rewards_only"
            return "closed"
        if self.rewards_until is not None and now >= self.rewards_until:
            return "closed"
        return "playable" if self.playable_until is not None else "unspecified"


@dataclass(frozen=True)
class EntryMatch:
    event_id: str
    entry_id: str
    screen: str
    target: Point
    evidence: str


def _object(value, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise EventProfileError(f"{label} must be an object")
    if value.keys() - allowed:
        raise EventProfileError(f"Unknown {label} fields: {', '.join(sorted(value.keys() - allowed))}")
    if required - value.keys():
        raise EventProfileError(f"Missing {label} fields: {', '.join(sorted(required - value.keys()))}")
    return value


def _text(value, label: str, *, identifier=False) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise EventProfileError(f"{label} must be nonempty text of at most 300 characters")
    if identifier and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise EventProfileError(f"{label} must be a lowercase identifier")
    return value.strip()


def _phrases(value, label: str, *, required=False) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 32 or (required and not value):
        raise EventProfileError(f"{label} must be a {'nonempty ' if required else ''}list of at most 32 phrases")
    result = tuple(_text(item, label) for item in value)
    if any(not _normalize(item) for item in result):
        raise EventProfileError(f"{label} must contain readable words")
    return result


def _region(value, label: str) -> Region:
    if (not isinstance(value, list) or len(value) != 4
            or any(type(number) is not int for number in value)):
        raise EventProfileError(f"{label} must contain four integer coordinates")
    x1, y1, x2, y2 = value
    if not (0 <= x1 < x2 <= 1280 and 0 <= y1 < y2 <= 720):
        raise EventProfileError(f"{label} must be inside the 1280×720 display")
    return x1, y1, x2, y2


def _contains(region: Region, point: Point) -> bool:
    return region[0] <= point[0] < region[2] and region[1] <= point[1] < region[3]


def _timestamp(value, label: str) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = datetime.fromisoformat(_text(value, label).replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventProfileError(f"{label} must be an ISO 8601 timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise EventProfileError(f"{label} must include a UTC offset or Z")
    return timestamp


def load_event_profile(path: str | Path) -> EventProfile:
    """Read schema version 1. Unknown keys and unguarded route steps are errors."""
    source = Path(path).expanduser().resolve()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventProfileError(f"Cannot read event profile {source}: {exc}") from exc
    document = _object(document, {"schema_version", "id", "title", "server", "availability", "entries",
                                  "route_checks", "sources"},
                       {"schema_version", "id", "title", "server", "entries", "route_checks"}, "profile")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise EventProfileError("Only event profile schema_version 1 is supported")
    if document["server"] != "global-en":
        raise EventProfileError("The supported event server is global-en")

    entries = []
    if not isinstance(document["entries"], list) or not 1 <= len(document["entries"]) <= 8:
        raise EventProfileError("entries must contain 1–8 entrance descriptions")
    for value in document["entries"]:
        entry = _object(value, {"id", "screen", "region", "tap", "ocr_keywords", "template_refs"},
                        {"id", "screen", "region", "tap", "ocr_keywords"}, "entry")
        if not isinstance(entry["screen"], str) or entry["screen"] not in {"home", "campaign"}:
            raise EventProfileError("entry.screen must be home or campaign")
        region = _region(entry["region"], "entry.region")
        tap = entry["tap"]
        if (not isinstance(tap, list) or len(tap) != 2 or any(type(n) is not int for n in tap)
                or not _contains(region, tuple(tap))):
            raise EventProfileError("entry.tap must be an integer point inside entry.region")
        refs = _phrases(entry.get("template_refs", []), "entry.template_refs")
        for ref in refs:
            template = Path(ref)
            if (template.is_absolute() or ".." in template.parts or "\\" in ref
                    or ":" in ref or template.suffix.lower() != ".png"):
                raise EventProfileError("Template references must be relative PNG paths without parent traversal")
            if not (source.parent / template).is_file():
                raise EventProfileError(f"Missing local event template: {ref}")
        entries.append(EventEntry(_text(entry["id"], "entry.id", identifier=True), entry["screen"],
                                  region, tuple(tap), _phrases(entry["ocr_keywords"], "entry.ocr_keywords",
                                                             required=True), refs))
    if len({entry.id for entry in entries}) != len(entries):
        raise EventProfileError("Entry identifiers must be unique")

    checks = []
    if not isinstance(document["route_checks"], list) or not 1 <= len(document["route_checks"]) <= 16:
        raise EventProfileError("route_checks must contain 1–16 recognized screens")
    for value in document["route_checks"]:
        check = _object(value, {"id", "kind", "region", "required_ocr", "any_ocr", "dismiss_control"},
                        {"id", "kind", "region", "required_ocr"}, "route check")
        if not isinstance(check["kind"], str) or check["kind"] not in {"optional_notice", "destination"}:
            raise EventProfileError("route check kind must be optional_notice or destination")
        control = check.get("dismiss_control")
        if control is not None and (not isinstance(control, str) or control not in {"confirm", "recognized_close"}):
            raise EventProfileError("Unsupported guarded notice control")
        if check["kind"] == "destination" and control is not None:
            raise EventProfileError("A destination check cannot specify a dismiss control")
        checks.append(RouteCheck(_text(check["id"], "route check id", identifier=True), check["kind"],
                                 _region(check["region"], "route check region"),
                                 _phrases(check["required_ocr"], "required_ocr", required=True),
                                 _phrases(check.get("any_ocr", []), "any_ocr"), control))
    if len({check.id for check in checks}) != len(checks):
        raise EventProfileError("Route check identifiers must be unique")
    if sum(check.kind == "destination" for check in checks) != 1:
        raise EventProfileError("A profile must have exactly one destination check")

    availability = _object(document.get("availability", {}),
                           {"starts_at", "playable_until", "rewards_until"}, set(), "availability")
    start, end, rewards = (_timestamp(availability.get(key), key)
                           for key in ("starts_at", "playable_until", "rewards_until"))
    if ((start is not None and end is not None and start >= end)
            or (end is not None and rewards is not None and end > rewards)
            or (start is not None and rewards is not None and start >= rewards)):
        raise EventProfileError("Availability timestamps must be in chronological order")
    sources = _phrases(document.get("sources", []), "sources")
    if any(urlparse(url).scheme != "https" or not urlparse(url).netloc for url in sources):
        raise EventProfileError("Event sources must be HTTPS URLs")
    return EventProfile(1, _text(document["id"], "profile.id", identifier=True),
                        _text(document["title"], "profile.title"), document["server"], tuple(entries),
                        tuple(checks), start, end, rewards, sources)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _region_text(words: Sequence[Word], region: Region) -> str:
    return " ".join(word.normalized for word in words
                    if word.confidence >= 0.65 and _contains(region, word.center))


def _has_phrase(text: str, phrase: str) -> bool:
    return f" {_normalize(phrase)} " in f" {text} "


def match_entries(profile: EventProfile, words: Sequence[Word], *, screen: str,
                  at: datetime | None = None,
                  template_hits: Mapping[str, Point] | None = None) -> tuple[EntryMatch, ...]:
    """Return candidates on a caller-verified screen, never perform an input.

    Template hits must come from the caller's local image matcher and use the
    profile's reference paths as keys. OCR keywords are all required within the
    banner region. Locked, upcoming, ended, or rewards-only entries are excluded.
    """
    if not isinstance(screen, str) or screen not in {"home", "campaign"}:
        return ()
    if profile.phase(at) not in {"playable", "unspecified"}:
        return ()
    hits = template_hits or {}
    matches = []
    for entry in profile.entries:
        if entry.screen != screen:
            continue
        text = _region_text(words, entry.region)
        if re.search(r"\blocked\b|\bnot yet available\b|\bstarts? in\b|\bcoming soon\b|"
                     r"\bevent (?:has )?ended\b|\brewards? (?:claim )?only\b", text):
            continue
        if all(_has_phrase(text, keyword) for keyword in entry.ocr_keywords):
            evidence = "ocr"
        elif any(ref in hits and _contains(entry.region, hits[ref]) for ref in entry.template_refs):
            evidence = "template"
        else:
            continue
        matches.append(EntryMatch(profile.id, entry.id, screen, entry.tap, evidence))
    return tuple(matches)


def inspect_entries(profile: EventProfile, png: bytes, vision, *, screen: str,
                    at: datetime | None = None) -> tuple[EntryMatch, ...]:
    """Classify banner text with StartupVision's local OCR; no emulator or network."""
    return match_entries(profile, vision.read(decode_frame(png)), screen=screen, at=at)


def match_route_check(profile: EventProfile, check_id: str, words: Sequence[Word]) -> bool:
    """Recognize guarded text evidence; a True result does not authorize a tap."""
    check = next((item for item in profile.route_checks if item.id == check_id), None)
    if check is None:
        raise EventProfileError(f"Unknown route check: {check_id}")
    text = _region_text(words, check.region)
    return (all(_has_phrase(text, phrase) for phrase in check.required_ocr)
            and (not check.any_ocr or any(_has_phrase(text, phrase) for phrase in check.any_ocr)))
