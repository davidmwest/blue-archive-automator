"""Fixed-layout English Quick Craft recognition; never targets instant completion."""
from dataclasses import dataclass
from importlib.resources import files
import re

import cv2
import numpy as np

from .vision import Word, decode_frame


@dataclass(frozen=True)
class CraftSlot:
    number: int
    kind: str
    seconds: int | None = None


@dataclass(frozen=True)
class CraftScreen:
    kind: str
    slots: tuple[CraftSlot, ...] = ()
    target: tuple[int, int] | None = None
    collect_target: tuple[int, int] | None = None
    quantity: int | None = None
    owned: int | None = None
    required: int | None = None
    credits: int | None = None
    reason: str = ''


def within(words, bounds):
    x1, y1, x2, y2 = bounds
    return [w for w in words if x1 <= w.center[0] <= x2 and y1 <= w.center[1] <= y2]


def has(words, text, bounds):
    return any(w.normalized == text for w in within(words, bounds))


def number(words, bounds, pattern=r'([\d,]+)'):
    hits = [re.fullmatch(pattern, w.text.strip()) for w in within(words, bounds)]
    hits = [hit for hit in hits if hit]
    if len(hits) != 1:
        return None
    return tuple(int(n.replace(',', '')) for n in hits[0].groups())


def bright(frame, bounds):
    x1, y1, x2, y2 = bounds
    return float(np.median(frame[y1:y2, x1:x2])) > 180


def cyan(frame, bounds):
    x1, y1, x2, y2 = bounds
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 110)
                  & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 180)).mean()) > .3


def yellow(frame, bounds):
    x1, y1, x2, y2 = bounds
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
                  & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 180)).mean()) > .3


