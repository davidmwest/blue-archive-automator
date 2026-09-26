"""Fill only observed empty Tactical Challenge attack slots.

The saved formation belongs to the player. An unread name or level is never an
empty slot, and the game's Auto button is not used because it replaces that
formation. All recognition stays local.
"""

from dataclasses import dataclass
import re

import cv2
import numpy as np

from .assault_vision import LEVEL_BOUNDS, NAME_BOUNDS
from .crafting_vision import bright, cyan, within
from .shop_vision import has, text_in
from .tactical_battles import Student
from .vision import decode_frame


def _name(words, bounds):
    found = within(words, bounds)
    if not found or any(word.confidence < .9 for word in found):
        return None
    name = text_in(found, bounds).strip()
    if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9 *.,'’()\-]+", name)
            or name.count('(') != name.count(')')):
        return None
    return name


def _level(words, bounds):
    found = within(words, bounds)
    if len(found) != 1 or found[0].confidence < .9:
        return None
    value = re.fullmatch(r'Lv\.?\s*(\d{1,3})', found[0].text.strip(), re.I)
    return int(value[1]) if value and int(value[1]) > 0 else None


def read_attack_slots(frame, words):
    """Return six observed students/blanks, or None when any slot is ambiguous."""
    if (frame.shape[:2] != (720, 1280)
            or not any(re.fullmatch(r'attack formation(?: [0-9])?', word.normalized)
                       for word in within(words, (90, 0, 410, 48)))
            or not bright(frame, (395, 4, 440, 37))
            or not has(words, 'quick formation', (1128, 185, 1270, 235))
            or not has(words, 'mobilize', (1090, 635, 1250, 705))):
        return None
    result = []
    for slot, (name_bounds, level_bounds) in enumerate(zip(NAME_BOUNDS, LEVEL_BOUNDS)):
        role = 'striker' if slot < 4 else 'special'
        name = _name(words, name_bounds)
        level = _level(words, level_bounds)
        # The game labels blanks by role, including the four invisible Striker
        # models. Never mistake an OCR omission or animation for an empty slot.
        empty = name == f'{role.upper()} Slot'
        if empty and level is None:
            result.append(None)
        elif name and not empty and level is not None:
            result.append(Student(name, role, level))
        else:
            return None
    identities = [student.student_id for student in result if student is not None]
    return tuple(result) if len(identities) == len(set(identities)) else None

COLUMNS = (562, 674, 785, 896, 1007, 1118)
SLOT_X = (30, 138, 245, 352, 459, 567)
FILTER_POINTS = ((291, 286), (526, 286), (761, 286), (995, 286),
                 (291, 431), (526, 431), (761, 431), (995, 431), (291, 496))


@dataclass(frozen=True)
class FormationEditor:
    kind: str = 'unknown'
    role: str | None = None
    empty: tuple[int, ...] = ()
    selected: tuple[bool, ...] = ()
    at_top: bool = False
    descending: bool = False
    all_filters: bool = False
    level_sort: bool = False


def _hsv_fraction(image, predicate):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return float(predicate(hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]).mean())


def read_editor(image, words):
    from .assault_assistant_vision import _template_match

    if image.shape[:2] != (720, 1280):
        return FormationEditor()
    if (has(words, 'display settings', (470, 55, 825, 115))
            and bright(image, (410, 66, 500, 108))
            and cyan(image, (701, 580, 810, 615))):
        if has(words, 'reset all', (1060, 140, 1190, 185)):
            checked = all(_hsv_fraction(image[y-18:y+18, x-18:x+18],
                lambda h, s, v: (h >= 85) & (h <= 120) & (s > 12) & (s < 65)
                & (v > 180) & (v < 245)) > .25 for x, y in FILTER_POINTS)
            return FormationEditor('filter', all_filters=checked)
        if has(words, 'lv', (303, 160, 365, 210)):
            selected = _hsv_fraction(image[177:191, 285:299],
                lambda h, s, v: (h > 85) & (h < 120) & (s > 80) & (v > 160)) > .6
            return FormationEditor('sort', level_sort=selected)
        return FormationEditor()
    if (not has(words, 'quick formation', (480, 63, 810, 119))
            or not bright(image, (400, 70, 485, 108))
            or not has(words, 'confirm', (1040, 559, 1220, 624))
            or not cyan(image, (1040, 570, 1218, 622))):
        return FormationEditor()
    striker = _hsv_fraction(image[165:174, 562:574],
                           lambda h, s, v: ((h > 170) | (h < 10)) & (s > 150) & (v > 120)) > .7
    special = _hsv_fraction(image[165:174, 685:698],
                           lambda h, s, v: (h > 90) & (h < 120) & (s > 180) & (v > 180)) > .7
    role = 'striker' if striker and not special else 'special' if special and not striker else None
    if role is None:
        return FormationEditor()
    empty = tuple(i for i, x in enumerate(SLOT_X)
                  if has(words, 'empty', (x, 570, x+103, 625)))
    selected = tuple(_hsv_fraction(image[210:340, x:x+5],
                     lambda h, s, v: (h > 24) & (h < 45) & (s > 170) & (v > 220)) > .25
                     for x in COLUMNS)
    bar = image[200:516, 1236].mean(axis=1)
    thumb = np.flatnonzero((bar >= 130) & (bar <= 175))
    top = len(thumb) >= 15 and int(thumb[0]) <= 3
    descending = (has(words, 'lv', (942, 139, 1040, 180))
                  and _template_match(image[141:179, 1112:1180],
                                      'assault-assistant-level-descending.png'))
    return FormationEditor('quick', role, empty, selected, top, descending)


