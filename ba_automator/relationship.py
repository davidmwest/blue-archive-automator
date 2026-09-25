"""Read the relationship celebration without estimating unseen progress.

The English celebration shows a portrait, a heart containing the new rank, and
stat deltas. It does not show a student name. A caller may supply a name only
when it has independent, verified context; the portrait is never classified.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re

import cv2
import numpy as np

from .vision import Word


@dataclass(frozen=True)
class RelationshipStat:
    name: str
    before: int | None = None
    after: int | None = None
    delta: int | None = None


@dataclass(frozen=True)
class RelationshipIncrease:
    student: str | None
    rank: int | None
    stats: tuple[RelationshipStat, ...]

    def fields(self):
        return {
            "student": self.student,
            "rank": self.rank,
            "stats": [asdict(stat) for stat in self.stats],
            "unread_fields": [name for name, value in
                              (("student", self.student), ("rank", self.rank),
                               ("stats", self.stats)) if not value],
        }

    def detail(self):
        subject = self.student or "Student"
        rank = f"relationship rank {self.rank}" if self.rank is not None else "a relationship rank"
        changes = ", ".join(f"{stat.name} {stat.delta:+d}" for stat in self.stats
                            if stat.delta is not None)
        return f"{subject} reached {rank}" + (f"; {changes}." if changes else ".")


def _within(words, bounds):
    left, top, right, bottom = bounds
    return [word for word in words if word.confidence >= .85
            and left <= word.center[0] <= right and top <= word.center[1] <= bottom]


def is_relationship_rank_up(words):
    return any(word.normalized == "relationship rank up"
               for word in _within(words, (350, 530, 940, 680)))


def _unique_rank(words):
    values = {int(word.text.strip()) for word in words
              if word.confidence >= .85 and re.fullmatch(r"[0-9]{1,3}", word.text.strip())
              and 1 <= int(word.text.strip()) <= 100}
    return next(iter(values)) if len(values) == 1 else None


def _stats(words):
    # Only the fixed stat bar is evidence. Never turn an account counter or a
    # student level visible behind another screen into a relationship benefit.
    parts = sorted(_within(words, (200, 660, 1080, 712)), key=lambda word: word.center[0])
    text = " ".join(word.text for word in parts)
    labels = {"atk": "ATK", "attack": "ATK", "hp": "HP", "max hp": "HP",
              "def": "DEF", "defense": "DEF", "healing": "Healing", "heal": "Healing"}
    names = r"Max HP|ATK|Attack|HP|DEF|Defense|Healing|Heal"
    pattern = (rf"(?<![A-Za-z])(?P<name>{names})\s*\+\s*(?P<delta>[0-9][0-9,]*)"
               rf"(?=\s*(?:$|(?:{names})\b))")
    values = {}
    for match in re.finditer(pattern, text, re.I):
        raw = match["delta"]
        if not re.fullmatch(r"[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+", raw):
            continue
        name = labels[match["name"].lower()]
        values.setdefault(name, set()).add(int(raw.replace(",", "")))
    return tuple(RelationshipStat(name=name, delta=next(iter(deltas)))
                 for name, deltas in values.items() if len(deltas) == 1)


def parse_relationship_rank_up(words, *, rank_words=(), student=None):
    """Parse only values anchored to a verified celebration.

    ``rank_words`` are OCR results from the isolated heart number. Conflicting
    whole-frame/crop readings remain unknown, as do before/after stat totals:
    the screen exposes only the increment, not those totals.
    """
    words = tuple(words)
    if not is_relationship_rank_up(words):
        return None
    rank_candidates = _within(words, (608, 512, 672, 567)) + list(rank_words)
    return RelationshipIncrease(student=student, rank=_unique_rank(rank_candidates), stats=_stats(words))


def read_relationship_rank_up(frame, reader=None, *, words=(), student=None):
    """OCR the small heart separately; whole-screen OCR often omits its digit."""
    words = tuple(words)
    if not is_relationship_rank_up(words):
        return None
    rank_words = ()
    if reader is not None:
        crop = cv2.resize(frame[512:567, 608:672], None, fx=3, fy=3,
                          interpolation=cv2.INTER_CUBIC)
        rank_words = tuple(reader.read(crop))
    return parse_relationship_rank_up(words, rank_words=rank_words, student=student)


def same_relationship_screen(before, after):
    """Compare portrait and rank pixels when rechecking a lingering overlay.

    Animated particles are tolerated over a small fraction of the portrait.
    The heart's dark numeral must match independently, so a later rank for the
    same student is not collapsed into the previous celebration.
    """
    if before.shape != after.shape or before.shape[:2] != (720, 1280):
        return False
    a, b = before[70:450, 260:1020], after[70:450, 260:1020]
    difference = np.abs(a.astype(np.int16) - b.astype(np.int16))
    if float(difference.mean()) > 4 or float((difference.max(axis=2) > 25).mean()) > .04:
        return False
    a = cv2.cvtColor(before[522:555, 618:663], cv2.COLOR_BGR2GRAY) < 160
    b = cv2.cvtColor(after[522:555, 618:663], cv2.COLOR_BGR2GRAY) < 160
    return float((a != b).mean()) <= .02


def record_relationship_increase(config, result, *, evidence, run_dir, task,
                                 record_action, **context):
    """One canonical important action for both Cafe and Lessons celebrations."""
    result = result or RelationshipIncrease(None, None, ())
    return record_action(config, "relationship_rank_increased", result.detail(), task=task,
                         evidence=str(evidence), run_dir=str(run_dir),
                         **result.fields(), **context)
