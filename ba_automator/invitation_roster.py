"""Read an owned student's current rarity before an automatic Cafe invitation.

This route only opens Students and Basic Info. It never opens an upgrade tab.
Rarity matters at relationship-cap boundaries; an unknown observation is not
permission to invite someone whose relationship might already be capped.
"""
from __future__ import annotations

import re

import cv2
import numpy as np

from .runtime import FRAME_MAX_AGE, HOME_STABLE_SECONDS
from .vision import classify

STAR_BOUNDS = (255, 565, 340, 591)


def _identity(value):
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _words(words, bounds):
    x1, y1, x2, y2 = bounds
    return [word for word in words if word.confidence >= .85
            and x1 <= word.center[0] <= x2 and y1 <= word.center[1] <= y2]


def _has(words, name, bounds):
    return sum(word.normalized == name for word in _words(words, bounds)) == 1


def _name_parts(words, bounds):
    """Keep the whole observed identity or reject it; never drop a weak variant.

    Numeric fragments belong to card metadata rather than student names. Every
    other visible token must be readable, otherwise a variant such as Aris
    (Maid) could be shortened into a confident but incorrect base identity.
    """
    x1, y1, x2, y2 = bounds
    parts = [word for word in words if word.text.strip()
             and x1 <= word.center[0] <= x2 and y1 <= word.center[1] <= y2
             and not word.normalized.isdecimal()]
    if any(word.confidence < .85 or not word.normalized for word in parts):
        return None
    return sorted(parts, key=lambda word: (word.center[1], word.center[0]))


def _bright_header(frame):
    return frame.shape == (720, 1280, 3) and float(frame[0:40, 280:400].mean()) > 180


def roster_screen(frame, words):
    """Require the uncovered roster header, all three tabs, and owned cards."""
    return (_bright_header(frame)
            and _has(words, "students", (90, 0, 250, 50))
            and _has(words, "student list", (75, 85, 280, 145))
            and _has(words, "all", (390, 85, 500, 145))
            and _has(words, "striker", (525, 85, 650, 145))
            and _has(words, "special", (675, 85, 790, 145))
            and bool(roster_cards(words)))


def roster_cards(words):
    """Use each owned card's level as its local name anchor after scrolling.

    The faded bottom row is deliberately excluded. A smaller overlapping drag
    brings it into view; guessing a clipped variant would select the wrong unit.
    """
    cards = []
    levels = [word for word in _words(words, (50, 220, 1220, 610))
              if re.fullmatch(r"lv\.?\s*\d{1,2}", word.text.strip(), re.I)]
    for level in levels:
        column = round((level.center[0] - 140) / 196)
        if column not in range(6):
            continue
        left, right = 42 + column * 196, 238 + column * 196
        if not left < level.center[0] < right:
            continue
        parts = _name_parts(words, (left, level.box[3] + 4, right, min(level.box[3] + 83, 660)))
        if parts is None:
            continue
        name = " ".join(word.normalized for word in parts)
        if name and not any(re.match(r"^(?:lv|level)(?: |$)", word.normalized) for word in parts):
            cards.append({"identity": name, "target": (140 + column * 196, level.center[1] - 75)})
    return cards


def profile_identity(words):
    if not (_has(words, "student", (90, 0, 250, 50))
            and _has(words, "basic info", (660, 105, 835, 170))
            and _has(words, "stats", (670, 180, 775, 220))):
        return None
    parts = _name_parts(words, (70, 545, 255, 592))
    if parts is None:
        return None
    identity = " ".join(word.normalized for word in parts)
    return identity or None


def profile_rarity(frame, words, student):
    """Return 1..5 only when an exact profile identity and gold stars agree."""
    if not _bright_header(frame) or profile_identity(words) != _identity(student):
        return None
    x1, y1, x2, y2 = STAR_BOUNDS
    strip = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (15, 120, 130), (40, 255, 255)) > 0
    ys, xs = np.where(gold)
    if not len(xs) or not 14 <= int(ys.max() - ys.min() + 1) <= 20:
        return None
    # The bottom halves touch. Their top three pixels remain separate and
    # correspond one-to-one with stars, including the one- and two-star cases.
    peaks = np.any(gold[ys.min():ys.min() + 3], axis=0)
    edges = np.diff(np.r_[False, peaks, False].astype(np.int8))
    groups = list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))
    centers = [(left + right - 1) / 2 for left, right in groups]
    if not 1 <= len(groups) <= 5 or any(not 1 <= right - left <= 6 for left, right in groups):
        return None
    if any(not 12 <= b - a <= 17 for a, b in zip(centers, centers[1:])):
        return None
    covered = np.zeros_like(gold)
    for center in centers:
        left, right = max(0, int(center) - 8), min(gold.shape[1], int(center) + 10)
        tile = gold[:, left:right]
        if not 75 <= int(tile.sum()) <= 185:
            return None
        covered[:, left:right] = True
    if np.any(gold & ~covered):
        return None
    return len(groups)