def read_roster_levels(image, startup):
    """Cross-check two independently bounded level digit crops in one OCR batch."""
    strip = np.full((12 * 180, 300, 3), 255, np.uint8)
    for i, x in enumerate(COLUMNS):
        for part, (a, b, c, d, scale) in enumerate(((x+37, 212, x+66, 231, 4),
                                                  (x+38, 213, x+67, 232, 5))):
            crop = cv2.resize(image[b:d, a:c], None, fx=scale, fy=scale)
            y = (i * 2 + part) * 180 + 45
            strip[y:y+crop.shape[0], 70:70+crop.shape[1]] = crop
    words = startup.read(strip)
    levels = []
    for i in range(6):
        values = []
        for part in range(2):
            found = [w for w in words if (i*2+part)*180 <= w.center[1] < (i*2+part+1)*180]
            match = re.fullmatch(r'(\d{1,3})', found[0].text.strip()) if len(found) == 1 else None
            values.append(int(match[1]) if match and found[0].confidence >= .9 else None)
        levels.append(values[0] if values[0] == values[1] and values[0] else None)
    return tuple(levels)


def highest_available(image, words, editor, levels, occupied):
    """The first unselected student in a verified global descending roster.

    Six cards suffice: the role has at most four occupied slots. No lower card
    can displace an unreadable higher card; any such ambiguity stops selection.
    """
    if editor.kind != 'quick' or not editor.at_top or not editor.descending:
        return None
    previous = 1000
    for index, (x, level) in enumerate(zip(COLUMNS, levels)):
        if level is None or not 0 < level <= previous:
            return None
        previous = level
        if editor.selected[index]:
            continue
        name = _name(words, (x+8, 294, x+100, 345))
        if name is None or name in occupied:
            return None
        return Student(name, editor.role, level), (x+53, 271)
    return None


def unchanged_portraits(before, after, occupied):
    """Ignore changing selection outlines, lead badge, levels, and star glints."""
    for slot in occupied:
        x = SLOT_X[slot]
        a, b = before[584:618, x+20:x+65], after[584:618, x+20:x+65]
        if float(np.abs(a.astype(float)-b.astype(float)).mean()) > 3:
            return False
    return True


