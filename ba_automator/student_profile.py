"""Read a student's Basic Info page into plain fields; all coordinates are canonical.

The page shows the bond rank, rarity, level, combat type, stats, the four skill
levels, the unique weapon, and the three equipment slots plus exclusive gear.
Anything not read with confidence is left as None rather than guessed.
"""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import re

import cv2
import numpy as np

from .invitation_roster import profile_rarity
from .vision import decode_frame, decode_native_frame, native_game_region, read_game_words

SKILLS = (("ex", 725), ("basic", 833), ("enhanced", 938), ("sub", 1044))
GEAR_LEVELS = (711, 804, 898)
GEAR_TIERS = (696, 788, 881, 973)          # the fourth slot is exclusive gear
STATS = {"max hp": "hp", "maxhp": "hp", "atk": "atk", "def": "def", "healing": "healing"}
LEVEL = r"lv[.:]?\s*(\d{1,2})"
KINDS = {
    "class": ("Tank", "Dealer", "Healer", "Support", "T.S."),
    "attack": ("Explosive", "Piercing", "Mystic", "Sonic"),
    "position": ("FRONT", "MIDDLE", "BACK"),
    "armor": ("Light", "Heavy", "Special", "Elastic"),
}


def _in(words, bounds, *, confidence=.8):
    x1, y1, x2, y2 = bounds
    return sorted((w for w in words if w.confidence >= confidence
                   and x1 <= w.center[0] <= x2 and y1 <= w.center[1] <= y2),
                  key=lambda w: w.center[0])


def _text(words, bounds, **kwargs):
    return " ".join(w.text.strip() for w in _in(words, bounds, **kwargs))


def _number(pattern, text):
    match = re.fullmatch(pattern, text.strip(), re.I)
    return int(match[1]) if match else None


def _enlarged(png, startup, bounds, scale=3):
    x1, y1, x2, y2 = bounds
    crop = native_game_region(decode_native_frame(png), bounds)
    crop = cv2.resize(crop, ((x2 - x1) * scale, (y2 - y1) * scale), interpolation=cv2.INTER_CUBIC)
    return " ".join(w.text.strip() for w in sorted(startup.read(crop), key=lambda w: w.box[0])
                    if w.confidence >= .8)


def bond_rank(png, startup):
    """The number in the heart beside the name; full-page OCR misses it."""
    return _number(r"(\d{1,3})", _enlarged(png, startup, (28, 552, 80, 600)))


@lru_cache(maxsize=1)
def tier_templates():
    """White digit masks cut from badges of known tier; the T is left out."""
    folder = files("ba_automator").joinpath("assets", "gear_tiers")
    templates = {}
    for tier in range(1, 11):
        resource = folder.joinpath(f"T{tier}.png")
        if resource.is_file():
            image = cv2.imdecode(np.frombuffer(resource.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                templates[tier] = image
    return templates


def _tier_by_template(png, x):
    """The digit's template must clearly win; 3 and 8 are close otherwise."""
    crop = native_game_region(decode_native_frame(png), (x - 20, 610, x + 20, 640))
    mask = (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) > 200).astype(np.uint8) * 255
    scores = sorted(((float(cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED).max()), tier)
                     for tier, template in tier_templates().items()
                     if template.shape[0] <= mask.shape[0] and template.shape[1] <= mask.shape[1]),
                    reverse=True)
    if len(scores) >= 2 and scores[0][0] >= .75 and scores[0][0] - scores[1][0] >= .08:
        return scores[0][1]
    return None


def gear_tiers(png, startup, words):
    """Confident page OCR, then an enlarged read, then a clear template match."""
    tiers = []
    for x in GEAR_TIERS:
        bounds = (x - 16, 614, x + 16, 636)
        tier = None
        for text in (_text(words, bounds, confidence=.9), lambda: _enlarged(png, startup, bounds, scale=4)):
            text = text() if callable(text) else text
            tier = _number(r"T\s*(\d{1,2})", (text or "").replace(" ", ""))
            if tier is not None and 1 <= tier <= 10:
                break
            tier = None
        tiers.append(tier if tier is not None else _tier_by_template(png, x))
    return tiers