def _home(runner, cap):
    return classify(cap[3], runner.startup.matches(cap[2])).state == "home"


def _wait(runner, predicate, message, timeout=30):
    end = runner.clock() + timeout
    while runner.clock() < end:
        cap = runner.capture(ocr=True)
        if predicate(cap) and runner.clock() - cap[0] < FRAME_MAX_AGE - 1:
            return cap
        runner.sleep(.6)
    runner.fail(message)


def _scroll(runner, cap, *, down):
    start, end = ((630, 595), (630, 335)) if down else ((630, 290), (630, 600))
    runner.journal.record("intent", operation="invitation_roster_scroll", start=list(start), end=list(end))
    sent = runner.device.swipe(start, end, duration_ms=700,
                              deadline=cap[0] + FRAME_MAX_AGE, monotonic=runner.clock)
    runner.journal.record("outcome", operation="invitation_roster_scroll",
                          result="ok" if sent else "skipped_stale")
    if not sent:
        runner.fail("Student roster frame expired before scrolling")
    runner.sleep(.8)
    return _wait(runner, lambda value: roster_screen(value[2], value[3]),
                 "Student roster was not recognized after scrolling")


def _stationary(before, after):
    old, new = roster_cards(before[3]), roster_cards(after[3])
    return bool(old) and len(old) == len(new) and all(
        first["identity"] == second["identity"]
        and all(abs(a - b) <= 5 for a, b in zip(first["target"], second["target"]))
        for first, second in zip(old, new)
    )


def _all_selected(frame):
    # The active tab is a dark blue trapezoid, unlike the white inactive tab.
    return float(frame[98:132, 388:420].mean()) < 120


def read_current_stars(runner, student, *, max_pages=45):
    """From home, inspect one exact owned profile and return home with its rarity.

    The caller owns the instance lock and restores the original Cafe floor.
    No persistent roster cache is used: upgrading a student changes their cap.
    """
    normalized = _identity(student)
    if not normalized or not isinstance(max_pages, int) or not 1 <= max_pages <= 60:
        runner.fail("Cannot verify invitation rarity without a bounded exact student name")
    cap = _wait(runner, lambda value: _home(runner, value),
                "Invitation rarity inspection requires an unobstructed home screen")
    runner.phase(f"Check current rarity for {student} before choosing an invitation")
    runner.tap(cap, (323, 659), "Open Students from verified home")
    cap = _wait(runner, lambda value: roster_screen(value[2], value[3]),
                "Owned student roster did not appear")
    runner.tap(cap, (445, 115), "Show All owned students for invitation rarity inspection")
    cap = _wait(runner, lambda value: roster_screen(value[2], value[3]) and _all_selected(value[2]),
                "The All students tab could not be verified")

    # The user may have left the roster halfway down. Two stationary reverse
    # drags establish its beginning; never assume a newly opened list is at top.
    stationary = 0
    for _ in range(max_pages):
        after = _scroll(runner, cap, down=False)
        stationary = stationary + 1 if _stationary(cap, after) else 0
        cap = after
        if stationary >= 2:
            break
    else:
        runner.fail("Student roster beginning was not reached within the bounded scan")

    stationary = 0
    for page in range(max_pages):
        matches = [card for card in roster_cards(cap[3]) if card["identity"] == normalized]
        if len(matches) > 1:
            runner.fail("Invitation student's roster identity is ambiguous")
        if matches:
            runner.tap(cap, matches[0]["target"], f"Inspect {student}'s owned student profile")
            break
        if page + 1 == max_pages:
            runner.fail("Invitation student was not found within the bounded owned roster scan")
        after = _scroll(runner, cap, down=True)
        stationary = stationary + 1 if _stationary(cap, after) else 0
        cap = after
        if stationary >= 2:
            runner.fail("Invitation student was not found in the owned roster")

    cap = _wait(runner, lambda value: profile_identity(value[3]) == normalized,
                "Invitation student profile identity did not match the selected name")
    rarity = profile_rarity(cap[2], cap[3], student)
    if rarity is None:
        runner.fail("Invitation student's current rarity could not be verified from gold stars")
    evidence = f"invitation-rarity-{runner.actions}.png"
    runner.journal.save_image(evidence, cap[1])
    runner.journal.record("invitation_rarity_observed", student=student, rarity=rarity, frame=evidence)
    runner.tap(cap, (1237, 24), "Return home after reading current student rarity")
    end, since, count = runner.clock() + 45, None, 0
    while runner.clock() < end:
        cap = runner.capture(ocr=True)
        if _home(runner, cap) and runner.clock() - cap[0] < FRAME_MAX_AGE - 1:
            since = runner.clock() if since is None else since
            count += 1
            if runner.clock() - since >= HOME_STABLE_SECONDS and count >= runner.config.home_confirmations:
                return rarity
        else:
            since, count = None, 0
        runner.sleep(.6)
    runner.fail("Home was not stable after invitation rarity inspection")
