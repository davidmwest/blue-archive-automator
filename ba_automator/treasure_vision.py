"""Deterministic recognition for Aquatic Showdown's fixed 5×9 treasure board."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from .treasure_rounds import round_counts
import re

import cv2
import numpy as np

from .shop_vision import classify_shop, has, text_in
from .vision import Word, decode_frame, read_game_region, read_game_words

Cell = tuple[int, int]
INVENTORY_BOXES = ((195, 668, 240, 702), (330, 668, 380, 702), (470, 668, 515, 702))
INVENTORY_CROPS = ((190, 665, 245, 708), (320, 665, 385, 708), (460, 665, 520, 708))
FINISH_BOXES = ((110, 605, 245, 650), (245, 605, 380, 650), (380, 605, 520, 650))


@dataclass(frozen=True)
class TreasureScreen:
    kind: str
    target: tuple[int, int] | None = None
    round: int | None = None
    currency: int | None = None
    remaining: int | None = None
    selected: int | None = None
    cost: int | None = None
    closed: frozenset[Cell] = frozenset()
    hits: frozenset[Cell] = frozenset()
    empty: frozenset[Cell] = frozenset()
    inventory: tuple[int, ...] = ()
    selected_cells: frozenset[Cell] = frozenset()


def _match_number(words, box, pattern):
    text = text_in([w for w in words if w.confidence >= .93], box).replace(',', '')
    match = re.fullmatch(pattern, text.strip(), re.I)
    return int(match[1]) if match else None


def _inventory_number(words, box, finish_box):
    number = _match_number(words, box, r'[x×]\s*(\d+)')
    finished = text_in([w for w in words if w.confidence >= .98], finish_box).strip() == 'Finish'
    if finished and number not in (None, 0):
        return None
    if number is not None:
        return number
    # The completed card dims its x0 counter. Accept that exact counter at a
    # lower confidence only when the same card independently says Finish.
    # Finish alone, missing counters, and low-confidence positive counts fail.
    faint = text_in([w for w in words if w.confidence >= .65], box).strip()
    return 0 if finished and re.fullmatch(r'[x×]\s*0', faint, re.I) else None


def _green_check(tile):
    hsv = cv2.cvtColor(tile, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 0] >= 33) & (hsv[:, :, 0] <= 51)
            & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 170))
    # The lime checkmark lies in the tile center. Colored closed tiles and
    # the selection outline cannot satisfy this center-only shape check.
    return mask[16:46, 16:46]


def classify_treasure(frame, words, templates):
    if not has(words, 'treasure hunt', (70, 0, 370, 60)):
        return None
    if (has(words, 'notice', (550, 130, 730, 195))
            and has(words, 'confirm', (670, 465, 880, 550))
            and float((frame[250:300, 400:860].min(axis=2) > 210).mean()) > .9):
        body = text_in(words, (390, 210, 890, 450)).lower()
        if re.fullmatch(r'open the selected slot\(s\)\?', body.strip()):
            return TreasureScreen('treasure_confirm', target=(773, 505))
        if re.fullmatch(r'refresh for the next round\?', body.strip()):
            return TreasureScreen('treasure_refresh_confirm', target=(773, 505))
        # Other notices never inherit authorization from a treasure intent.
        return TreasureScreen('unknown')
    # Entry dialogue overlaps revealed cells along the board's left edge. Its
    # translucent white panel can look like empty stone, even across two frames.
    # The tile grid itself has no text; wait for the dialogue to disappear.
    finished_notice = text_in([w for w in words if w.confidence >= .98],
                              (740, 340, 1100, 390)).strip() == 'Please refresh for the next round.'
    if not finished_notice and any(w.confidence >= .9 and w.box[2] > 607 and w.box[0] < 1235
           and w.box[3] > 190 and w.box[1] < 535 for w in words):
        return TreasureScreen('unknown')
    round_no = _match_number(words, (830, 130, 1010, 163), r'Current Round:\s*Round\s*(\d+)')
    remaining = _match_number(words, (825, 160, 1020, 188), r'Remaining Slots:\s*(\d+)/45')
    currency = _match_number(words, (635, 135, 755, 178), r'(\d+)')
    selected = _match_number(words, (820, 593, 1010, 640), r'Open Slot\s*[x×]\s*(\d+)')
    cost = _match_number(words, (890, 562, 965, 595), r'(\d+)')
    inventory = tuple(_inventory_number(words, box, finish)
                      for box, finish in zip(INVENTORY_BOXES, FINISH_BOXES))
    if (any(v is None for v in (round_no, remaining, currency, selected, cost, *inventory))
            or not 1 <= round_no <= 999 or not 0 <= remaining <= 45
            or not 0 <= selected <= remaining or cost != selected * 200
            or any(n > maximum for n, maximum in zip(inventory, round_counts(round_no)))):
        return TreasureScreen('unknown')
    if finished_notice:
        if inventory != (0, 0, 0) or selected or cost:
            return TreasureScreen('unknown')
        # The game shades the grid after the last prize. Do not invent tile
        # classifications; the runtime reconciles the saved final reveal.
        return TreasureScreen('treasure_complete', round=round_no, currency=currency,
                              remaining=remaining, selected=0, cost=0, inventory=inventory)
    closed, hits, empty, selected_cells = set(), set(), set(), set()
    check = _green_check(templates[-1])
    for r in range(5):
        for c in range(9):
            x, y = 607 + 69*c, 190 + 69*r
            region = frame[y+2:y+67, x+2:x+67]
            tile = frame[y+5:y+64, x+5:x+64]
            # Small alignment tolerance covers canonical resizing and fractional
            # tile pitch without comparing unrelated screen regions.
            errors = [float(cv2.minMaxLoc(cv2.matchTemplate(
                region.astype(np.float32), t.astype(np.float32), cv2.TM_SQDIFF))[0]
                / t.size) ** .5 for t in templates[:-1]]
            actual = _green_check(tile)
            union = (actual | check).sum()
            if union and float((actual & check).sum()) / union > .65:
                closed.add((r, c)); selected_cells.add((r, c))
            elif min(errors) < 12:
                closed.add((r, c))
            else:
                hsv = cv2.cvtColor(tile, cv2.COLOR_BGR2HSV)
                white = (hsv[:, :, 1] < 38) & (hsv[:, :, 2] > 205)
                # Revealed prize fragments sit on white cracked backgrounds.
                # A minimum white area also rejects dimmed boards and animations.
                dimmed_prize = ((hsv[:, :, 1] < 45) & (hsv[:, :, 2] >= 80)
                                & (hsv[:, :, 2] <= 160))
                # Completed prizes darken their artwork, sometimes covering nearly
                # the entire cell; require both that gray art and exposed white stone.
                prize_art = (hsv[:, :, 1] > 75) | (hsv[:, :, 2] < 180)
                if float(white.mean()) > .90 and float(prize_art.mean()) < .005:
                    # A pointed surfboard fragment can leave over 93% white
                    # stone. Require the absence of colored/dark artwork too;
                    # white coverage alone would turn that hit into a miss.
                    empty.add((r, c))
                elif (float(white.mean()) > .06
                        or (float(white.mean()) > .025 and float(dimmed_prize.mean()) > .6)):
                    hits.add((r, c))
                else:
                    return TreasureScreen('unknown')
    if len(closed) != remaining or len(selected_cells) != selected:
        return TreasureScreen('unknown')
    return TreasureScreen('treasure_board', round=round_no, currency=currency,
                          remaining=remaining, selected=selected, cost=cost,
                          closed=frozenset(closed), hits=frozenset(hits),
                          empty=frozenset(empty), inventory=inventory,
                          selected_cells=frozenset(selected_cells))


class TreasureVision:
    def __init__(self, startup, fallback):
        self.startup, self.fallback = startup, fallback
        root = files('ba_automator').joinpath('assets/treasure')
        self.templates = [cv2.imdecode(np.frombuffer(root.joinpath(name).read_bytes(), np.uint8),
                                      cv2.IMREAD_COLOR)
                          for name in [*(f'closed-{i}.png' for i in range(5)), 'selected.png']]

    def analyze(self, png, *, billing=False):
        if billing:
            return self.fallback.analyze(png, billing=True)
        frame = decode_frame(png)
        words = read_game_words(png, self.startup)
        shop = classify_shop(frame, words)
        if shop.kind != 'unknown':
            return shop
        if has(words, 'treasure hunt', (70, 0, 370, 60)):
            # Full-frame OCR sometimes drops the narrow x1 counter. Re-read only
            # missing counters at native resolution; still require exact text.
            for box, crop in zip(INVENTORY_BOXES, INVENTORY_CROPS):
                if _match_number(words, box, r'[x×]\s*(\d+)') is None:
                    local = read_game_region(png, self.startup, crop)
                    words.extend(Word(w.text, w.confidence,
                                      (w.box[0] + crop[0], w.box[1] + crop[1],
                                       w.box[2] + crop[0], w.box[3] + crop[1])) for w in local)
        treasure = classify_treasure(frame, words, self.templates)
        return treasure if treasure is not None else self.fallback.analyze(png, billing=False)
