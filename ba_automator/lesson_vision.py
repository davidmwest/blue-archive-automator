"""Local recognition for the fixed English Lessons interface.

The planner receives observed values, including ``None`` for unresolved evidence.
Student artwork never determines ownership: the portrait border and relationship
heart must agree. All coordinates are scoped to an identified screen or modal.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

import cv2
import numpy as np

from .lesson_planner import LessonStudent
from .vision import Word, decode_frame


@dataclass(frozen=True)
class LocationRow:
    name: str
    rank: int | None
    xp: int | None
    xp_to_next: int | None
    capped: bool | None
    target: tuple[int, int]
    unlocked: bool | None = True


@dataclass(frozen=True)
class RoomCard:
    index: int
    name: str
    level: int | None
    available: bool | None
    target: tuple[int, int]
    students: tuple[LessonStudent, ...] = ()
    inspection_complete: bool = True


@dataclass(frozen=True)
class LessonScreen:
    kind: str
    tickets: int | None = None
    ticket_capacity: int | None = None
    total_rank: int | None = None
    location_rows: tuple[LocationRow, ...] = ()
    location_name: str | None = None
    location_rank: int | None = None
    location_xp: int | None = None
    location_xp_to_next: int | None = None
    location_capped: bool | None = None
    room_cards: tuple[RoomCard, ...] = ()
    room_name: str | None = None
    students: tuple[LessonStudent, ...] = ()
    start_target: tuple[int, int] | None = None
    tickets_after: int | None = None
    dismiss_target: tuple[int, int] | None = None
    inspection_complete: bool = True
    words: tuple[Word, ...] = ()
    detail: str = ""


def _within(words, bounds):
    x1, y1, x2, y2 = bounds
    return [word for word in words if x1 <= word.center[0] <= x2 and y1 <= word.center[1] <= y2]


def _reading_order(words):
    lines = []
    for word in sorted(words, key=lambda w: w.center[1]):
        for line in lines:
            reference = line[0]
            tolerance = max(6, .35 * max(word.box[3] - word.box[1], reference.box[3] - reference.box[1]))
            if abs(word.center[1] - reference.center[1]) <= tolerance:
                line.append(word)
                break
        else:
            lines.append([word])
    return [word for line in lines for word in sorted(line, key=lambda w: w.center[0])]


def _text(words):
    return " ".join(word.text for word in _reading_order(words))


def _has(words, text, bounds):
    return any(word.normalized == text for word in _within(words, bounds))


def _bright(frame, bounds):
    x1, y1, x2, y2 = bounds
    return float(np.median(frame[y1:y2, x1:x2])) >= 180


def _active_confirmation(frame, word):
    x, y = word.center
    crop = frame[max(0, y - 20):min(720, y + 20), max(0, x - 85):min(1280, x + 85)]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    cyan = ((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 110)
            & (hsv[:, :, 1] >= 70) & (hsv[:, :, 1] <= 210)
            & (hsv[:, :, 2] >= 180))
    return float(cyan.mean()) >= .25


def _unique_matches(words, expression):
    matches = [re.fullmatch(expression, word.text.strip(), re.I) for word in words]
    return [match for match in matches if match]


def _ratio(words):
    matches = _unique_matches(words, r"([\d,]+)\s*/\s*([\d,]+)")
    values = {(int(m[1].replace(",", "")), int(m[2].replace(",", ""))) for m in matches}
    return next(iter(values)) if len(values) == 1 else (None, None)


def _rank(words):
    matches = _unique_matches(words, r"[\[|]?\s*rank\s*(\d+)[\]|]?")
    values = {int(match[1]) for match in matches}
    return next(iter(values)) if len(values) == 1 else None


def _ticket_count(words, bounds):
    selected = _within(words, bounds)
    text = _text(selected)
    matches = re.findall(r"tickets\s*owned\s*(\d+)\s*/\s*(\d+)", text, re.I)
    values = {(int(current), int(capacity)) for current, capacity in matches}
    ratios = {(int(current), int(capacity))
              for current, capacity in re.findall(r"(\d+)\s*/\s*(\d+)", text)}
    if len(values) != 1 or ratios != values:
        return None, None
    current, capacity = next(iter(values))
    return (current, capacity) if 0 <= current <= 99 and 1 <= capacity <= 99 else (None, None)


def _header_name(words, *, level=False):
    expression = r"(?:lv\.?\s*)(\d+)\s*[|]?\s*(.*)" if level else r"[\[|]?\s*rank\s*(\d+)\s*[\]|]?\s*(.*)"
    pieces = []
    number = None
    for word in _reading_order(words):
        match = re.fullmatch(expression, word.text.strip(), re.I)
        if match:
            if number is not None and number != int(match[1]):
                return None, None
            number = int(match[1])
            if match[2].strip():
                pieces.append(match[2].strip())
        elif word.normalized not in {"reward", "rewards", "max"}:
            pieces.append(word.text.strip())
    name = " ".join(pieces).strip(" |")
    return (number, name or None)


class LessonVision:
    def __init__(self, startup):
        self.startup = startup
        self._bond_cache: dict[bytes, int | None] = {}
        self._header_cache: dict[bytes, str | None] = {}
        self._ticket_cache: dict[bytes, tuple[int | None, int | None]] = {}

    def _tickets(self, frame, words, bounds):
        result = _ticket_count(words, bounds)
        if result[0] is not None:
            return result
        # Small counters can disappear from whole-frame OCR after a ticket is
        # spent. Recover only inside the identified screen's ticket control;
        # resource and area-XP ratios elsewhere are never candidates.
        selected = _text(_within(words, bounds))
        if len(re.findall(r"tickets\s*owned", selected, re.I)) != 1:
            return None, None
        observed = {(int(current), int(capacity))
                    for current, capacity in re.findall(r"(\d+)\s*/\s*(\d+)", selected)}
        if len(observed) > 1:
            return None, None
        x1, y1, x2, y2 = bounds
        crop = frame[y1:y2, x1:x2]
        key = crop.tobytes()
        if key not in self._ticket_cache:
            candidates = []
            for scale in (2, 3):
                enlarged = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                cropped_words = self.startup.read(enlarged)
                text = _text(cropped_words)
                if (not cropped_words or any(word.confidence < .9 for word in cropped_words)
                        or len(re.findall(r"tickets\s*owned", text, re.I)) != 1):
                    candidates.append((None, None))
                else:
                    candidates.append(_ticket_count(cropped_words, (0, 0, enlarged.shape[1], enlarged.shape[0])))
            result = candidates[0] if candidates[0] == candidates[1] else (None, None)
            if len(self._ticket_cache) >= 64:
                self._ticket_cache.clear()
            self._ticket_cache[key] = result
        result = self._ticket_cache[key]
        return result if not observed or result in observed else (None, None)

    def _room_name(self, frame, x, y, fallback):
        # Long/wrapped labels can lose a letter in full-screen OCR (Club/Cub).
        # The name crop excludes the circular level badge and room portraits.
        if len(fallback) < 16:
            return fallback
        crop = frame[y + 4:y + 66, x + 80:x + 331]
        key = crop.tobytes()
        if key not in self._header_cache:
            # Text against a tight crop edge can make detection split overlapping
            # words, duplicating letters ("Hyakkiyako S Shopping"). A small plain
            # margin preserves complete lines without changing their contents.
            padded = cv2.copyMakeBorder(crop, 8, 8, 8, 8, cv2.BORDER_CONSTANT,
                                       value=(255, 255, 255))
            candidates = []
            for scale in (2, 3):
                enlarged = cv2.resize(padded, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                words = self.startup.read(enlarged)
                candidates.append(_text(words).strip() if words and all(word.confidence >= .85 for word in words) else None)
            normalized = [re.sub(r"[^a-z0-9]", "", candidate.lower()) if candidate else None
                          for candidate in candidates]
            value = candidates[-1] if normalized[0] and normalized[0] == normalized[1] else None
            if len(self._header_cache) >= 256:
                self._header_cache.clear()
            self._header_cache[key] = value
        return self._header_cache[key] or fallback

    def _bond(self, frame, left, top):
        # Isolate the small heart label; whole-grid OCR frequently drops its digits.
        crop = frame[top + 29:top + 51, left + 36:left + 70]
        key = crop.tobytes()
        if key in self._bond_cache:
            return self._bond_cache[key]
        def read_number(image):
            enlarged = cv2.resize(image, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
            words = self.startup.read(enlarged)
            values = {int(word.text.strip()) for word in words
                      if re.fullmatch(r"\d{1,3}", word.text.strip()) and word.confidence >= .85}
            return next(iter(values)) if len(values) == 1 else None

        result = read_number(crop)
        if result is None:
            # The wider crop sometimes joins adjacent hair to a single digit
            # (for example C4). This crop stays entirely on the heart label.
            result = read_number(frame[top + 33:top + 54, left + 40:left + 67])
        if result is not None and not 1 <= result <= 100:
            result = None
        if len(self._bond_cache) >= 256:
            self._bond_cache.clear()
        self._bond_cache[key] = result
        return result

    def _students(self, frame, left, top, *, slots=4, step=72, allow_leading_blank=False):
        students = []
        complete = True
        blank_seen = False
        for slot in range(slots):
            x = left + step * slot
            portrait = frame[top:top + 55, x:x + 66]
            inner = portrait[4:45, 10:48]
            # Empty slots are the flat card background. Intermediate uncertainty
            # keeps a portrait in the observation instead of shrinking the count.
            contrast = float(np.std(cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)))
            if contrast < 3:
                if students or not allow_leading_blank:
                    blank_seen = True
                continue
            if contrast < 9 or blank_seen:
                complete = False
            hsv = cv2.cvtColor(portrait, cv2.COLOR_BGR2HSV)
            pink = (hsv[:, :, 0] >= 145) & (hsv[:, :, 1] >= 30) & (hsv[:, :, 2] >= 150)
            blue = ((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 110)
                    & (hsv[:, :, 1] >= 20) & (hsv[:, :, 1] <= 150)
                    & (hsv[:, :, 2] >= 90))
            heart = int(pink[30:55, 40:66].sum())
            outside_heart = int(pink[34:54, 60:66].sum())
            pink_border = int(pink[:4].sum() + pink[:, :4].sum())
            blue_border = int(blue[:4].sum() + blue[:, :4].sum())
            owned = None
            if heart >= 100 and outside_heart >= 30 and pink_border >= 35:
                owned = True
            elif outside_heart < 10 and blue_border >= 80:
                owned = False
            bond = self._bond(frame, x, top) if owned is True else None
            students.append(LessonStudent(str(slot), owned, bond))
        return tuple(students), complete

    def _rooms(self, frame, words):
        cards = []
        for index in range(9):
            row, column = divmod(index, 3)
            x, y = (129, 474, 818)[column], (181, 332, 484)[row]
            header = _within(words, (x + 10, y + 12, x + 326, y + 62))
            level, name = _header_name(header, level=True)
            header_brightness = float(frame[y + 8:y + 20, x + 85:x + 280].mean())
            if level is None:
                if header_brightness > 225 or header_brightness < 120:
                    cards.append(RoomCard(index, f"Unrecognized room {index + 1}", None,
                                          None, (x + 168, y + 70), inspection_complete=False))
                continue
            inner_words = _within(words, (x, y, x + 336, y + 143))
            text = _text(inner_words).lower()
            excluded = bool(re.search(r"rank\s*\d+\s*required|lesson\s*complete|completed", text))
            available = False if excluded else True if header_brightness > 180 else None
            if available is True and name:
                name = self._room_name(frame, x, y, name)
            students, complete = self._students(frame, x + 20, y + 78) if available is True else ((), True)
            if available is True and students:
                # Finished rooms keep a white title and bright heart labels, but
                # every portrait's standard border is dimmed. Student artwork or
                # a green check alone does not establish that the room is done.
                borders = []
                for student in students:
                    left = x + 20 + 72 * int(student.id)
                    top = y + 78
                    border = frame[top:top + 2, left + 15:left + 48].max(axis=2)
                    borders.append(float(np.percentile(border, 75)))
                if all(value <= 140 for value in borders):
                    available, students = False, ()
            cards.append(RoomCard(index, name or f"Unrecognized room {index + 1}", level,
                                  available, (x + 168, y + 70), students,
                                  complete and name is not None))
        return tuple(cards)

    def _overview(self, words):
        rows = []
        # Anchoring each row to its rank handles partially scrolled list pages.
        ranks = [word for word in _within(words, (650, 138, 735, 657))
                 if re.fullmatch(r"[\[|]?\s*rank\s*\d+[\]|]?", word.text.strip(), re.I)]
        for anchor in ranks:
            y = anchor.center[1]
            # Only whole rows can be clicked; a clipped rank at the list edge is
            # not enough evidence for the location's contents or XP display.
            if y + 74 > 676:
                continue
            header = _within(words, (650, y - 22, 1120, y + 22))
            rank, name = _header_name(header)
            progress = _within(words, (650, y + 35, 1123, y + 80))
            xp, needed = _ratio(progress)
            capped = True if any(word.normalized == "max" for word in progress) else False if xp is not None else None
            if name:
                rows.append(LocationRow(name, rank, xp, needed, capped, (900, y + 25)))
        return tuple(rows)

    def analyze(self, png: bytes) -> LessonScreen:
        frame = decode_frame(png)
        words = tuple(self.startup.read(frame))
        # Modals take precedence over their dimmed underlying controls.
        if _has(words, "relationship rank up", (350, 580, 940, 660)):
            return LessonScreen("relationship_rank_up", dismiss_target=(1170, 650),
                                words=words, detail="Relationship rank-up screen verified")
        area_titles = [word for word in _within(words, (300, 60, 980, 300))
                       if word.normalized in {"area rank up", "location rank up"}
                       and word.confidence >= .9]
        if area_titles:
            # This conservative variant has synthetic coverage only. Do not
            # invent a dismissal point for an unknown tap-anywhere animation.
            controls = [word for word in _within(words, (360, 350, 920, 685))
                        if word.normalized == "confirm" and word.confidence >= .9]
            if len(area_titles) == 1 and len(controls) == 1 and _active_confirmation(frame, controls[0]):
                return LessonScreen("area_rank_up", dismiss_target=controls[0].center,
                                    words=words, detail="Area rank-up title and active Confirm verified")
            return LessonScreen("unknown", words=words, inspection_complete=False,
                                detail="Area rank-up needs a unique visible active Confirm control")
        if _has(words, "lesson report", (470, 110, 820, 169)):
            labels_present = (
                _has(words, "lesson location", (430, 175, 850, 219))
                and _has(words, "relationship points", (430, 256, 850, 305))
                and _has(words, "lesson reward", (430, 380, 850, 435))
            )
            names = _within(words, (430, 217, 850, 258))
            controls = [word for word in _within(words, (510, 515, 770, 600))
                        if word.normalized == "confirm"]
            if labels_present and names and len(controls) == 1 and _bright(frame, (420, 217, 435, 255)):
                return LessonScreen("receipt", room_name=_text(names),
                                    dismiss_target=controls[0].center, words=words,
                                    detail="Lesson Report and its rewards verified")
            return LessonScreen("unknown", words=words, inspection_complete=False,
                                detail="Lesson Report is loading or its contents are incomplete")
        if (_has(words, "location info", (460, 85, 820, 140))
                and _bright(frame, (310, 94, 450, 102))):
            if not (float(frame[87, 390:490].mean()) < 180
                    and float(frame[88, 390:490].mean()) > 235):
                return LessonScreen("unknown", words=words, inspection_complete=False,
                                    detail="The lesson confirmation is still moving or has an unsupported layout")
            _, name = _header_name(_within(words, (300, 150, 767, 209)), level=True)
            controls = [word for word in _within(words, (480, 510, 800, 595))
                        if word.normalized == "start lesson"]
            counts = _unique_matches(_within(words, (680, 475, 790, 524)), r"(\d+)\s*(?:→|->|>)\s*(\d+)")
            before, after = (int(counts[0][1]), int(counts[0][2])) if len(counts) == 1 else (None, None)
            # Portraits align to the right edge; there can be one to three. Detect
            # the actual leftmost occupied slot rather than assigning student names.
            students, complete = self._students(frame, 774, 156, slots=3, step=69,
                                               allow_leading_blank=True)
            return LessonScreen("confirm", tickets=before, tickets_after=after,
                                room_name=name, students=students,
                                start_target=controls[0].center if len(controls) == 1 else None,
                                inspection_complete=complete,
                                words=words)

        if (_has(words, "all locations", (440, 78, 830, 132))
                and _bright(frame, (350, 82, 500, 90))):
            # The opening animation translates the whole grid several pixels.
            # Its inset top edge is stable before interpreting small heart labels.
            if not (float(frame[169, 245:445].mean()) > 225
                    and float(frame[170, 245:445].mean()) < 225
                    and float(frame[171, 245:445].mean()) < 215):
                return LessonScreen("unknown", words=words, inspection_complete=False,
                                    detail="The room grid is still moving or has an unsupported layout")
            tickets, capacity = self._tickets(frame, words, (530, 132, 765, 170))
            cards = self._rooms(frame, words)
            return LessonScreen("rooms", tickets=tickets, ticket_capacity=capacity,
                                room_cards=cards, words=words,
                                inspection_complete=bool(cards) and all(card.inspection_complete for card in cards),
                                detail="No room cards recognized" if not cards else "")

        if (_has(words, "lesson", (85, 0, 230, 47))
                and _has(words, "location select", (620, 76, 900, 134))
                and _bright(frame, (1020, 91, 1060, 117))):
            tickets, capacity = self._tickets(frame, words, (40, 75, 290, 125))
            return LessonScreen("overview", tickets=tickets, ticket_capacity=capacity,
                                total_rank=_rank(_within(words, (875, 110, 1010, 133))),
                                location_rows=self._overview(words), words=words)

        if (_has(words, "select location", (85, 0, 330, 47))
                and _has(words, "all locations", (1050, 625, 1270, 705))
                and _has(words, "area rewards", (920, 166, 1080, 208))
                and _bright(frame, (1005, 91, 1200, 99))):
            tickets, capacity = self._tickets(frame, words, (40, 75, 290, 125))
            rank, name = _header_name(_within(words, (924, 87, 1235, 131)))
            progress = _within(words, (928, 130, 1245, 165))
            xp, needed = _ratio(progress)
            capped = True if any(word.normalized == "max" for word in progress) else False if xp is not None else None
            return LessonScreen("map", tickets=tickets, ticket_capacity=capacity,
                                location_name=name, location_rank=rank, location_xp=xp,
                                location_xp_to_next=needed, location_capped=capped, words=words)

        return LessonScreen("unknown", words=words, detail="No supported Lessons screen recognized")