STAR_AREA = 135   # blue pixels in one weapon star at 1280x720


def weapon_stars(frame):
    """Blue stars on the weapon card. Neighbouring stars touch, so count by area."""
    hsv = cv2.cvtColor(frame[495:532, 1020:1095], cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (85, 70, 150), (115, 255, 255))
    count, _, stats, _ = cv2.connectedComponentsWithStats(blue)
    area = sum(int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count)
               if stats[index, cv2.CC_STAT_AREA] >= 60)
    stars = round(area / STAR_AREA)
    return stars if 1 <= stars <= 4 and abs(area - stars * STAR_AREA) <= 25 * stars else None


def _kind(words, bounds, names):
    """A known type name; an icon read as text beside it is ignored."""
    found = [name for name in names
             if any(w.text.strip().lower() == name.lower() for w in _in(words, bounds))]
    return found[0] if len(found) == 1 else None


def parse_basic_info(png, startup, words=None):
    frame = decode_frame(png)
    words = read_game_words(png, startup) if words is None else words
    # The heart beside the name is sometimes read as a stray symbol before it.
    name = re.sub(r"^[^A-Za-z]+", "", _text(words, (70, 545, 330, 592), confidence=.85)) or None
    level = _number(LEVEL, _text(words, (25, 588, 110, 620)))
    exp = re.fullmatch(r"(\d+)\s*/\s*(\d+)", _text(words, (150, 588, 420, 620)))
    skills = {}
    for key, x in SKILLS:
        text = _text(words, (x - 40, 395, x + 40, 430))
        skills[key] = "MAX" if text.upper() == "MAX" else _number(LEVEL, text)
    stats = {}
    for word in _in(words, (670, 222, 1100, 296)):
        match = re.fullmatch(r"([A-Za-z ]+?)\s*(\d+)", word.text.strip())
        if match and match[1].strip().lower() in STATS:
            stats[STATS[match[1].strip().lower()]] = int(match[2])
    for label in _in(words, (670, 222, 1100, 296)):
        key = STATS.get(label.text.strip().lower())
        value = next((w for w in _in(words, (label.box[2] - 5, label.box[1] - 6, label.box[2] + 90, label.box[3] + 6))
                      if w.text.strip().isdigit()), None)
        if key and value and key not in stats:
            stats[key] = int(value.text.strip())
    weapon_level = _number(LEVEL, _text(words, (765, 445, 845, 485)))
    gear = []
    tiers = gear_tiers(png, startup, words)
    for slot, x in enumerate(GEAR_LEVELS):
        bounds = (x - 25, 552, x + 25, 582)
        gear_level = (_number(LEVEL, _text(words, bounds))
                      or _number(LEVEL, _enlarged(png, startup, (x - 29, 548, x + 29, 586), scale=2)))
        gear.append({"tier": tiers[slot], "level": gear_level})
    empty_exclusive = bool(_in(words, (950, 575, 1060, 615)) and
                           _text(words, (950, 575, 1060, 615)).upper() == "EMPTY")
    return {
        "name": name,
        "bond": bond_rank(png, startup),
        "stars": profile_rarity(frame, words, name) if name else None,
        "role": (_text(words, (330, 558, 420, 596)) or None),
        "level": level,
        "exp": [int(exp[1]), int(exp[2])] if exp else None,
        "class": _kind(words, (60, 625, 170, 660), KINDS["class"]),
        "attack": _kind(words, (180, 625, 300, 660), KINDS["attack"]),
        "position": _kind(words, (50, 660, 170, 698), KINDS["position"]),
        "armor": _kind(words, (180, 660, 300, 698), KINDS["armor"]),
        "stats": stats,
        "skills": skills,
        "weapon": None if weapon_level is None else {
            "type": _text(words, (690, 475, 750, 512)) or None, "level": weapon_level,
            "stars": weapon_stars(frame)},
        "gear": gear,
        "exclusive_gear_tier": None if empty_exclusive else tiers[3],
    }
