"""English pack checkout and mailbox recognition, using local OCR only."""
from dataclasses import dataclass, field, replace
import re
import unicodedata

import cv2
import numpy as np

from .crafting_vision import bright, cyan, yellow, has as _has, within, number
from .packs_state import PACKS, PERMANENT_PACKS, WEEKLY_PACKS
from .vision import read_game_words, VisionError, classify, decode_frame
from .home_badges import badges


@dataclass(frozen=True)
class ShopScreen:
    kind: str
    target: tuple[int, int] | None = None
    pack: str | None = None
    cents: int | None = None
    cards: dict = field(default_factory=dict)
    items: tuple = ()
    balances: dict = field(default_factory=dict)
    tab: str | None = None
    size: tuple[int, int] = (1280, 720)
    red_dot: bool = False


def text_in(words, box):
    return ' '.join(w.text for w in sorted(within(words, box), key=lambda w: (w.box[1], w.box[0])))


def has(words, text, box):
    return _has(words, re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip(), box)


def price(words, box, *, play=False):
    text = ' '.join(w.text for w in sorted(within(words, box), key=lambda w: w.box[0]))
    pattern = r'(?:US\$|\$)(\d+)\.(\d{2})' if play else r'USD (\d+)\.(\d{2})'
    match = re.fullmatch(pattern, text.strip())
    return int(match[1]) * 100 + int(match[2]) if match else None


def selected_tab(frame, words, name, box):
    if not has(words, name, box):
        return False
    x1, y1, x2, y2 = box
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
                  & (hsv[:, :, 1] > 150) & (hsv[:, :, 2] > 180)).mean()) > .025


def receipt_items(words):
    items = []
    for word in within(words, (90, 380, 1190, 570)):
        match = re.fullmatch(r'[x×]([\d,]+)', word.text.strip())
        if not match:
            continue
        x, y = word.center
        name_box = (x - 74, 249, x + 74, 306) if 427 <= y <= 468 else (x - 72, y - 170, x + 72, y - 100)
        name = text_in(words, name_box).strip()
        if name:
            items.append({'name': name, 'quantity': int(match[1].replace(',', ''))})
    return tuple(items)


def weekly_cards(frame, words):
    """Read fully visible named cards, including scrolled and sold-out cards."""
    cards = {}
    for left in (257, 514, 772):
        column = sorted(within(words, (left, 214, left + 251, 620)),
                        key=lambda w: (w.box[1], w.box[0]))
        for i, word in enumerate(column):
            if not word.text.startswith('Weekly'):
                continue
            title = word.text
            key = next((k for k in WEEKLY_PACKS if PACKS[k][0] == title), None)
            if key is None and i + 1 < len(column):
                following = column[i + 1]
                if following.box[1] - word.box[1] < 32:
                    title += ' ' + following.text
                    key = next((k for k in WEEKLY_PACKS if PACKS[k][0] == title), None)
            if key is None:
                continue
            buttons = [w for w in column if w.text == 'Purchase'
                       and word.center[1] + 150 < w.center[1] < word.center[1] + 310]
            if len(buttons) != 1:
                continue
            y = buttons[0].center[1]
            cents = price(words, (left + 30, y - 52, left + 220, y - 12))
            if cents is None:
                continue
            status = text_in(words, (left, y - 116, left + 251, y - 34)).lower()
            active = re.search(r'(\d+) day\(s\) until subscription expires', status)
            stock = re.search(r'can purchase (\d+) time\(s\) a week', status)
            state, days = 'unknown', None
            if active:
                state, days = 'active', int(active[1])
            elif 'check your' in status and 'mailbox' in status:
                state = 'mail'
            elif stock:
                state = 'available' if int(stock[1]) > 0 else 'unavailable'
            elif key == 'weekly_ap_iv' and not text_in(words, (left, y-85, left+251, y-54)):
                # A missing OCR status must not hide a dark ownership stripe.
                stripe = frame[y-83:y-55, left+2:left+245]
                if float((stripe.max(axis=2) < 180).mean()) < .025:
                    state = 'available'
            if state == 'available' and not cyan(frame, (left+50, y-5, left+200, y+18)):
                state = 'unknown'
            cards[key] = {'state': state, 'days': days, 'cents': cents,
                          'target': (left + 128, y)}
    return cards