def classify_crafting(frame, words, *, keystone=False, home=False):
    # Confirmation is recognized before dimmed Quick Craft controls behind it.
    confirmation = number(words, (450, 285, 850, 400), r'Craft (\d) time\(s\)\?')
    if (has(words, 'notice', (500, 130, 780, 200)) and confirmation
            and has(words, 'confirm', (650, 465, 890, 545))
            and cyan(frame, (655, 475, 875, 535))):
        return CraftScreen('confirm', quantity=confirmation[0], target=(768, 504))
    quick = any(w.normalized.startswith('quick craft') for w in within(words, (500, 70, 790, 135)))
    if quick and has(words, 'materials list', (850, 140, 1160, 185)) and bright(frame, (420, 80, 520, 120)):
        if has(words, 'use off', (70, 228, 180, 267)):
            return CraftScreen('disabled', target=(1206, 102), reason='Quick Craft Node 1 is switched off')
        if has(words, 'settings', (280, 240, 550, 298)):
            return CraftScreen('disabled', target=(1206, 102), reason='Quick Craft Node 1 has not been configured')
        if not (has(words, 'node 1', (330, 195, 490, 235))
                and has(words, 'use on', (70, 228, 180, 267))
                and has(words, 'edit', (620, 235, 760, 299))):
            return CraftScreen('unknown', reason='Quick Craft setup is not readable')
        ratios = [w for w in within(words, (800, 190, 1235, 474)) if re.fullmatch(r'[\d,]+/[\d,]+', w.text.strip())]
        # This implementation spends keystones and the displayed credit fee only.
        # Additional material cards must not disappear merely because their
        # counters failed OCR. The rest of the one-material list is plain gray.
        extra_cards = any(float((np.max(np.abs(frame[y1:y2, x1:x2].astype(float) - 204), axis=2) > 20).mean()) > .04
                          for x1, y1, x2, y2 in ((950, 195, 1215, 469), (807, 347, 942, 469)))
        if len(ratios) > 1 or not keystone or extra_cards:
            return CraftScreen('disabled', target=(1206, 102), reason='Quick Craft preset requires unsupported materials; use a keystone-only preset')
        if not ratios:
            return CraftScreen('unknown', reason='Keystone inventory is unreadable')
        owned, required = [int(n.replace(',', '')) for n in ratios[0].text.strip().split('/')]
        quantity = number(words, (940, 488, 1080, 538))
        credits = number(words, (825, 550, 980, 623))
        if quantity and 1 <= quantity[0] <= 3 and required > 0 and credits:
            return CraftScreen('quick', quantity=quantity[0], owned=owned, required=required, credits=credits[0])
        return CraftScreen('unknown', reason='Quick Craft quantity or cost is unreadable')
    if (any(w.normalized in {'crafting', 'crafting 0'} for w in within(words, (90, 0, 250, 50)))
            and has(words, 'crafting list', (840, 140, 1080, 195))
            and bright(frame, (260, 5, 290, 35))):
        # The selected synthesis tab is yellow; Fusion must never authorize input.
        hsv = cv2.cvtColor(frame[175:200, 25:178], cv2.COLOR_BGR2HSV)
        if float(((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40) & (hsv[:, :, 1] > 140) & (hsv[:, :, 2] > 180)).mean()) < .04:
            return CraftScreen('unknown', reason='Material Synthesis is not the selected tab')
        slots = []
        for i, top in enumerate((203, 326, 450), 1):
            region = (674, top, 1235, top + 112)
            timer = number(words, (680, top + 40, 920, top + 104), r'(\d{2}):(\d{2}):(\d{2})')
            empty = has(words, 'start crafting', region)
            remaining = has(words, 'remaining time', region)
            instant = has(words, 'instantly', region)
            ready = has(words, 'crafting complete', region) or has(words, 'claim', region)
            receive = has(words, 'receive', region) and yellow(frame, (1055, top + 25, 1200, top + 85))
            if receive and timer == (0, 0, 0) and remaining and not (empty or instant):
                slots.append(CraftSlot(i, 'ready'))
            elif empty and not (timer or remaining or ready or instant or receive):
                slots.append(CraftSlot(i, 'empty'))
            elif timer and any(timer) and timer[0] <= 24 and timer[1] < 60 and timer[2] < 60 and remaining and not (empty or ready or receive):
                slots.append(CraftSlot(i, 'running', timer[0] * 3600 + timer[1] * 60 + timer[2]))
            elif ready and not (empty or instant or timer or remaining):
                slots.append(CraftSlot(i, 'ready'))
            else:
                return CraftScreen('unknown', reason=f'Craft slot {i} is not readable')
        target = (890, 618) if has(words, 'quick craft', (760, 575, 1010, 655)) and cyan(frame, (805, 590, 975, 645)) else None
        collect_target = ((1120, 618) if has(words, 'claim all', (1020, 575, 1230, 655))
                          and any(slot.kind == 'ready' for slot in slots)
                          and yellow(frame, (1040, 590, 1210, 645)) else None)
        return CraftScreen('list', slots=tuple(slots), target=target, collect_target=collect_target)
    if (has(words, 'reward acquired', (300, 30, 980, 220))
            and has(words, 'touch to continue', (380, 590, 900, 665))):
        return CraftScreen('receipt', target=(640, 631))
    if home:
        # Some home scenes ignore a tap on the protruding Crafting illustration.
        # Use its visible menu label, independently of the character/background.
        # The two home anchors alone do not authorize an unreadable label.
        labels = [word for word in within(words, (600, 672, 723, 706))
                  if word.normalized == 'crafting' and word.confidence >= .9]
        return CraftScreen('home', target=labels[0].center if len(labels) == 1 else None)
    return CraftScreen('unknown')


class CraftVision:
    def __init__(self, startup):
        self.startup = startup
        data = files('ba_automator').joinpath('assets/craft_keystone.png').read_bytes()
        self.keystone = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

    def analyze(self, png):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        sample = frame[198:296, 812:943]
        error = cv2.minMaxLoc(cv2.matchTemplate(sample, self.keystone, cv2.TM_SQDIFF_NORMED))[0]
        if error < .025:
            # Zero inventory is red and may vanish in full-frame OCR. Read the
            # same fixed inventory counter at 2x without assuming missing means 0.
            bounds = (812, 293, 937, 337)
            if not any(re.fullmatch(r'[\d,]+/[\d,]+', w.text.strip()) for w in within(words, bounds)):
                x1, y1, x2, y2 = bounds
                enlarged = cv2.resize(frame[y1:y2, x1:x2], None, fx=2, fy=2)
                for word in self.startup.read(enlarged):
                    if re.fullmatch(r'[\d,]+/[\d,]+', word.text.strip()):
                        box = tuple((n // 2) + (x1 if i % 2 == 0 else y1) for i, n in enumerate(word.box))
                        words.append(Word(word.text, word.confidence, box))
        home = {'home_left', 'home_right'} <= self.startup.matches(frame).keys()
        return classify_crafting(frame, words, keystone=error < .025, home=home)
