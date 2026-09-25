"""Deterministic Cafe invitation parsing and relationship-cap selection.

The caller must verify that the list is sorted by descending Relationship Rank.
Names and ranks are associated with visible row controls, never used as tap
coordinates themselves. No saved base rarity or character catalogue is enough
to establish a student's *current* relationship cap.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Literal, Mapping, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from .vision import Word


class InvitationRow(TypedDict):
    name: str
    identity: str
    rank: int | None
    enabled: bool
    target: tuple[int, int]


@dataclass(frozen=True)
class InvitationChoice:
    status: Literal["selected", "need_rarity", "unreadable", "none"]
    row: InvitationRow | None = None
    reason: str = ""


def normalized_identity(name: str) -> str:
    """Use the same exact-name normalization as the existing invitation flow."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def parse_invitation_rows(words: Iterable[Word]) -> list[InvitationRow]:
    """Read visible invitation rows from a 1280×720 English MomoTalk list.

    Both the normal and expanded-search list use the same columns. The rank is
    inside a heart below the name, approximately 20px below the row's Invite
    control. Wrapped variants can share that height, but occupy the name column
    to the right of the heart. Missing or conflicting rank readings stay unknown.
    Even a row whose name is unreadable is retained so it cannot silently lose
    priority to a lower-ranked student.
    """
    words = list(words)
    controls = sorted(
        (w for w in words if 745 <= w.center[0] <= 835
         and 194 <= w.center[1] <= 588
         and w.normalized in {"invite", "invited", "visiting", "in cafe"}),
        key=lambda w: w.center[1],
    )
    groups: list[list[Word]] = [[] for _ in controls]
    rank_groups: list[list[Word]] = [[] for _ in controls]
    for word in words:
        x, y = word.center
        if not (478 <= x <= 704 and 194 <= y <= 624):
            continue
        distances = [abs(y - control.center[1]) for control in controls]
        if not distances or min(distances) > 36 or distances.count(min(distances)) != 1:
            continue
        index = distances.index(min(distances))
        dy = y - controls[index].center[1]
        if 478 <= x <= 535 and 6 <= dy <= 36:
            rank_groups[index].append(word)
        elif (488 <= x <= 704 and word.normalized
              and not word.normalized.isdecimal()
              and not re.match(r"^(?:rank|relationship|lv|level|bond)(?: |$)", word.normalized)):
            groups[index].append(word)

    rows: list[InvitationRow] = []
    for control, parts, rank_parts in zip(controls, groups, rank_groups):
        parts.sort(key=lambda w: (w.center[1], w.center[0]))
        name = " ".join(w.text.strip() for w in parts)
        # A malformed/weak competing read makes the whole rank uncertain. This
        # also allows identical OCR reads from full-frame and enlarged crops.
        ranks = {
            int(w.text.strip()) if w.confidence >= .85
            and re.fullmatch(r"[0-9]{1,3}", w.text.strip())
            and 1 <= int(w.text.strip()) <= 100 else None
            for w in rank_parts
        }
        rank = next(iter(ranks)) if len(ranks) == 1 else None
        if control.confidence < .85 or any(w.confidence < .85 for w in parts):
            rank = None
        rows.append({"name": name, "identity": normalized_identity(name),
                     "rank": rank, "enabled": control.normalized == "invite",
                     "target": control.center})
    return rows


def relationship_cap(current_stars: int) -> int | None:
    """Current Global caps; the January 2026 update raised the 4★ cap to 30."""
    if isinstance(current_stars, bool) or not isinstance(current_stars, int):
        return None
    return {1: 10, 2: 10, 3: 20, 4: 30, 5: 100}.get(current_stars)


def choose_invitation(
    rows: Iterable[InvitationRow],
    stars_by_identity: Mapping[str, int] | None = None,
) -> InvitationChoice:
    """Choose the highest eligible row, or explain the evidence still needed.

    Rows must arrive in verified descending relationship order. A missing rank
    blocks selection rather than causing a lower student to win. Only the cap
    boundaries 10, 20, and 30 require a current-rarity lookup; 100 is always maxed.
    ``stars_by_identity`` keys use :func:`normalized_identity`.
    """
    stars_by_identity = stars_by_identity or {}
    for row in rows:
        if not row["enabled"]:
            continue
        rank = row["rank"]
        if (not row["identity"] or isinstance(rank, bool) or not isinstance(rank, int)
                or not 1 <= rank <= 100):
            return InvitationChoice("unreadable", row, "Student name or relationship rank is unreadable")
        if rank == 100:
            continue
        cap = relationship_cap(stars_by_identity.get(row["identity"]))
        if cap is not None:
            if rank > cap:
                return InvitationChoice("unreadable", row, "Relationship rank conflicts with current rarity")
            if rank == cap:
                continue
        elif rank in {10, 20, 30}:
            return InvitationChoice("need_rarity", row, "Current rarity is needed to verify this relationship cap")
        return InvitationChoice("selected", row, "Highest available student below their relationship cap")
    return InvitationChoice("none", reason="No available student below their relationship cap on this page")
