"""Strict current-season modal recognition, ahead of dimmed navigation text."""
from functools import lru_cache
from pathlib import Path
import re

import cv2

from .assault_vision import AssaultScreen, _difficulty, _projection
from .crafting_vision import bright, cyan, yellow
from .shop_vision import has, receipt_items, text_in


@lru_cache(maxsize=4)
def _asset(name):
    return cv2.imread(str(Path(__file__).parent / "assets" / name))


def _matches(frame, bounds, asset):
    x1, y1, x2, y2 = bounds
    crop = frame[y1:y2, x1:x2]
    template = _asset(asset)
    return (template is not None and crop.shape == template.shape
            and float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)[0, 0]) >= .92)


def classify_notice(frame, words):
    """Return a recognized modal, or None without exposing a guessed input."""
    if frame.shape[:2] != (720, 1280):
        return None
    if has(words, "sweep complete", (450, 65, 840, 130)):
        active = (has(words, "final", (185, 437, 300, 496))
                  and has(words, "rewards earned", (185, 476, 370, 520))
                  and bright(frame, (783, 80, 1015, 119))
                  and has(words, "confirm", (550, 549, 729, 612))
                  and cyan(frame, (537, 553, 744, 613)))
        # The shared receipt reader inspects every Final-row card, including
        # quantities missing from full-screen OCR. Per-sweep rows are not loot.
        return AssaultScreen("receipt", tuple(words),
                             target=(640, 583) if active else None,
                             confirm_target=(640, 583) if active else None)
    sweep_body = text_in(words, (390, 277, 886, 348)).strip()
    if (has(words, "notice", (575, 136, 705, 190))
            and "total assault ticket" in sweep_body.lower() and "sweep" in sweep_body.lower()):
        unknown = AssaultScreen("sweep_confirm", tuple(words))
        request = re.fullmatch(r"Use (\d+) Total Assault Ticket to Sweep (\d+) time\(s\)\?", sweep_body)
        points = re.fullmatch(r"Obtain \d+ Rank Points via Sweep\.",
                              text_in(words, (390, 348, 886, 394)).strip())
        # The modal leaves these specific Room Info fields visible. They bind
        # the confirmation to the selected tier and observed ticket projection;
        # no background control itself authorizes an input.
        difficulty = _difficulty(text_in(words, (143, 185, 340, 220)))
        projection = _projection(words, (1000, 316, 1100, 366))
        if (not request or not points or difficulty is None or projection is None
                or not 1 <= int(request[1]) == int(request[2]) <= 99
                or projection[0] - projection[1] != int(request[1]) or projection[1] < 0
                or not has(words, "room info", (160, 110, 304, 157))
                or not bright(frame, (370, 212, 413, 271))
                or not has(words, "cancel", (451, 477, 579, 533))
                or not has(words, "confirm", (692, 475, 842, 533))
                or not cyan(frame, (666, 481, 870, 532))):
            return unknown
        return AssaultScreen("sweep_confirm", tuple(words), difficulty=difficulty,
                             count=int(request[1]), tickets=projection[0], after_tickets=projection[1],
                             target=(765, 503), confirm_target=(765, 503))
    if has(words, "best season record reached", (405, 135, 875, 194)):
        active = (has(words, "best rank points", (375, 367, 581, 414))
                  and has(words, "total season points", (375, 415, 586, 452))
                  and bright(frame, (378, 478, 426, 574))
                  and has(words, "confirm", (565, 500, 716, 553))
                  and cyan(frame, (553, 503, 733, 553)))
        return AssaultScreen("season_record", tuple(words),
                             target=(640, 530) if active else None,
                             confirm_target=(640, 530) if active else None)
    if has(words, "reward acquired", (300, 120, 980, 210)):
        items = receipt_items(words)
        active = (yellow(frame, (375, 134, 901, 182))
                  and has(words, "go to lobby", (400, 625, 610, 690))
                  and cyan(frame, (410, 626, 607, 690))
                  and has(words, "confirm", (692, 625, 853, 690))
                  and yellow(frame, (675, 630, 862, 690)))
        if active and items and all(item['quantity'] > 0 for item in items):
            return AssaultScreen("receipt", tuple(words), target=(771, 660),
                                 confirm_target=(771, 660), items=items)
        # Preserve the shared Touch to Continue receipt layout when this is
        # another kind of reward page. Never let its dimmed battle result win.
        if has(words, "go to lobby", (400, 625, 610, 690)):
            return AssaultScreen("receipt", tuple(words))
    if (has(words, "notice", (575, 136, 705, 190))
            and has(words, "use total assault ticket to enter", (430, 325, 850, 375))):
        unknown = AssaultScreen("entry_confirm", tuple(words))
        difficulty = _difficulty(text_in(words, (532, 244, 751, 296)))
        projection = _projection(words, (810, 436, 884, 477))
        if (difficulty is None or projection is None
                or not 1 <= projection[0] <= 99 or projection[1] != projection[0]-1
                or not bright(frame, (373, 205, 420, 310))
                or not has(words, "cancel", (451, 477, 579, 528))
                or not has(words, "confirm", (692, 475, 842, 529))
                or not yellow(frame, (660, 478, 871, 534))):
            return unknown
        return AssaultScreen("entry_confirm", tuple(words), difficulty=difficulty,
                             tickets=projection[0], after_tickets=projection[1], count=1,
                             target=(767, 503), confirm_target=(767, 503))
    if (has(words, "using an assistant", (480, 140, 800, 185))
            and has(words, "would you like to borrow an assistant", (400, 230, 890, 275))):
        unknown = AssaultScreen("assistant_confirm", tuple(words))
        level = re.fullmatch(r"Lv[.:]?\s*(\d{1,3})", text_in(words, (592, 303, 649, 333)).strip())
        stars = text_in(words, (588, 369, 615, 397)).strip()
        if not stars and _matches(frame, (588, 369, 615, 397), "assault-assistant-fee-five-stars.png"):
            stars = "5"
        fee = text_in(words, (600, 401, 706, 448)).strip()
        if (level is None or stars not in {"1", "2", "3", "4", "5"}
                or fee != "40,000"
                or not bright(frame, (350, 210, 390, 290))
                or not has(words, "cancel", (452, 480, 577, 530))
                or not has(words, "confirm", (697, 480, 841, 532))
                or not yellow(frame, (660, 475, 872, 544))
                or not _matches(frame, (663, 303, 688, 328), "assault-assistant-fee-marker.png")
                or not _matches(frame, (527, 401, 578, 449), "assault-assistant-fee-credit.png")):
            return unknown
        return AssaultScreen("assistant_confirm", tuple(words), target=(768, 510),
                             confirm_target=(768, 510), credit_fee=40000,
                             assistant_level=int(level[1]), assistant_stars=int(stars))
    return None