class TacticalFormationMixin:
    """Runner integration; the host provides fresh captures and guarded input."""

    def fill_attack_formation(self, frame):
        original = read_attack_slots(decode_frame(frame.capture.png), frame.screen.words)
        if original is None:
            self.fail('The attack formation has unreadable slots; no team changes were made')
        if all(student is not None for student in original):
            return frame
        return self._fill_attack_slots(frame, original)

    def _editor_wait(self, kind='quick', *, predicate=lambda editor: True, timeout=25):
        kinds = {kind} if isinstance(kind, str) else set(kind)
        end = self.clock() + timeout
        while self.clock() < end:
            frame = self.capture()
            editor = read_editor(decode_frame(frame.capture.png), frame.screen.words)
            if (editor.kind in kinds and predicate(editor)
                    and frame.capture.deadline - self.clock() >= 1):
                return frame, editor
            self.sleep(.7)
        self.fail('The Tactical Challenge formation editor could not be verified')

    def _roster_top(self, frame, editor):
        for _ in range(12):
            if editor.at_top:
                return frame, editor
            self.budget()
            if (not frame.capture.is_fresh(self.clock())
                    or self.device.foreground_package() != frame.capture.foreground):
                self.fail('The owned roster changed before scrolling')
            self.journal.record('intent', operation='swipe', detail='Return owned roster to highest levels',
                                start=(1080, 270), end=(1080, 480))
            if not self.device.swipe((1080, 270), (1080, 480), duration_ms=500,
                                     deadline=frame.capture.deadline, monotonic=self.clock):
                self.fail('Owned roster scrolling expired')
            self.actions += 1
            self.sleep(.7)
            frame, editor = self._editor_wait()
        self.fail('The top of the owned roster could not be verified')

    def _prepare_roster(self, frame, editor, role):
        if editor.role != role:
            self.tap(frame, (735, 160) if role == 'special' else (618, 160),
                     f'Show owned {role} students')
            frame, editor = self._editor_wait(predicate=lambda e: e.role == role)
        self.tap(frame, (978, 160), 'Open owned student display settings')
        frame, settings = self._editor_wait({'filter', 'sort'})
        if settings.kind != 'filter':
            self.tap(frame, (145, 154), 'Show owned roster filters')
            frame, _ = self._editor_wait('filter')
        self.tap(frame, (1110, 162), 'Clear all owned roster filters')
        frame, _ = self._editor_wait('filter', predicate=lambda e: e.all_filters)
        self.tap(frame, (145, 214), 'Choose owned roster sort')
        frame, _ = self._editor_wait('sort')
        self.tap(frame, (332, 184), 'Sort owned students by level')
        frame, _ = self._editor_wait('sort', predicate=lambda e: e.level_sort)
        self.tap(frame, (762, 597), 'Apply level sorting')
        frame, editor = self._editor_wait(predicate=lambda e: e.role == role)
        if not editor.descending:
            self.tap(frame, (1144, 160), 'Put highest-level owned students first')
            frame, editor = self._editor_wait(predicate=lambda e: e.role == role and e.descending)
        return self._roster_top(frame, editor)

    def _fill_attack_slots(self, frame, original):
        expected = list(original)
        self.tap(frame, (1203, 162), 'Fill only empty saved attack slots')
        frame, editor = self._editor_wait()
        if editor.empty != tuple(i for i, student in enumerate(expected) if student is None):
            self.fail('The saved attack formation changed before filling empty slots')
        added = []
        for role, slots in (('striker', range(4)), ('special', range(4, 6))):
            missing = [i for i in slots if expected[i] is None]
            if not missing:
                continue
            frame, editor = self._prepare_roster(frame, editor, role)
            for slot in missing:
                # Local OCR must leave enough of the captured-frame lease to tap.
                # Retry observation, never selection, when a slow OCR lease expires.
                choice = None
                for _ in range(3):
                    image = decode_frame(frame.capture.png)
                    levels = read_roster_levels(image, self.vision.startup)
                    choice = highest_available(image, frame.screen.words, editor, levels,
                        {student.student_id for student in expected if student is not None})
                    if frame.capture.deadline - self.clock() >= 1:
                        break
                    frame, editor = self._editor_wait(predicate=lambda e: e.role == role)
                else:
                    self.fail('Owned roster recognition exceeded the fresh-input time limit')
                if choice is None:
                    self.fail(f'The highest-level available {role} could not be verified')
                blanks = tuple(i for i, student in enumerate(expected) if student is None)
                if editor.empty != blanks or next((i for i in blanks if i in slots), None) != slot:
                    self.fail('The expected empty attack slot changed')
                student, target = choice
                card_index = next(i for i, x in enumerate(COLUMNS) if x+53 == target[0])
                occupied = tuple(i for i, student in enumerate(expected) if student is not None)
                self.tap(frame, target, f'Fill empty {role} slot with {student.student_id}, Lv.{student.level}')
                frame, editor = self._editor_wait(predicate=lambda e:
                    e.role == role and e.empty == tuple(i for i in blanks if i != slot)
                    and e.selected[card_index])
                if not unchanged_portraits(image, decode_frame(frame.capture.png), occupied):
                    self.fail('An occupied attack slot changed while filling a blank; no battle entered')
                expected[slot] = student
                added.append(dict(slot=slot, student=student.student_id, level=student.level))
        self.tap(frame, (1130, 595), 'Confirm the preserved attack team with filled blanks')
        formation = self.wait('formation')
        observed = read_attack_slots(decode_frame(formation.capture.png), formation.screen.words)
        if observed != tuple(expected):
            self.fail('The completed attack formation differs from the verified students; no battle entered')
        self.journal.record('tactical_formation_filled', students=added,
                            preserved=sum(student is not None for student in original))
        return formation
