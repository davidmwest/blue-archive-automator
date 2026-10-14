"""Event Task, Draw a Card and Event Shop pages; all coordinates are canonical.

Recognition here never authorizes spending by itself. Runners still verify the
event identity on entry, the balance, the price and a fresh frame before input.
"""
from dataclasses import dataclass
import re

import cv2
import numpy as np

from .ap_vision import ap_value
from .crafting_vision import bright, cyan, yellow, within
from .shop_vision import classify_shop, has, repair_receipt_prompt, text_in
from .vision import decode_frame, read_game_words

PAGE_BUTTONS = {'shop': (277, 673), 'tasks': (398, 673), 'cards': (520, 671)}
SHOP_TABS = ((90, 140), (90, 218), (90, 296))
CARD_SLOTS = ((666, 304, 800, 493), (808, 304, 942, 493), (951, 304, 1085, 493), (1094, 304, 1228, 493))
CARD_BACK = '山海'


@dataclass(frozen=True)
class ShopItem:
    name: str
    price: int
    stock: int | None      # None when the stock line is unreadable or unlimited
    target: tuple          # centre of this card's Purchase button


@dataclass(frozen=True)
class ExtrasScreen:
    kind: str
    ap: int | None = None
    target: tuple | None = None
    balance: int | None = None
    items: tuple = ()
    tab: int | None = None
    draw_one: tuple | None = None
    shuffle: tuple | None = None
    face_down: tuple = ()
    premium: tuple = ()
    name: str | None = None
    quantity: int | None = None
    unit_cost: int | None = None
    total_cost: int | None = None
    maximum: tuple | None = None


def normalized(name):
    return re.sub(r'[^a-z0-9]+', '', name.lower())


def same_item(first, second):
    """Card and dialog names agree; a long name may be cut short in one of them."""
    first, second = normalized(first or ''), normalized(second or '')
    if not first or not second:
        return False
    short, long = sorted((first, second), key=len)
    return first == second or (len(short) >= 12 and long.startswith(short))


def _number(words, bounds):
    hits = [w for w in within(words, bounds) if re.fullmatch(r'[\d,]+', w.text.strip())]
    return int(hits[0].text.replace(',', '')) if len(hits) == 1 else None