def classify_shop(frame, words, *, home=False, billing=False):
    height, width = frame.shape[:2]
    if billing:
        # Only the observed English Google Play portrait checkout is supported.
        # All other payment, verification, and account setup screens need the user.
        size = (width, height)
        if size != (720, 1280):
            return ShopScreen('billing_attention', size=size)
        if has(words, 'processing', (180, 850, 540, 1200)):
            return ShopScreen('processing', size=size)
        if (has(words, 'google play', (20, 240, 310, 340))
                and has(words, 'turn on backup payment methods?', (20, 590, 700, 740))):
            return ShopScreen('backup_prompt', target=(668, 280), size=size)
        names = {key: title for key, (title, _) in PACKS.items()}
        # Play uses this shorter product label for the Lite offer. The caller
        # still requires the full in-game identity and matching price first.
        names['weekly_reports_lite'] = 'Weekly Activity Report'
        product = ' '.join(' '.join(w.text.strip() for w in sorted(
            within(words, (110, 430, 570, 550)), key=lambda w: w.box[0])).split())
        found = [key for key, title in names.items() if product == title]
        cents = price(words, (570, 430, 710, 570), play=True)
        if (len(found) == 1 and cents is not None
                and (has(words, 'google play', (15, 310, 350, 430))
                     or has(words, 'googleplay', (15, 310, 350, 430)))
                and has(words, 'blue archive', (110, 530, 480, 610))
                and has(words, '1 tap buy', (110, 1150, 610, 1270))):
            return ShopScreen('checkout', (360, 1215), found[0], cents, size=size)
        return ShopScreen('billing_attention', size=size)
    if (width, height) != (1280, 720):
        return ShopScreen('unknown', size=(width, height))
    balances = {}
    for name, box in (('pyroxenes', (920, 0, 1030, 48)), ('credits', (700, 0, 850, 48))):
        amount = number(words, box)
        if amount:
            balances[name] = amount[0]
    ap = number(words, (495, 0, 615, 48), r'(\d+)/(\d+)')
    if ap:
        balances['ap'] = ap[0]
    # The animated heading can add an accent to an otherwise correctly read A.
    # Normalize only this receipt label, never product names, prices or status.
    if (any(re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', w.text).lower()) == 'rewardacquired'
            for w in within(words, (300, 120, 980, 210)))
            and has(words, 'touch to continue', (380, 590, 900, 665))
            and yellow(frame, (375, 134, 901, 182))):
        return ShopScreen('receipt', (640, 631), items=receipt_items(words), balances=balances)
    if (has(words, 'notice', (500, 130, 780, 200)) and cyan(frame, (660, 475, 860, 535))
            and has(words, 'would you like to claim the item?', (390, 290, 880, 395))):
        return ShopScreen('claim_confirm', (767, 504))
    if (has(words, 'notice', (500, 130, 780, 200))
            and text_in(words, (430, 280, 840, 390)) == 'The purchased products will be delivered to your mailbox.'
            and cyan(frame, (550, 475, 735, 535))):
        return ShopScreen('delivered', (640, 505))
    if (has(words, 'buy pyroxene', (450, 50, 830, 125))
            and has(words, 'purchase this product?', (440, 120, 850, 180))
            and bright(frame, (400, 75, 460, 100))):
        title = text_in(words, (275, 185, 525, 255))
        found = [key for key, (name, _) in PACKS.items() if title == name]
        cents = price(words, (705, 410, 940, 480))
        if len(found) == 1 and cents is not None and has(words, 'confirm', (660, 550, 860, 630)):
            return ShopScreen('purchase_confirm', (760, 592), found[0], cents)
        return ShopScreen('unknown')
    if (has(words, 'buy pyroxene', (450, 85, 830, 145))
            and has(words, 'pyroxenes', (520, 150, 760, 210))
            and bright(frame, (420, 90, 500, 130))):
        cards = {}
        for key, left in zip(PERMANENT_PACKS, (257, 514, 772)):
            title = text_in(words, (left, 215, left + 251, 276))
            if title != PACKS[key][0]:
                continue
            cents = price(words, (left + 35, 445, left + 185, 487))
            status = text_in(words, (left, 388, left + 251, 444)).lower()
            match = re.search(r'(\d+) day\(s\) until subscription expires', status)
            state, days = 'unknown', None
            if match:
                state, days = 'active', int(match[1])
            elif 'check your' in status and 'mailbox' in status:
                state = 'mail'
            elif 'restocks' in status:
                state = 'unavailable'
            else:
                stripe = frame[418:442, left + 2:left + 245]
                hsv = cv2.cvtColor(frame[388:419, left + 2:left + 245], cv2.COLOR_BGR2HSV)
                red = ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170)) & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 160)
                if (not status and float((stripe.max(axis=2) < 180).mean()) < .025
                        and float(red.mean()) < .01):
                    state = 'available'
            if (cents is not None and has(words, 'purchase', (left + 35, 480, left + 225, 530))
                    and cyan(frame, (left + 50, 485, left + 200, 520))):
                cards[key] = {'state': state, 'days': days, 'cents': cents, 'target': (left + 128, 500)}
        cards.update(weekly_cards(frame, words))
        return ShopScreen('store', cards=cards, target=(640, 180))
    if (has(words, 'mailbox', (80, 0, 240, 50))
            and bright(frame, (250, 7, 350, 30))):
        tab = next((name for name, box in (('product', (15, 185, 210, 245)),
                                         ('unclaimed', (15, 108, 210, 165)))
                    if selected_tab(frame, words, name, box)), None)
        if tab:
            claims = [w for w in within(words, (1070, 100, 1240, 600))
                      if w.normalized == 'claim' and cyan(frame, (1090, w.center[1]-20, 1220, w.center[1]+20))]
            empty = has(words, 'no more mail', (600, 420, 900, 540))
            count = number(words, (1120, 48, 1260, 100), r'(\d+)/200')
            if claims:
                return ShopScreen('mail', sorted(claims, key=lambda w: w.center[1])[0].center,
                                  balances=balances, tab=tab)
            if empty and count == (0,):
                return ShopScreen('mail_empty', balances=balances, tab=tab)
        return ShopScreen('unknown')
    if home:
        return ShopScreen('home', red_dot='mail' in badges(frame))
    return ShopScreen('unknown')


class ShopVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        if billing:
            # Checkout uses native portrait coordinates and its own strict size guard.
            frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise VisionError('Invalid shop screenshot')
        else:
            frame = decode_frame(png)
        native_size = (frame.shape[1], frame.shape[0])
        if billing and native_size == (1440, 2560):
            frame = cv2.resize(frame, (720, 1280), interpolation=cv2.INTER_AREA)
        words = self.startup.read(frame) if billing else read_game_words(png, self.startup)
        if billing and any(w.box[0] < 570 < w.box[2] and 430 < w.center[1] < 540 for w in words):
            # OCR can join the long product label and adjacent price. Read
            # those fields separately, without accepting partial title matches.
            words = [w for w in words if not 430 < w.center[1] < 540]
            for x1, y1, x2, y2 in ((110, 430, 570, 543), (570, 430, 710, 543)):
                for word in self.startup.read(frame[y1:y2, x1:x2]):
                    a, b, c, d = word.box
                    words.append(replace(word, box=(a+x1, b+y1, c+x1, d+y1)))
        home = (not billing and frame.shape[:2] == (720, 1280)
                and classify(words, self.startup.matches(frame)).state == 'home')
        screen = classify_shop(frame, words, home=home, billing=billing)
        if billing and native_size == (1440, 2560):
            target = tuple(2 * n for n in screen.target) if screen.target else None
            return replace(screen, target=target, size=native_size)
        return screen
