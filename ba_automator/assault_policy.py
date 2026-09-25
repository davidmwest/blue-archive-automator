"""Deterministic Total Assault team choices and mock-battle qualification.

Missing observations never count as success. The runner supplies observations from
the game; none of these functions perform device input or infer student identities.
"""

from dataclasses import asdict, dataclass, replace
from datetime import date
import hashlib
import json
import math


DIFFICULTIES = ("normal", "hard", "very_hard", "hardcore", "extreme", "insane", "torment", "lunatic")
DAMAGE_TYPES = ("explosive", "piercing", "mystic", "sonic")
DEFAULT_MIN_REMAINING_SECONDS = 30


class AssaultPlanningError(ValueError):
    """A difficulty choice needs more observed information before spending."""


def next_difficulty(target, locked_by_difficulty):
    """Choose the target or its nearest verified accessible prerequisite.

    Values are explicit lock observations: ``False`` means an enabled Enter
    control was seen, ``True`` means Locked was seen, and ``None`` or a missing
    key means unknown. An unknown tier between the target and an accessible tier
    stops planning. The runner must resurvey after every prerequisite clear;
    this function never predicts that winning one tier unlocks another.
    """
    if target not in DIFFICULTIES:
        raise AssaultPlanningError("Unknown requested Total Assault difficulty")
    if not isinstance(locked_by_difficulty, dict):
        raise AssaultPlanningError("Total Assault difficulty locks must be observed")
    for difficulty in reversed(DIFFICULTIES[:DIFFICULTIES.index(target) + 1]):
        locked = locked_by_difficulty.get(difficulty)
        if type(locked) is not bool:
            raise AssaultPlanningError(
                f"The {difficulty.replace('_', ' ')} lock is unreadable; inspect it before choosing a prerequisite")
        if locked is False:
            return difficulty
    raise AssaultPlanningError("No verified accessible Total Assault prerequisite; inspect the difficulty list")


@dataclass(frozen=True)
class AssaultContext:
    event_id: str
    boss: str
    difficulty: str
    day_key: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.event_id, self.boss)):
            raise ValueError("Total Assault event and boss must be observed")
        if self.difficulty not in DIFFICULTIES:
            raise ValueError("Unknown Total Assault difficulty")
        if not isinstance(self.day_key, str) or date.fromisoformat(self.day_key).isoformat() != self.day_key:
            raise ValueError("Total Assault day must be a game-day YYYY-MM-DD key")


@dataclass(frozen=True)
class TeamMember:
    student_id: str
    slot: int
    role: str
    damage_type: str
    stars: int
    level: int
    assistant: bool = False
    # Different lenders can offer the same student with different invested skills.
    assistant_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.student_id, str) or not self.student_id.strip():
            raise ValueError("A student identity must be observed")
        if type(self.slot) is not int or not 0 <= self.slot <= 5:
            raise ValueError("Team slot must be between 0 and 5")
        if self.role not in ("striker", "special") or self.damage_type not in DAMAGE_TYPES:
            raise ValueError("Unknown student role or damage type")
        if type(self.stars) is not int or not 1 <= self.stars <= 5:
            raise ValueError("Student stars must be observed, between 1 and 5")
        if type(self.level) is not int or not 1 <= self.level <= 999:
            raise ValueError("Student level must be observed")
        if type(self.assistant) is not bool:
            raise ValueError("Assistant flag must be boolean")
        if self.assistant and (not isinstance(self.assistant_id, str) or not self.assistant_id.strip()):
            raise ValueError("An assistant must identify the exact lender offering")
        if not self.assistant and self.assistant_id is not None:
            raise ValueError("An owned student cannot have a lender identity")


@dataclass(frozen=True)
class MockResult:
    won: bool
    remaining_seconds: float | None
    surviving_striker_ids: tuple[str, ...] | None
    damage_by_student: dict[str, int]


def validate_team(team):
    members = tuple(team)
    if len(members) != 6 or any(not isinstance(member, TeamMember) for member in members):
        raise ValueError("A verified team must contain four strikers and two specials")
    if {member.slot for member in members} != set(range(6)):
        raise ValueError("Team slots must be unique")
    if len({member.student_id for member in members}) != 6:
        raise ValueError("A student cannot appear twice in the team")
    if any(member.role != ("striker" if member.slot < 4 else "special") for member in members):
        raise ValueError("The first four team slots must be strikers")
    assistants = [member for member in members if member.assistant]
    if len(assistants) > 1 or any(member.role != "striker" for member in assistants):
        raise ValueError("Only one assistant striker is allowed")
    return tuple(sorted(members, key=lambda member: member.slot))


def team_fingerprint(team):
    body = [asdict(member) for member in validate_team(team)]
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def comfortable_win(team, result, min_remaining_seconds=DEFAULT_MIN_REMAINING_SECONDS):
    """Require verified victory and time margin; reject any observed retirement.

    Some result screens do not disclose survivors. Their absence is not a claim
    that all strikers survived, and does not invalidate an otherwise verified win.
    """
    members = validate_team(team)
    if type(min_remaining_seconds) is not int or min_remaining_seconds < 0:
        raise ValueError("The comfort margin must be a nonnegative integer")
    if not isinstance(result, MockResult) or result.won is not True:
        return False
    remaining = result.remaining_seconds
    if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining < min_remaining_seconds:
        return False
    survivors = result.surviving_striker_ids
    if survivors is None:
        return True
    return (
        isinstance(survivors, (list, tuple))
        and len(survivors) == 4
        and set(survivors) == {member.student_id for member in members if member.role == "striker"}
    )


def least_damage_striker(team, result):
    """Unknown damage is not zero; a complete four-striker report is required."""
    members = [member for member in validate_team(team) if member.role == "striker"]
    if not isinstance(result, MockResult) or not isinstance(result.damage_by_student, dict):
        return None
    if any(type(result.damage_by_student.get(member.student_id)) is not int
           or result.damage_by_student[member.student_id] < 0 for member in members):
        return None
    return min(members, key=lambda member: (result.damage_by_student[member.student_id], member.slot))


def choose_assistant(candidates, damage_type, remaining_team):
    """Highest observed stars, then level; caller must finish scanning all pages.

    Candidate order supplies a deterministic tie break. ``None`` means there is
    no compatible verified assistant, not permission to enter without a mock.
    """
    if damage_type not in DAMAGE_TYPES:
        raise ValueError("Auto formation's damage type must be observed")
    existing = tuple(remaining_team)
    if any(member.assistant for member in existing):
        return None
    identities = {member.student_id for member in existing}
    eligible = [member for member in candidates
                if isinstance(member, TeamMember) and member.assistant
                and member.role == "striker" and member.damage_type == damage_type
                and member.student_id not in identities]
    return max(eligible, key=lambda member: (member.stars, member.level), default=None)


def replace_striker(team, removed_id, assistant):
    members = validate_team(team)
    removed = next((member for member in members if member.student_id == removed_id), None)
    if removed is None or removed.role != "striker" or removed.assistant:
        raise ValueError("Replace an owned striker from the observed auto team")
    if not isinstance(assistant, TeamMember) or not assistant.assistant or assistant.role != "striker":
        raise ValueError("Replacement must be an assistant striker")
    return validate_team(tuple(replace(assistant, slot=removed.slot) if member == removed else member for member in members))