def shop_items(frame, words):
    """Fully visible cards only: a Purchase button with its price, stock and name."""
    items = []
    for button in within(words, (620, 300, 1250, 700)):
        if button.normalized != 'purchase':
            continue
        x, y = button.center
        price = _number(words, (x - 70, y - 42, x + 70, y - 10))
        name = ' '.join(text_in(words, (x - 80, y - 222, x + 80, y - 150)).split())
        if price is None or not name:
            continue
        stock = re.search(r'Can purchase (\d+) time\(s\)', text_in(words, (x - 82, y - 72, x + 82, y - 42)))
        items.append(ShopItem(name, price, int(stock[1]) if stock else None, (x, y)))
    return tuple(sorted(items, key=lambda item: (item.target[1] // 40, item.target[0])))


def premium_border(frame, slot):
    """True for a revealed card framed in gold or rainbow, not grey, blue or white.

    Only a Normal card has been observed live. Its frame is unsaturated, and a
    Rare card's is blue; anything else strongly coloured is treated as the
    guaranteed high-grade card of the set. A wrong answer only changes when the
    free shuffle happens.
    """
    x1, y1, x2, y2 = slot
    card = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    ring = np.ones(card.shape[:2], dtype=bool)
    ring[7:-7, 7:-7] = False
    hue, saturation, value = (card[:, :, index][ring] for index in range(3))
    vivid = (saturation > 90) & (value > 150)
    if float(vivid.mean()) < .45:
        return False
    blue = (hue[vivid] >= 90) & (hue[vivid] <= 130)
    return float(blue.mean()) < .6


def classify_extras(frame, words):
    readable = [w for w in words if w.confidence >= .75]
    words = [w for w in words if w.confidence >= .9]
    ap = ap_value(words)
    if (has(words, 'notice', (540, 120, 740, 185))
            and has(words, 'would you like to purchase the selected product', (300, 195, 980, 260))
            and has(words, 'confirm', (680, 495, 860, 550))
            and yellow(frame, (690, 500, 850, 540))):
        name = ' '.join(text_in(words, (470, 268, 790, 322)).split())
        # A lone "1" is read with less confidence than longer numbers. The
        # quantity is accepted only when both confident costs agree with it.
        quantity = _number(readable, (620, 330, 790, 372))
        unit, total = _number(words, (480, 405, 600, 445)), _number(words, (790, 405, 930, 445))
        if name and quantity and unit and total == quantity * unit:
            return ExtrasScreen('event_purchase', ap=ap, target=(765, 520), name=name,
                                quantity=quantity, unit_cost=unit, total_cost=total,
                                maximum=(883, 350))
        return ExtrasScreen('event_purchase', ap=ap)
    if (has(words, 'event task', (80, 0, 300, 60))
            and has(words, 'achievement', (480, 85, 700, 135))
            and has(words, 'claim all', (1060, 640, 1240, 700))
            and bright(frame, (300, 5, 460, 30))):
        claimable = yellow(frame, (1075, 650, 1225, 690))
        return ExtrasScreen('event_tasks', ap=ap, target=(1150, 670) if claimable else None)
    if (has(words, 'event shop', (80, 0, 300, 60))
            and has(words, 'currency', (20, 115, 170, 165))
            and bright(frame, (300, 5, 460, 30))):
        tabs = [index for index, (x, y) in enumerate(SHOP_TABS)
                if not bright(frame, (x - 60, y - 22, x - 30, y + 22))]
        return ExtrasScreen('event_shop', ap=ap, balance=_number(words, (1060, 60, 1250, 110)),
                            items=shop_items(frame, words), tab=tabs[0] if len(tabs) == 1 else None)
    if (has(words, 'draw a card', (80, 0, 300, 60))
            and has(words, 'draw a card', (720, 555, 930, 610))
            and has(words, 'draw 4 cards', (950, 555, 1180, 610))
            and bright(frame, (300, 5, 460, 30))):
        backs = tuple(index for index, (x1, y1, x2, y2) in enumerate(CARD_SLOTS)
                      if any(w.text.strip().startswith(CARD_BACK) for w in within(words, (x1, y1, x2, y2))))
        revealed = [index for index in range(len(CARD_SLOTS)) if index not in backs]
        return ExtrasScreen(
            'event_cards', ap=ap, balance=_number(words, (680, 160, 820, 215)),
            draw_one=(828, 582) if cyan(frame, (740, 560, 920, 605)) else None,
            shuffle=(1150, 186), face_down=backs,
            premium=tuple(index for index in revealed if premium_border(frame, CARD_SLOTS[index])))
    return None


class ExtrasVision:
    def __init__(self, startup, fallback):
        self.startup, self.fallback = startup, fallback

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = repair_receipt_prompt(frame, read_game_words(png, self.startup), self.startup)
        # Reward Acquired dims the page beneath it; recognize the overlay first.
        receipt = classify_shop(frame, words)
        if receipt.kind == 'receipt':
            return ExtrasScreen('receipt', ap=ap_value(words), target=receipt.target)
        if (has(words, 'touch to continue', (380, 590, 900, 665))
                and yellow(frame, (375, 134, 901, 182))):
            # The Task page's own text shows through beside the heading and is
            # read as part of it; re-read the heading alone, exactly.
            from .loot_receipts import reward_heading_words
            if reward_heading_words(png, self.startup, words):
                return ExtrasScreen('receipt', ap=ap_value(words), target=(640, 631))
        return classify_extras(frame, words) or self.fallback.analyze(png, billing=billing)
