"""Local, fixed-layout Total Assault navigation and formation recognition.

Each returned input target is backed by text and an undimmed control in the
current frame. Missing team metadata leaves the entire team unverified.
"""
from dataclasses import dataclass
import re

import cv2
import numpy as np

from .assault_policy import DIFFICULTIES, TeamMember, validate_team
from .crafting_vision import bright, cyan, within, yellow
from .shop_vision import classify_shop, has, text_in
from .vision import Word, decode_frame


@dataclass(frozen=True)
class AssaultStage:
    difficulty: str
    boss: str
    target: tuple[int, int] | None
    locked: bool | None = None


@dataclass(frozen=True)
class AssaultScreen:
    kind: str
    words: tuple[Word, ...] = ()
    boss: str | None = None
    event_period: str | None = None
    tickets: int | None = None
    difficulty: str | None = None
    stages: tuple[AssaultStage, ...] = ()
    team: tuple[TeamMember, ...] = ()
    target: tuple[int, int] | None = None
    mock_target: tuple[int, int] | None = None
    enter_target: tuple[int, int] | None = None
    sweep_target: tuple[int, int] | None = None
    sweep_max_target: tuple[int, int] | None = None
    quick_target: tuple[int, int] | None = None
    auto_target: tuple[int, int] | None = None
    confirm_target: tuple[int, int] | None = None
    assistant_target: tuple[int, int] | None = None
    count: int | None = None
    after_tickets: int | None = None
    items: tuple = ()
    credit_fee: int | None = None
    assistant_level: int | None = None
    assistant_stars: int | None = None


def _difficulty(text):
    value = re.sub(r"[^a-z]", "", text.lower())
    return next((tier for tier in DIFFICULTIES if tier.replace("_", "") == value), None)


def _period(words):
    dates = []
    for bounds in ((1135, 80, 1275, 112), (1135, 113, 1275, 140)):
        value = text_in(words, bounds).strip().lstrip("- ")
        if not re.fullmatch(r"\d{2}/\d{2} \d{2}:\d{2}", value):
            return None
        dates.append(value)
    return " – ".join(dates)


def _projection(words, bounds):
    value = text_in(words, bounds).replace(" ", "")
    match = re.fullmatch(r"(\d+)→(\d+)", value)
    return tuple(map(int, match.groups())) if match else None


def _integer(words, bounds):
    value = text_in(words, bounds).strip()
    return int(value) if re.fullmatch(r"\d{1,3}", value) else None


# The small star is a number, not a row of repeated star glyphs. Reading just the
# number avoids the star outline being mistaken for a Chinese character by OCR.
STAR_DIGITS = ((222, 530, 236, 544), (446, 530, 460, 544),
               (670, 530, 684, 544), (894, 530, 908, 544),
               (428, 661, 442, 675), (776, 661, 790, 675))
NAME_BOUNDS = ((263, 517, 395, 572), (487, 517, 619, 572),
               (711, 517, 843, 572), (935, 517, 1067, 572),
               (470, 651, 607, 700), (816, 651, 954, 700))
LEVEL_BOUNDS = ((204, 545, 261, 574), (428, 545, 485, 574),
                (652, 545, 709, 574), (876, 545, 933, 574),
                (407, 677, 465, 704), (755, 677, 813, 704))
DAMAGE_POINTS = ((307, 505), (531, 505), (755, 505), (979, 505),
                 (446, 635), (794, 635))


def formation_stars(frame, startup):
    """Read all six enlarged star digits in one bounded local OCR invocation."""
    strip = np.full((130, 1020, 3), 255, np.uint8)
    colors = []
    for index, (x1, y1, x2, y2) in enumerate(STAR_DIGITS):
        color = cv2.cvtColor(frame[y1-8:y2+5, x1-8:x2+6], cv2.COLOR_BGR2HSV)
        blue = ((color[:, :, 0] >= 85) & (color[:, :, 0] <= 115)
                & (color[:, :, 1] >= 70) & (color[:, :, 2] >= 100))
        gold = ((color[:, :, 0] >= 15) & (color[:, :, 0] <= 40)
                & (color[:, :, 1] >= 100) & (color[:, :, 2] >= 100))
        equipped = float(blue.mean()) >= .15 and float(gold.mean()) < .025
        colors.append((equipped, float(gold.mean())))
        # Blue UE digits need no OCR. Their different color can also cause the
        # text detector to join the entire strip into an unreadable word.
        if not equipped:
            crop = cv2.resize(frame[y1:y2, x1:x2], (56, 56))
            strip[35:91, index * 170 + 50:index * 170 + 106] = crop
    digits = startup.read(strip)
    stars = []
    for index, (equipped, gold_fraction) in enumerate(colors):
        # Equipped students cycle between the gold base rarity and a blue UE
        # star count. Unique equipment is unlocked at base rarity five, so the
        # blue digit must not change the team fingerprint or assistant ranking.
        if equipped:
            stars.append(5)
            continue
        found = [word for word in digits if index * 170 <= word.center[0] < (index + 1) * 170]
        if (len(found) != 1 or found[0].confidence < .75
                or not re.fullmatch(r"[1-5]", found[0].text)
                or gold_fraction < .1):
            return ()
        stars.append(int(found[0].text))
    return tuple(stars)


