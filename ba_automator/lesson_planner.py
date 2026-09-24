"""Pure decisions over observed Lessons screens; no device or account access.

The runner must inspect every unlocked location and room before the first call,
then supply a fresh snapshot after each confirmed ticket. This planner deliberately
does not predict rank-ups, invent ownership, or assume a completed room can repeat.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Literal


LessonStrategy = Literal["relationship", "school_rank"]


@dataclass(frozen=True)
class LessonStudent:
    """An observed portrait. A room-local slot ID suffices; names are optional."""

    id: str
    owned: bool | None
    bond: int | None = None


@dataclass(frozen=True)
class LessonLocation:
    id: str
    name: str
    rank: int | None = None
    xp: int | None = None
    xp_to_next: int | None = None
    capped: bool | None = False
    unlocked: bool | None = True


@dataclass(frozen=True)
class LessonRoom:
    location_id: str
    id: str
    name: str
    students: tuple[LessonStudent, ...]
    available: bool | None = True
    inspection_complete: bool = True


@dataclass(frozen=True)
class LessonDecision:
    status: Literal["selected", "blocked", "complete"]
    reason: str
    strategy: LessonStrategy
    location: LessonLocation | None = None
    room: LessonRoom | None = None
    owned_count: int | None = None
    student_count: int | None = None
    bond_total: int | None = None
    fallback: bool = False


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _owned_count(room: LessonRoom) -> int | None:
    if any(student.owned is None for student in room.students):
        return None
    return sum(student.owned is True for student in room.students)


def _bond_total(room: LessonRoom) -> int | None:
    if any(student.owned is None or (student.owned and student.bond is None)
           for student in room.students):
        return None
    return sum(student.bond for student in room.students if student.owned)


def choose_lesson(
    locations: Iterable[LessonLocation],
    rooms: Iterable[LessonRoom],
    *,
    strategy: LessonStrategy = "relationship",
    allowed_location_ids: Iterable[str] = (),
    inspection_complete: bool = True,
) -> LessonDecision:
    """Choose exactly one lesson, or explain why no ticket should be spent.

    ``relationship`` maximizes owned-student encounters, then the sum of their
    relationship ranks. ``school_rank`` balances the lowest location rank and
    fractional XP first, then maximizes total students and owned bond-rank sum.
    Equal scores use stable location/room names and IDs. Capped locations are
    excluded while an eligible uncapped location exists; if all are capped,
    relationship selection applies instead.

    Missing evidence blocks only comparisons that require it. Unknown ownership
    is never an unowned student, and unknown bond ranks are never zero. Unavailable
    rooms and locked or explicitly excluded locations do not affect selection.
    The allowlist contains exact observed IDs; an empty allowlist means all.
    """
    if strategy not in {"relationship", "school_rank"}:
        raise ValueError(f"Unknown lesson strategy: {strategy}")

    def blocked(reason: str) -> LessonDecision:
        return LessonDecision("blocked", reason, strategy)

    def complete(reason: str, *, fallback: bool = False) -> LessonDecision:
        return LessonDecision("complete", reason, strategy, fallback=fallback)

    if inspection_complete is not True:
        return blocked("The location and room inspection is incomplete; no ticket selected.")

    locations = tuple(locations)
    rooms = tuple(rooms)
    allowed = frozenset(allowed_location_ids)
    if any(not isinstance(item, str) or not item.strip() for item in allowed):
        raise ValueError("The location allowlist must contain nonempty location IDs")
    if any(not isinstance(location.id, str) or not location.id.strip() for location in locations):
        return blocked("A location is missing its identifier.")
    location_by_id = {location.id: location for location in locations}
    if len(location_by_id) != len(locations):
        return blocked("Duplicate location identifiers make this inspection ambiguous.")
    if missing := allowed - location_by_id.keys():
        return blocked(f"Configured locations were not observed: {', '.join(sorted(missing))}.")

    eligible_locations = {}
    for location in locations:
        if allowed and location.id not in allowed:
            continue
        if type(location.unlocked) is not bool:
            return blocked(f"Whether {location.name} is unlocked could not be confirmed.")
        if location.unlocked:
            eligible_locations[location.id] = location

    candidates = []
    seen_rooms = set()
    observed_locations = set()
    for room in rooms:
        if room.location_id not in location_by_id:
            return blocked(f"Room {room.name} refers to an unobserved location.")
        if room.location_id not in eligible_locations:
            continue
        if not isinstance(room.id, str) or not room.id.strip():
            return blocked(f"A room in {eligible_locations[room.location_id].name} has no identifier.")
        key = room.location_id, room.id
        if key in seen_rooms:
            return blocked(f"Duplicate room identifier: {room.location_id}/{room.id}.")
        seen_rooms.add(key)
        observed_locations.add(room.location_id)
        if type(room.available) is not bool:
            return blocked(f"Availability of {room.name} could not be confirmed.")
        if not room.available:
            continue
        if room.inspection_complete is not True:
            return blocked(f"The student inspection in {room.name} is incomplete.")
        student_ids = [student.id for student in room.students]
        if any(not isinstance(item, str) or not item.strip() for item in student_ids):
            return blocked(f"A student portrait in {room.name} has no identifier.")
        if len(student_ids) != len(set(student_ids)):
            return blocked(f"Duplicate student portraits in {room.name} would inflate the score.")
        for student in room.students:
            if student.owned is not None and type(student.owned) is not bool:
                return blocked(f"Invalid ownership evidence in {room.name}.")
            if student.bond is not None and not _integer(student.bond):
                return blocked(f"Invalid relationship rank in {room.name}.")
        candidates.append(room)

    if missing := eligible_locations.keys() - observed_locations:
        names = ", ".join(sorted(eligible_locations[item].name for item in missing))
        return blocked(f"No rooms were inspected in these unlocked locations: {names}.")
    if not candidates:
        return complete("No available rooms remain in the configured locations.")

    fallback = False
    effective_strategy = strategy
    if strategy == "school_rank":
        active_ids = {room.location_id for room in candidates}
        active = [eligible_locations[item] for item in active_ids]
        for location in active:
            if type(location.capped) is not bool:
                return blocked(f"Whether {location.name} is at maximum rank could not be confirmed.")
        uncapped = [location for location in active if not location.capped]
        if uncapped:
            for location in uncapped:
                if (not _integer(location.rank, 1) or not _integer(location.xp)
                        or not _integer(location.xp_to_next, 1)
                        or location.xp >= location.xp_to_next):
                    return blocked(f"The rank and XP in {location.name} could not be confirmed.")
            progress = {location.id: (location.rank, Fraction(location.xp, location.xp_to_next))
                        for location in uncapped}
            lowest = min(progress.values())
            candidates = [room for room in candidates if progress.get(room.location_id) == lowest]
        else:
            fallback = True
            effective_strategy = "relationship"

    if effective_strategy == "relationship":
        for room in candidates:
            if _owned_count(room) is None:
                return blocked(f"Student ownership in {room.name} could not be confirmed.")
        best_count = max(_owned_count(room) for room in candidates)
        if best_count == 0:
            return complete("No available room has an owned student; leaving tickets unused.",
                            fallback=fallback)
        candidates = [room for room in candidates if _owned_count(room) == best_count]
        score_description = f"{best_count} owned students"
    else:
        best_count = max(len(room.students) for room in candidates)
        candidates = [room for room in candidates if len(room.students) == best_count]
        score_description = f"{best_count} total students"

    if len(candidates) > 1:
        for room in candidates:
            if _bond_total(room) is None:
                return blocked(f"Relationship ranks in {room.name} are needed to break the student-count tie.")
        highest_bond = max(_bond_total(room) for room in candidates)
        candidates = [room for room in candidates if _bond_total(room) == highest_bond]

    def stable_key(room: LessonRoom) -> tuple[str, str, str, str]:
        location = eligible_locations[room.location_id]
        return location.name.casefold(), location.id, room.name.casefold(), room.id

    room = min(candidates, key=stable_key)
    location = eligible_locations[room.location_id]
    reason = f"{location.name} / {room.name}: {score_description}"
    if effective_strategy == "school_rank":
        reason += f" in a lowest-ranked available location (rank {location.rank}, XP {location.xp}/{location.xp_to_next})"
    if (bond := _bond_total(room)) is not None:
        reason += f"; owned relationship-rank sum {bond}"
    if fallback:
        reason += "; all available locations are at maximum rank, so relationship priority applies"
    return LessonDecision("selected", reason + ".", strategy, location, room,
                          _owned_count(room), len(room.students), bond, fallback)
