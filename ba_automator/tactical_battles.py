"""Deterministic Tactical Challenge choices, independent of device input.

The runner supplies observations and persists its pending battle before entry.
These helpers never treat an unread ticket count, opponent, or empty team slot as
permission to spend. Rewards remain a separate task.
"""

from dataclasses import dataclass, field
from datetime import date
import random


MAX_ATTEMPTS_PER_OPPONENT = 1
# Older releases permitted a second attempt. Keep that history readable and
# excluded from new choices; changing policy must never erase a spent ticket.
MAX_RECORDED_ATTEMPTS = 2
DEFAULT_PRESERVE_TICKETS = 1


class TacticalPlanningError(ValueError):
    """The observed ladder or formation cannot support a safe battle choice."""


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise TacticalPlanningError(f"{label} must be an observed integer of at least {minimum}")
    return value


def _identity(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TacticalPlanningError(f"{label} must be a nonempty observed identity")
    return value


def _day_key(value):
    try:
        valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise TacticalPlanningError("Tactical Challenge day must be a game-day YYYY-MM-DD key")
    return value


@dataclass(frozen=True)
class Opponent:
    opponent_id: str
    rank: int
    level: int
    # Only verified visible unit levels. Every omitted slot must be confirmed
    # hidden by the observer, not an unreadable visible number.
    visible_levels: tuple[int, ...] = ()

    def __post_init__(self):
        _identity(self.opponent_id, "Opponent")
        _integer(self.rank, "Opponent rank", 1)
        _integer(self.level, "Opponent level", 1)
        if not isinstance(self.visible_levels, (tuple, list)) or len(self.visible_levels) > 6:
            raise TacticalPlanningError("Opponent formation must contain at most six visible unit levels")
        for level in self.visible_levels:
            _integer(level, "Visible unit level", 1)
            if level > self.level:
                raise TacticalPlanningError("A visible unit level cannot exceed its opponent's account level")
        object.__setattr__(self, "visible_levels", tuple(self.visible_levels))


@dataclass(frozen=True)
class BattleState:
    day_key: str
    # Keys identify opponents, never their temporary row or rank in the list.
    attempts: dict[str, int] = field(default_factory=dict)
    retry_opponent_id: str | None = None

    def __post_init__(self):
        _day_key(self.day_key)
        if not isinstance(self.attempts, dict):
            raise TacticalPlanningError("Opponent attempts must be an identity-to-count mapping")
        counts = dict(self.attempts)
        for opponent_id, count in counts.items():
            _identity(opponent_id, "Attempted opponent")
            _integer(count, "Opponent attempt count", 1)
            if count > MAX_RECORDED_ATTEMPTS:
                raise TacticalPlanningError("An opponent cannot have more than two recorded attempts")
        if self.retry_opponent_id is not None:
            _identity(self.retry_opponent_id, "Retry opponent")
        # The field remains only for compatibility with earlier daily history.
        object.__setattr__(self, "retry_opponent_id", None)
        # Do not share the caller's mutable persistence dictionary.
        object.__setattr__(self, "attempts", counts)


def state_for_day(state, day_key):
    """Discard previous game-day opponents, including an unfinished retry."""
    _day_key(day_key)
    if state is not None and not isinstance(state, BattleState):
        raise TacticalPlanningError("Tactical Challenge history must be a BattleState")
    if state is None or state.day_key != day_key:
        return BattleState(day_key)
    return state


def ticket_budget(tickets, preserve=DEFAULT_PRESERVE_TICKETS):
    """Use only the observed tickets above the user's manual-play reserve."""
    _integer(tickets, "Ticket count")
    _integer(preserve, "Preserved tickets")
    return max(0, tickets - preserve)


def estimated_team_levels(opponent):
    """Sum all six levels, assuming each hidden unit is at the account level."""
    if not isinstance(opponent, Opponent):
        raise TacticalPlanningError("Opponent identity, rank, and formation levels must be observed")
    return sum(opponent.visible_levels) + (6 - len(opponent.visible_levels)) * opponent.level


def opponent_score(opponent):
    """User's strength heuristic: level / 90 below 90, double at 90 or above.

    This is a ranking heuristic, not a measured relationship or damage bonus.
    Multiply integer factors before division so equal scores tie exactly.
    """
    total = estimated_team_levels(opponent)
    return total * 2 if opponent.level >= 90 else total * opponent.level / 90


def rank_opponents(opponents, *, rng=None):
    """Order a completed survey by weighted strength, randomizing exact ties.

    Call once per survey and keep its returned ordering during lookup. Account
    level weights the six-unit total; it is not an additional tie breaker.
    An injected random.Random supports reproducible replay and tests.
    """
    candidates = list(opponents)
    if any(not isinstance(p, Opponent) for p in candidates):
        raise TacticalPlanningError("Every surveyed opponent must be observed")
    if len({p.opponent_id for p in candidates}) != len(candidates):
        raise TacticalPlanningError("Deduplicate surveyed opponent identities before ranking")
    (rng if rng is not None else random.SystemRandom()).shuffle(candidates)
    return tuple(sorted(candidates, key=opponent_score))


def eligible_opponents(opponents, own_rank):
    """Validate a current three-row list and return opponents ahead in UI order.

    Refresh until all three are ahead, except at rank two or three where only
    one or two higher ranks exist. First place has nothing left to climb. This
    validates a current list; rank_opponents orders the full multi-list survey.
    """
    _integer(own_rank, "Player rank", 1)
    if own_rank == 1:
        return ()
    candidates = tuple(opponents)
    if len(candidates) != 3 or any(not isinstance(p, Opponent) for p in candidates):
        raise TacticalPlanningError("A full list of three observed opponents is required")
    if len({p.opponent_id for p in candidates}) != 3 or len({p.rank for p in candidates}) != 3:
        raise TacticalPlanningError("The opponent list contains duplicate identities or ranks")
    ahead = tuple(p for p in candidates if p.rank < own_rank)
    if len(ahead) < min(3, own_rank - 1):
        raise TacticalPlanningError("Refresh until all available higher ranks fill the opponent list")
    return ahead


def choose_opponent(opponents, own_rank, state, tickets, preserve=DEFAULT_PRESERVE_TICKETS):
    """Pick the first opponent not fought during the current game day.

    Counts follow identities across list refreshes. The supplied candidates are
    the ranked aggregate survey, not necessarily the three currently visible
    rows. The runner locates the exact candidate before entry and resurveys
    after a victory. Both wins and losses exclude an opponent for the day.
    """
    if not isinstance(state, BattleState):
        raise TacticalPlanningError("Tactical Challenge history must be a BattleState")
    if ticket_budget(tickets, preserve) == 0:
        return None
    _integer(own_rank, "Player rank", 1)
    if own_rank == 1:
        return None
    candidates = tuple(opponents)
    if any(not isinstance(p, Opponent) for p in candidates):
        raise TacticalPlanningError("Every surveyed opponent must be observed")
    if len({p.opponent_id for p in candidates}) != len(candidates):
        raise TacticalPlanningError("The survey contains duplicate opponent identities")
    if any(p.rank >= own_rank for p in candidates):
        raise TacticalPlanningError("Surveyed opponents must still be ahead of the player")
    return next((p for p in candidates if state.attempts.get(p.opponent_id, 0) < MAX_ATTEMPTS_PER_OPPONENT), None)


def record_result(state, opponent_id, won):
    """Record one confirmed battle outcome without mutating previous history.

    This is completion bookkeeping, not the pre-entry ticket reservation. The
    runner must durably reserve an attempt before tapping the battle control so
    a crash or unreadable result cannot cause an unaccounted repeat.
    """
    if not isinstance(state, BattleState):
        raise TacticalPlanningError("Tactical Challenge history must be a BattleState")
    _identity(opponent_id, "Attempted opponent")
    if type(won) is not bool:
        raise TacticalPlanningError("Victory or defeat must be observed before recording the result")
    count = state.attempts.get(opponent_id, 0) + 1
    if count > MAX_ATTEMPTS_PER_OPPONENT:
        raise TacticalPlanningError("This opponent has already been fought today")
    attempts = dict(state.attempts)
    attempts[opponent_id] = count
    return BattleState(state.day_key, attempts)


@dataclass(frozen=True)
class Student:
    student_id: str
    role: str
    level: int

    def __post_init__(self):
        _identity(self.student_id, "Student")
        if self.role not in ("striker", "special"):
            raise TacticalPlanningError("Student role must be striker or special")
        _integer(self.level, "Student level", 1)


def fill_formation(existing, owned):
    """Preserve every filled slot, filling blanks with the highest-level owned.

    Slots 0–3 are Strikers and 4–5 are Specials. Equal levels retain the observed
    roster order. The caller must complete its roster survey before planning;
    unobserved or missing students never become an implicit Auto formation.
    """
    slots = tuple(existing)
    if len(slots) != 6:
        raise TacticalPlanningError("A formation must contain four Striker and two Special slots")
    identities = set()
    for slot, student in enumerate(slots):
        if student is None:
            continue
        role = "striker" if slot < 4 else "special"
        if not isinstance(student, Student) or student.role != role:
            raise TacticalPlanningError("An occupied formation slot has an unreadable or mismatched role")
        if student.student_id in identities:
            raise TacticalPlanningError("A student cannot occupy two formation slots")
        identities.add(student.student_id)
    if all(student is not None for student in slots):
        return slots

    candidates = {}
    for student in owned:
        if not isinstance(student, Student):
            raise TacticalPlanningError("Owned student identities, roles, and levels must be observed")
        previous = candidates.get(student.student_id)
        if previous is not None and previous != student:
            raise TacticalPlanningError("The roster contains conflicting observations of the same student")
        candidates[student.student_id] = student
    ordered = sorted(candidates.values(), key=lambda student: -student.level)
    result = list(slots)
    for slot, student in enumerate(result):
        if student is not None:
            continue
        role = "striker" if slot < 4 else "special"
        choice = next((p for p in ordered if p.role == role and p.student_id not in identities), None)
        if choice is None:
            raise TacticalPlanningError(f"Not enough observed owned {role}s to fill the formation")
        result[slot] = choice
        identities.add(choice.student_id)
    return tuple(result)


def run_tactical_battles(*args, **kwargs):
    """Keep the public task entry point separate from these pure decisions."""
    from .tactical_runtime import run_tactical_battles as run

    return run(*args, **kwargs)