def damage_type(frame, point):
    """Read the attack-color band, never the adjacent armor-color band."""
    x, y = point
    hsv = cv2.cvtColor(frame[y-4:y+5, x-2:x+3], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    valid = (sat >= 100) & (val >= 70)
    masks = {"explosive": (hue <= 12) | (hue >= 170),
             "piercing": (hue >= 17) & (hue <= 39),
             "mystic": (hue >= 91) & (hue <= 115),
             "sonic": (hue >= 126) & (hue <= 159)}
    hits = [kind for kind, mask in masks.items() if float((valid & mask).mean()) >= .8]
    return hits[0] if len(hits) == 1 else None


def read_formation(frame, words, *, stars=(), startup=None):
    """Return a complete owned team, or no team when any field is unreadable.

    Assistant provenance is not inferred from a portrait or a student name. The
    runner must bind its observed lender identity before qualifying a borrowed
    team; the ordinary formation screen alone cannot establish that identity.
    """
    if not stars and startup is not None:
        stars = formation_stars(frame, startup)
    if len(stars) != 6:
        return ()
    team = []
    for slot in range(6):
        name = text_in(words, NAME_BOUNDS[slot]).strip()
        level = re.fullmatch(r"Lv[.:]?\s*(\d{1,3})", text_in(words, LEVEL_BOUNDS[slot]).strip())
        damage = damage_type(frame, DAMAGE_POINTS[slot])
        if (not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .,'’()\-]+", name)
                or name.count("(") != name.count(")") or "slot" in name.lower()
                or level is None or damage is None):
            return ()
        try:
            team.append(TeamMember(name, slot, "striker" if slot < 4 else "special",
                                   damage, stars[slot], int(level[1])))
        except ValueError:
            return ()
    try:
        return validate_team(team)
    except ValueError:
        return ()


def classify_assault(frame, words, *, home=False, stars=()):
    words = tuple(words)
    unknown = AssaultScreen("unknown", words)
    from .assault_notices import classify_notice
    notice = classify_notice(frame, words)
    if notice is not None:
        return notice
    receipt = classify_shop(frame, words)
    if receipt.kind == "receipt":
        return AssaultScreen("receipt", words, target=receipt.target, items=receipt.items)
    if home:
        return AssaultScreen("home", words)
    if (has(words, "campaign", (80, 0, 350, 50)) and bright(frame, (320, 5, 390, 32))
            and has(words, "total assault", (815, 420, 972, 483))):
        return AssaultScreen("campaign", words, target=(906, 456))
    if (has(words, "quick formation", (470, 62, 810, 120))
            and bright(frame, (400, 70, 485, 108))
            and has(words, "my students", (610, 132, 850, 185))
            and has(words, "confirm", (1065, 550, 1245, 631))
            and cyan(frame, (1100, 562, 1225, 625))):
        auto = ((623, 593) if has(words, "auto", (570, 548, 670, 637))
                and cyan(frame, (595, 563, 651, 622)) else None)
        assistant = next((word for word in within(words, (930, 130, 1240, 183))
                          if re.fullmatch(r"Assistant \(Available: \d+/\d+\)", word.text)), None)
        return AssaultScreen("quick", words, auto_target=auto, confirm_target=(1168, 593),
                             target=(1168, 593), assistant_target=(1083, 156) if assistant else None)
    if (has(words, "room info", (480, 108, 805, 165))
            and bright(frame, (405, 122, 480, 152))
            and has(words, "expected rewards", (290, 455, 620, 505))):
        difficulty = _difficulty(text_in(words, (143, 185, 340, 220)))
        boss = text_in(words, (143, 219, 590, 263)).strip()
        entry = _projection(words, (1035, 466, 1145, 514))
        mock = ((803, 545) if has(words, "mock battle", (720, 504, 890, 583))
                and cyan(frame, (748, 515, 866, 574)) else None)
        enter = ((1010, 545) if has(words, "enter", (900, 505, 1135, 583))
                 and yellow(frame, (950, 515, 1100, 574))
                 and entry and entry[0] - entry[1] == 1 else None)
        projected = _projection(words, (1000, 316, 1100, 366))
        count = _integer(words, (900, 267, 966, 314))
        sweep = ((940, 385) if has(words, "start sweep", (780, 350, 1100, 420))
                 and cyan(frame, (806, 360, 1070, 411)) and projected and count
                 and projected[0] - projected[1] == count else None)
        sweep_max = ((1085, 290) if sweep and projected[0] > count
                     and has(words, "max", (1045, 264, 1125, 314))
                     and bright(frame, (1055, 268, 1117, 311)) else None)
        # After the final ticket the game displays 0→- instead of a numeric
        # projection. Read this explicit zero; never treat missing OCR as zero.
        empty_budget = (count == 0 and entry is None and enter is None and sweep is None
                        and text_in(words, (1035, 466, 1145, 514)).replace(" ", "") == "0→-")
        if not difficulty or not boss or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 '\-]+", boss):
            return unknown
        return AssaultScreen("detail", words, boss=boss, difficulty=difficulty,
                             event_period=_period(words), tickets=entry[0] if entry else 0 if empty_budget else None,
                             mock_target=mock, enter_target=enter, sweep_target=sweep,
                             sweep_max_target=sweep_max,
                             count=count, after_tickets=projected[1] if projected else None)
    # Formation title and menu title remain readable under overlays; the bright
    # header and active button color prevent those background labels authorizing input.
    if (has(words, "total assault formation", (80, 0, 470, 50))
            and bright(frame, (325, 0, 392, 40))
            and has(words, "quick formation", (1120, 201, 1275, 245))
            and has(words, "mobilize", (1060, 615, 1270, 696))):
        team = read_formation(frame, words, stars=stars)
        return AssaultScreen("formation", words, team=team, quick_target=(1205, 182),
                             target=(1177, 659) if team and yellow(frame, (1100, 626, 1240, 690)) else None)
    if (has(words, "battle select", (630, 75, 910, 143))
            and has(words, "total assault ticket", (905, 78, 1110, 117))
            and bright(frame, (330, 5, 390, 32))):
        counter = re.fullmatch(r"(\d+)/(\d+)", text_in(words, (925, 111, 1005, 143)).strip())
        if not counter or not 0 <= int(counter[1]) <= int(counter[2]) <= 99:
            return unknown
        stages = []
        difficulty_words = [word for word in within(words, (668, 150, 1025, 570))
                            if _difficulty(word.text)]
        difficulty_words.sort(key=lambda word: word.center[1])
        for index, word in enumerate(difficulty_words):
            difficulty = _difficulty(word.text)
            if not difficulty:
                continue
            y = word.center[1]
            boss = text_in(words, (668, y+18, 1045, y+49)).strip()
            if not boss or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 '\-]+", boss):
                continue
            entered = (has(words, "enter", (1080, y+8, 1240, y+65))
                       and yellow(frame, (1095, y+10, 1210, y+65)))
            # Locked rows put the unlock explanation across the artwork, not
            # in the right-hand Enter button. Bind that exact explanation to
            # this row so a neighboring locked tier cannot change its status.
            bottom = min(y + 112, difficulty_words[index + 1].center[1] - 18
                         if index + 1 < len(difficulty_words) else 620)
            lock_words = re.sub(r"\s+", " ", text_in(words, (660, y+18, 1250, bottom)).lower())
            explicit_lock = bool(re.search(
                r"(?:^| )unlocks from clearing the lower difficulty\.?(?:$| )", lock_words))
            button_lock = text_in(words, (1050, y, 1240, min(y+82, bottom))).strip().lower() == "locked"
            locked = None if entered and (explicit_lock or button_lock) else (
                False if entered else True if explicit_lock or button_lock else None)
            target = (1157, y+38) if entered and locked is False else None
            stages.append(AssaultStage(difficulty, boss, target, locked))
        if stages:
            return AssaultScreen("menu", words, tickets=int(counter[1]), event_period=_period(words),
                                 boss=stages[0].boss if len({stage.boss for stage in stages}) == 1 else None,
                                 stages=tuple(stages))
    return unknown


class AssaultVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        home = {"home_left", "home_right"} <= self.startup.matches(frame).keys()
        screen = classify_assault(frame, words, home=home)
        if screen.kind == "formation" and not any(w.normalized == "empty" for w in words):
            screen = classify_assault(frame, words, stars=formation_stars(frame, self.startup))
        if screen.kind == "unknown":
            # Lazy import keeps the navigation screen contract shared without a
            # module cycle, and reuses the same local OCR observation.
            from .assault_battle_vision import classify_battle
            screen = classify_battle(frame, words)
        return screen
