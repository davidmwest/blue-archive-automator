"""Observed Total Assault battle HUD; cut-ins and unreadable results stay unknown."""
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
import re

import cv2
import numpy as np

from .assault_vision import AssaultScreen
from .crafting_vision import bright, cyan, yellow
from .shop_vision import has, text_in


@dataclass(frozen=True)
class BattleScreen(AssaultScreen):
    remaining_seconds: float | None = None
    elapsed_seconds: float | None = None
    auto_on: bool | None = None
    won: bool | None = None
    damage_by_student: dict[str, int] = field(default_factory=dict)
    surviving_striker_ids: tuple[str, ...] | None = None
    mock: bool | None = None
    boss_hp: int | None = None
    boss_max_hp: int | None = None
    auto_target: tuple[int, int] | None = None
    damage_target: tuple[int, int] | None = None


def _clock(value):
    match = re.fullmatch(r"(\d{2}):([0-5]\d)\.(\d{3})", value)
    if not match or int(match[1]) > 30:
        return None
    return int(match[1]) * 60 + int(match[2]) + int(match[3]) / 1000


def battle_clock(words):
    return _clock(text_in(words, (1065, 10, 1210, 59)).strip())


def damage_report(words):
    """Read six name/value columns; unreadable damage is never treated as zero."""
    damage = {}
    for index in range(6):
        left, right = 340 + index * 98, 438 + index * 98
        name = text_in(words, (left, 566, right, 625)).strip()
        value = text_in(words, (left, 170, right + 4, 503)).strip()
        if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .,'’()\-]+", name)
                or name.count("(") != name.count(")")
                or not re.fullmatch(r"\d{1,12}", value) or name in damage):
            return {}
        damage[name] = int(value)
    return damage


def auto_state(frame, words):
    if not has(words, "auto", (1160, 652, 1270, 703)):
        return None
    if yellow(frame, (1178, 658, 1249, 696)):
        return True
    crop = frame[658:696, 1178:1249]
    light = np.min(crop, axis=2) >= 185
    neutral = np.max(crop, axis=2).astype(int) - np.min(crop, axis=2).astype(int) <= 35
    return False if float((light & neutral).mean()) >= .45 else None


@lru_cache(maxsize=1)
def _damage_icon():
    data = files("ba_automator").joinpath("assets/assault_damage_report.png").read_bytes()
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def damage_control(frame):
    """Locate the observed report glyph, not an arbitrary white result button."""
    left, top, right, bottom = 280, 625, 1100, 705
    sample = frame[top:bottom, left:right]
    template = _damage_icon()
    scores = cv2.matchTemplate(sample, template, cv2.TM_SQDIFF_NORMED)
    error, _, (x, y), _ = cv2.minMaxLoc(scores)
    if error > .015:
        return None
    # Several adjacent matches are the same glyph; two separated controls are
    # ambiguous and must not authorize a tap.
    hits_y, hits_x = np.where(scores <= .015)
    if np.any(np.abs(hits_x - x) > 20) or np.any(np.abs(hits_y - y) > 20):
        return None
    return left + x + template.shape[1] // 2, top + y + template.shape[0] // 2


def _neutral_complete_heading(frame):
    crop = frame[15:66, 50:470]
    light = np.min(crop, axis=2) >= 185
    neutral = np.max(crop, axis=2).astype(int) - np.min(crop, axis=2).astype(int) <= 25
    return float((light & neutral).mean()) >= .12


def classify_battle(frame, words):
    words = tuple(words)
    if (has(words, "damage report", (490, 90, 785, 154))
            and bright(frame, (400, 108, 480, 140))):
        return BattleScreen("damage", words, target=(932, 122),
                            damage_by_student=damage_report(words))
    elapsed = _clock(text_in(words, (1130, 18, 1260, 65)).strip())
    if (has(words, "battle complete", (20, 0, 500, 90))
            and yellow(frame, (50, 15, 470, 66))
            and has(words, "ranking point", (650, 15, 850, 67))
            and has(words, "time", (1030, 15, 1130, 67))
            and elapsed is not None
            and has(words, "striker", (20, 560, 150, 609))
            and has(words, "special", (450, 560, 570, 609))
            and has(words, "confirm", (1090, 630, 1245, 694))
            and cyan(frame, (1130, 641, 1228, 691))):
        # The live win capture has a gold Battle Complete heading and a cyan
        # lower-right Confirm. The BAAS author's separate English result layouts
        # place defeat Confirm at the center instead. Require the complete win
        # conjunction; do not infer survival from the decorative student models.
        # https://github.com/pur1fying/blue_archive_auto_script/blob/master/src/images/Global_en-us/x_y_range/total_assault.py
        return BattleScreen("result", words, elapsed_seconds=elapsed, won=True,
                            target=(1170, 665), confirm_target=(1170, 665),
                            damage_target=(1055, 665) if bright(frame, (1040, 641, 1084, 683)) else None)
    if (has(words, "battle complete", (20, 0, 500, 90))
            and not yellow(frame, (50, 15, 470, 66))
            and _neutral_complete_heading(frame)
            and has(words, "striker", (20, 560, 150, 609))
            and has(words, "special", (450, 560, 570, 609))
            and has(words, "confirm", (577, 636, 700, 683))
            and cyan(frame, (577, 636, 700, 683))
            and not has(words, "confirm", (1090, 630, 1245, 694))):
        # Historical BAAS English loss recognition puts Confirm at the center;
        # the current live victory fixture puts it at the lower right. This
        # stricter loss conjunction is tested offline until a defeat is captured.
        # A loss may only lead to reading damage and another free mock, never to
        # a paid entry. Missing/ambiguous controls remain unknown.
        report = damage_control(frame)
        if report is not None:
            return BattleScreen("result", words, elapsed_seconds=elapsed, won=False,
                                target=(638, 659), confirm_target=(638, 659),
                                damage_target=report)
    clock = battle_clock(words)
    hp_text = text_in(words, (560, 39, 820, 67)).strip()
    hp = re.fullmatch(r"([\d,]+)/([\d,]+)", hp_text)
    if clock is None or not hp:
        return BattleScreen("unknown", words)
    current, maximum = (int(value.replace(",", "")) for value in hp.groups())
    if not 0 <= current <= maximum or maximum == 0:
        return BattleScreen("unknown", words)
    # The timer alone is insufficient: this is the raid boss HUD, with its HP
    # ratio, boss label, and name in their recorded positions.
    if not has(words, "battle boss", (315, 63, 460, 110)):
        return BattleScreen("unknown", words)
    boss = text_in(words, (805, 7, 1038, 43)).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 '\-]+", boss):
        return BattleScreen("unknown", words)
    auto = auto_state(frame, words)
    return BattleScreen("battle", words, boss=boss, remaining_seconds=clock,
                        auto_on=auto, auto_target=(1214, 677) if auto is False else None,
                        mock=True if has(words, "mock battle", (0, 5, 165, 65)) else None,
                        boss_hp=current, boss_max_hp=maximum)
