"""Observed assistant cards and filters, with no inferred stars or lender names.

The card's blue weapon-star display implies five base stars. Weapon stars are
reported separately and never override the user's base-stars-then-level policy.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import unicodedata

import cv2
import numpy as np

from .assault_policy import DAMAGE_TYPES, TeamMember
from .crafting_vision import bright, cyan
from .shop_vision import has, text_in

COLUMNS = (574, 685, 796, 907, 1018, 1129)
VIEWPORT = (566, 240, 1238, 518)
FILTER_TARGETS = dict(zip(DAMAGE_TYPES, ((291, 286), (525, 286), (760, 286), (995, 286))))


@dataclass(frozen=True)
class AssistantMetadata:
    bounds: tuple[int, int, int, int]
    level: int | None
    stars: int | None
    weapon_stars: int | None = None


@dataclass(frozen=True)
class AssistantCard:
    member: TeamMember
    bounds: tuple[int, int, int, int]
    target: tuple[int, int]
    selected: bool = False
    weapon_stars: int | None = None


@dataclass(frozen=True)
class AssistantPage:
    kind: str = "unknown"
    cards: tuple[AssistantCard, ...] = ()
    incomplete: tuple[tuple[int, int, int, int], ...] = ()
    unavailable: tuple[tuple[int, int, int, int], ...] = ()
    clipped: tuple[tuple[int, int, int, int], ...] = ()
    at_top: bool = False
    at_bottom: bool = False
    available: int | None = None
    scrollbar: tuple[int, int] | None = None
    filter_target: tuple[int, int] | None = None
    confirm_target: tuple[int, int] | None = None
    level_descending: bool = False
    # Complete name+lender rows establish overlap while portraits are clipped.
    rows: tuple[tuple[str, bool], ...] = ()
    words: tuple = ()


@dataclass(frozen=True)
class AssistantFilter:
    kind: str = "unknown"
    selected: frozenset[str] | None = None
    reset_target: tuple[int, int] | None = None
    confirm_target: tuple[int, int] | None = None


def _groups(indices):
    if not len(indices):
        return []
    return np.split(indices, np.where(np.diff(indices) > 1)[0] + 1)


def _card_bounds(frame):
    """Locate name plates; partial rows never masquerade as full cards."""
    scores = np.zeros(278)
    for x in COLUMNS:
        colors = frame[240:518, x+10:x+96].astype(int)
        mask = np.max(np.abs(colors - [102, 82, 67]), axis=2) < 8
        scores += (mask.mean(axis=1) > .4)
    rows = _groups(np.flatnonzero(scores >= 1) + 240)
    full, clipped = [], []
    for row in rows:
        if len(row) < 24:
            continue
        top = int(row[0]) - 102
        # Fixed card geometry; text occupies the 36px name plate, lender 26px.
        present = [i for i, x in enumerate(COLUMNS)
                   if (frame[max(240, top+104):min(518, top+135), x+12:x+95].max(axis=2) < 160).mean() > .5]
        for x in COLUMNS[:max(present)+1 if present else 0]:
            bounds = (x, top, x + 107, top + 171)
            (full if top >= 240 and top + 171 <= 518 else clipped).append(bounds)
    return tuple(full), tuple(clipped)


def _digit(words, cell, maximum):
    found = [word for word in words if cell * 180 <= word.center[1] < (cell+1) * 180]
    if len(found) != 1 or found[0].confidence < .8 or not re.fullmatch(r"\d{1,3}", found[0].text):
        return None
    value = int(found[0].text)
    return value if 1 <= value <= maximum else None


def read_assistant_metadata(frame, startup, bounds=None):
    """Read tightly cropped level/star digits in one local OCR batch."""
    bounds = _card_bounds(frame)[0] if bounds is None else tuple(bounds)
    if not bounds:
        return ()
    strip = np.full((len(bounds) * 360, 240, 3), 255, np.uint8)
    blue = []
    for index, (x, y, _, _) in enumerate(bounds):
        for part, region in enumerate(((x+38, y+12, x+57, y+32),
                                       (x+16, y+72, x+25, y+86))):
            x1, y1, x2, y2 = region
            crop = cv2.resize(frame[y1:y2, x1:x2], None, fx=5, fy=5)
            offset = index * 360 + part * 180 + 45
            strip[offset:offset+crop.shape[0], 70:70+crop.shape[1]] = crop
        star = cv2.cvtColor(frame[y+70:y+86, x+12:x+29], cv2.COLOR_BGR2HSV)
        blue.append(float(((star[:, :, 0] >= 85) & (star[:, :, 0] <= 115)
                           & (star[:, :, 1] > 100) & (star[:, :, 2] > 120)).mean()) > .15)
    words = startup.read(strip)
    result = []
    for index, box in enumerate(bounds):
        digit = _digit(words, index*2+1, 5)
        stars = 5 if blue[index] and digit in (1, 2, 3, 4) else digit if not blue[index] else None
        result.append(AssistantMetadata(box, _digit(words, index*2, 999), stars,
                                        digit if blue[index] and digit in (1, 2, 3, 4) else None))
    return tuple(result)


def _damage(frame, x, y):
    hsv = cv2.cvtColor(frame[y+85:y+88, x+35:x+65], cv2.COLOR_BGR2HSV)
    hue, sat, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    valid = (sat >= 100) & (value >= 70)
    ranges = {"explosive": (hue <= 12) | (hue >= 170),
              "piercing": (hue >= 17) & (hue <= 39),
              "mystic": (hue >= 91) & (hue <= 115),
              "sonic": (hue >= 126) & (hue <= 159)}
    hits = [name for name, mask in ranges.items() if (valid & mask).mean() >= .8]
    return hits[0] if len(hits) == 1 else None


def _offering_id(frame, words, name, x, y):
    # Never retain or log the lender's display name. Glyph pixels distinguish
    # duplicate student offerings without requiring reliable multilingual OCR.
    lender = [w for w in words if x+3 <= w.center[0] <= x+104 and y+143 <= w.center[1] <= y+167]
    if lender and all(w.confidence >= .9 for w in lender):
        text = "".join(w.text for w in sorted(lender, key=lambda w: w.box[0]))
        normalized = unicodedata.normalize("NFKC", text).casefold().strip()
        return hashlib.sha256(name.encode() + b"/ocr/" + normalized.encode()).hexdigest()
    crop = cv2.cvtColor(frame[y+146:y+165, x+10:x+97], cv2.COLOR_BGR2GRAY)
    glyphs = cv2.resize(crop, (64, 16), interpolation=cv2.INTER_AREA) < 170
    return hashlib.sha256(name.encode() + np.packbits(glyphs).tobytes()).hexdigest()


def _selected(frame, x, y):
    hsv = cv2.cvtColor(frame[y:y+168, x:x+5], cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 0] >= 24) & (hsv[:, :, 0] <= 45)
                  & (hsv[:, :, 1] > 170) & (hsv[:, :, 2] > 220)).mean()) > .25


def read_assistant_page(frame, words, *, startup=None, metadata=()):
    if (frame.shape[:2] != (720, 1280)
            or not has(words, "quick formation", (470, 62, 810, 120))
            or not bright(frame, (400, 70, 485, 108))
            or not (frame[137:175, 922:957].min(axis=2) > 240).mean() > .9
            or not has(words, "striker", (570, 185, 695, 232))
            or not cyan(frame, (1100, 562, 1225, 625))):
        return AssistantPage()
    counter = re.fullmatch(r"Assistant \(Available: (\d+)/(\d+)\)",
                           text_in(words, (945, 130, 1243, 183)).strip())
    striker = cv2.cvtColor(frame[194:225, 573:582], cv2.COLOR_BGR2HSV)
    if (not counter or not 0 <= int(counter[1]) <= int(counter[2]) <= 1
            or not ((striker[:, :, 0] >= 170) & (striker[:, :, 1] > 140)).mean() > .8):
        return AssistantPage()
    full, clipped = _card_bounds(frame)
    metadata = metadata or (read_assistant_metadata(frame, startup, full) if startup else ())
    by_bounds = {item.bounds: item for item in metadata}
    cards, incomplete, unavailable = [], [], []
    for bounds in full:
        x, y, _, _ = bounds
        name = text_in(words, (x+3, y+98, x+104, y+140)).strip()
        if "already" in name.lower() and "formation" in name.lower():
            unavailable.append(bounds)
            continue
        meta = by_bounds.get(bounds)
        damage = _damage(frame, x, y)
        if (meta is None or meta.level is None or meta.stars is None or damage is None
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 *.,'’()\-]+", name)
                or name.count("(") != name.count(")") or "formation" in name.lower()):
            incomplete.append(bounds)
            continue
        member = TeamMember(name, 0, "striker", damage, meta.stars, meta.level,
                            True, _offering_id(frame, words, name, x, y))
        cards.append(AssistantCard(member, bounds, (x+54, y+55), _selected(frame, x, y), meta.weapon_stars))
    track = frame[243:515, 1246:1251].mean(axis=(1, 2))
    runs = [run for run in _groups(np.flatnonzero((track > 110) & (track < 173)) + 243) if len(run) >= 8]
    scrollbar = (int(runs[0][0]), int(runs[0][-1])) if len(runs) == 1 else None
    descending = (text_in(words, (970, 189, 1027, 232)).strip() == "Lv."
                  and _template_match(frame[191:229, 1123:1191], "assault-assistant-level-descending.png"))
    rows = []
    for y in sorted({box[1] for box in full + clipped}):
        if y+100 < 240 or y+169 > 518:
            continue
        boxes = [box for box in full+clipped if box[1] == y]
        identities = []
        for x, _, _, _ in boxes:
            name = text_in(words, (x+3, y+98, x+104, y+140)).strip()
            if not name:
                break
            identities.append(_offering_id(frame, words, name, x, y))
        if len(identities) == len(boxes):
            key = hashlib.sha256(''.join(identities).encode()).hexdigest()
            rows.append((key, y >= 240 and y+171 <= 518))
    return AssistantPage("assistant", tuple(cards), tuple(incomplete), tuple(unavailable), clipped,
                         bool(scrollbar and scrollbar[0] <= 245), bool(scrollbar and scrollbar[1] >= 512),
                         int(counter[1]), scrollbar, (990, 208), (1168, 593), descending,
                         tuple(rows), tuple(words))


def read_assistant_filter(frame, words):
    if (not has(words, "display settings", (470, 60, 825, 112))
            or not bright(frame, (410, 66, 500, 108))
            or not has(words, "reset all", (1040, 136, 1208, 187))
            or not has(words, "atk attribute", (250, 193, 439, 242))
            or not has(words, "confirm", (656, 559, 881, 638))
            or not cyan(frame, (690, 566, 845, 626))):
        return AssistantFilter()
    selected, defaults = set(), 0
    for name, (x, y) in FILTER_TARGETS.items():
        hsv = cv2.cvtColor(frame[y-18:y+18, x-18:x+18], cv2.COLOR_BGR2HSV)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        if ((h >= 80) & (h <= 115) & (s > 140) & (v > 210)).mean() > .15:
            selected.add(name)
        elif ((h >= 85) & (h <= 120) & (s > 12) & (s < 65) & (v > 180) & (v < 245)).mean() > .2:
            defaults += 1
        elif ((s < 15) & (v > 245)).mean() < .85:
            return AssistantFilter()
    if defaults and (defaults != 4 or selected):
        return AssistantFilter()
    return AssistantFilter("assistant_filter", frozenset(DAMAGE_TYPES if defaults == 4 else selected),
                           (1110, 162), (762, 597))


def _template_match(crop, asset):
    template = cv2.imread(str(Path(__file__).parent / "assets" / asset))
    if template is None:
        return False
    return crop.shape == template.shape and float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)[0, 0]) >= .88


def _assistant_marker(frame, x, y, *, slot=False):
    asset = "assault-assistant-slot-marker.png" if slot else "assault-assistant-marker.png"
    return _template_match(frame[y-12:y+13, x-12:x+13], asset)


def verify_assistant_preview(frame, words, card, slot):
    """Verify selection and the exact offering before binding borrowed provenance."""
    if not 0 <= slot < 4 or not card.selected:
        return False
    name = text_in(words, (225, 209, 546, 254)).strip()
    damage = text_in(words, (271, 293, 350, 332)).strip().lower()
    return (name == card.member.student_id and damage == card.member.damage_type
            and _assistant_marker(frame, 119, 212)
            and _assistant_marker(frame, 103 + 90 * slot, 570, slot=True))


def verify_retained_assistant(frame, card, slot):
    """Verify a retained selection when reopening clears the inspection panel.

    The caller must separately verify the complete formation's student metadata
    and the selected card's exact offering ID. This checks that its sole borrowed
    badge remains in that same slot; it never infers a lender from a portrait.
    """
    return (frame.shape[:2] == (720, 1280) and card.selected and 0 <= slot < 4
            and [index for index in range(6)
                 if _assistant_marker(frame, 103 + 90*index, 570, slot=True)] == [slot])


def verify_owned_quick(frame, words, *, startup=None):
    """Require six occupied Quick Formation slots without borrowed badges."""
    if (frame.shape[:2] != (720, 1280)
            or not has(words, "quick formation", (470, 62, 810, 120))
            or not bright(frame, (400, 70, 485, 108))
            or not cyan(frame, (1100, 562, 1225, 625))):
        return False
    levels = [text_in(words, (28 + 90*slot, 558, 93 + 90*slot, 585)).strip() for slot in range(6)]
    if startup is not None and any(not re.fullmatch(r"Lv\.?\s*\d{1,3}", level, re.I) for level in levels):
        strip = np.full((600, 250, 3), 255, np.uint8)
        for slot in range(6):
            strip[slot*100:slot*100+84, 20:196] = cv2.resize(
                frame[561:582, 33+90*slot:77+90*slot], None, fx=4, fy=4)
        observations = startup.read(strip)
        levels = [text_in(observations, (0, slot*100, 250, (slot+1)*100)).strip() for slot in range(6)]
    for slot, level in enumerate(levels):
        if not re.fullmatch(r"Lv\.?\s*\d{1,3}", level, re.I):
            return False
        if _assistant_marker(frame, 103 + 90*slot, 570, slot=True):
            return False
    return True
