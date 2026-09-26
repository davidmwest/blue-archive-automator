"""Local, bounded recognition of the Tactical Challenge attack flow.

Small opponent levels are read together by the existing CPU OCR recognizer.
Only the actual gray question-mark card counts as a hidden student: missing
text on a visible portrait never supplies an estimated level.
"""

from dataclasses import dataclass
from importlib.resources import files
import hashlib
import re
import unicodedata

import cv2
import numpy as np

from .crafting_vision import bright, cyan, has, within, yellow
from .shop_vision import text_in
from .tactical_battles import Opponent
from .tactical_rewards import classify_tactical
from .vision import classify, decode_frame


@dataclass(frozen=True)
class ObservedOpponent:
    choice: Opponent
    name: str
    target: tuple
    signature: str


@dataclass(frozen=True)
class TacticalBattleScreen:
    kind: str
    words: tuple = ()
    rank: int | None = None
    tickets: int | None = None
    opponents: tuple = ()
    sampled_ranks: tuple = ()
    all_ahead: bool = False
    target: tuple | None = None
    quick_target: tuple | None = None
    skip_target: tuple | None = None
    skip_selected: bool | None = None
    formation_seconds: int | None = None
    battle_seconds: int | None = None
    after_tickets: int | None = None
    won: bool | None = None
    items: tuple = ()
    refresh_target: tuple | None = None
    refresh_seconds: int | None = None
    cooldown: int | None = None


def _normalized_name(name):
    return unicodedata.normalize('NFKC', name).casefold().strip()


def same_opponent(left, right):
    """Match the observed name, account level and avatar; rank can change."""
    return (left.choice.level == right.choice.level
            and same_opponent_identity(left, right))


def same_opponent_identity(left, right):
    """Conservative history alias: leveling up must not bypass daily exclusions.

    Selection and battle entry still use :func:`same_opponent`, which also
    requires the account level to agree with the current observation.
    """
    name = _normalized_name(left.name)
    if not name or name != _normalized_name(right.name):
        return False
    try:
        a = np.frombuffer(bytes.fromhex(left.signature), np.uint8)
        b = np.frombuffer(bytes.fromhex(right.signature), np.uint8)
    except (ValueError, TypeError):
        return False
    if a.size != 24 * 24 or b.size != a.size or min(a.std(), b.std()) < 8:
        return False
    return float(np.corrcoef(a, b)[0, 1]) >= .82


def _signature(frame, bounds):
    x1, y1, x2, y2 = bounds
    crop = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    return cv2.resize(crop, (24, 24), interpolation=cv2.INTER_AREA).tobytes().hex()


def _match(words, pattern, bounds):
    found = within(words, bounds)
    if not found or any(w.confidence < .9 for w in found):
        return None
    return re.fullmatch(pattern, text_in(found, bounds).strip(), re.I)


def _rank(words, bounds):
    found = _match(words, r'Rank\s*([1-9][0-9,]*)', bounds)
    return int(found[1].replace(',', '')) if found else None


def _cooldown(words):
    if not has(words, 'standby time', (45, 503, 180, 550)):
        return None
    clocks = []
    for word in within(words, (178, 503, 275, 550)):
        value = word.text.strip()
        if word.confidence >= .8 and re.fullmatch(r'(?:[-–—]+\s*:\s*[-–—]+|\d{1,2}:\d{2})', value):
            clocks.append(value)
        elif (word.confidence >= .7 and re.fullmatch(r'e\s+[-–—]+\s*:\s*[-–—]+', value)):
            # Full-frame OCR sometimes includes the last letter of "Time"
            # twice. Accept this observed overlap only for the exact dash
            # clock, with the complete label independently present above.
            clocks.append(value[1:].strip())
    if len(clocks) != 1:
        return None
    text = clocks[0]
    if re.fullmatch(r'[-–—]+\s*:\s*[-–—]+', text):
        return 0
    found = re.fullmatch(r'(\d{1,2}):(\d{2})', text)
    return int(found[1]) * 60 + int(found[2]) if found and int(found[2]) < 60 else None


def _asset(name):
    return cv2.imdecode(np.frombuffer(files('ba_automator').joinpath(
        'assets', name).read_bytes(), np.uint8), cv2.IMREAD_COLOR)


def _hidden(frame, x, y, template):
    # Search only the inner card. Levels on top and changing borders cannot
    # turn a visible student into a question mark.
    crop = frame[y + 5:y + 47, x + 7:x + 60]
    return float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED).max()) >= .88


def _level_crops(frame, x, y):
    crops = []
    # The outlined italic font is too small for full-screen text detection.
    # Padded whole labels retain "Lv"; digit crops handle low contrast, while
    # wider crops below reject clipping errors. No Greek pi -> 77 substitutions.
    for dx, dy, width, height, scale, pad in (
            (0, 0, 34, 14, 4, False), (0, 0, 34, 14, 5, True),
            (10, 1, 25, 14, 5, True), (11, 1, 24, 14, 5, True),
            (14, 0, 17, 14, 4, False), (13, 0, 18, 15, 4, False),
            (13, 0, 19, 14, 5, False), (15, 0, 19, 14, 5, False),
            (0, 3, 34, 12, 5, False),
            # Preserve the left and top strokes of outlined 8s. Tight OCR
            # crops can unanimously hallucinate 3 after clipping those strokes;
            # these wider reads retain the missing pixels for conflict checks.
            (11, 0, 22, 15, 6, False), (11, 0, 22, 15, 5, False),
            (11, -1, 24, 16, 6, False), (10, -1, 25, 16, 5, False)):
        crop = cv2.resize(frame[y + dy:y + dy + height, x + dx:x + dx + width],
                          None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        if pad:
            crop = cv2.copyMakeBorder(crop, 0, 0, 10, 50, cv2.BORDER_REPLICATE)
        crops.append(crop)
    return crops


def _read_level(values, maximum):
    anchored = set()
    possible_anchors = []
    digits = []
    for index, (text, score) in enumerate(values[:9]):
        if index in (0, 1, 8):
            match = re.fullmatch(r'[Ll1I]v[. :\-]*([0-9]{1,3})', text, re.I)
            if match and score >= .8:
                possible_anchors.append(int(match[1]))
            if match and score >= .85:
                anchored.add(int(match[1]))
        elif score >= .94 and re.fullmatch(r'[0-9]{1,3}', text):
            digits.append(int(text))
    if len(anchored) > 1:
        return None
    agreed = {value for value in digits if digits.count(value) >= 2 and value <= maximum}
    digit_value = next(iter(agreed)) if len(agreed) == 1 else None
    value = next(iter(anchored), digit_value)
    if anchored and digit_value is not None and digit_value != value:
        return None
    if any(anchor != value and possible_anchors.count(anchor) >= 2
           for anchor in possible_anchors):
        # An uncertain whole-label reading cannot authorize a level, but it
        # can contradict digit-only OCR. In the live 75 -> 15 failure, both
        # complete labels read 75 just below the acceptance threshold.
        return None
    guards = []
    for text, score in values[9:]:
        match = re.fullmatch(r'(?:[vV][. :]*|[. ])?([0-9]{1,3})', text)
        if match and score >= .94:
            guards.append(int(match[1]))
        if match and score >= .94 and int(match[1]) != value:
            # Never resolve contradictory font interpretations by voting:
            # multiple resized copies share the same missing-pixel error.
            return None
    if not anchored and (value is None or value not in guards):
        # Digit-only recognition needs confirmation with the full glyph
        # bounds; agreement among clipped versions alone is insufficient.
        return None
    return value if value is not None and 1 <= value <= maximum else None


class TacticalBattleVision:
    def __init__(self, startup):
        self.startup = startup
        self.hidden_template = _asset('tactical-hidden-student.png')
        self.versus_template = _asset('tactical-versus.png')

    def _opponents(self, frame, words, detail=False):
        specs = ((178, 532, (250, 252, 505, 299), (345, 173, 510, 222),
                  (278, 179, 320, 215), (640, 575)),) if detail else tuple(
            (y, 740, (450, y + 75, 733, y + 118),
             (549, y - 7, 729, y + 45), (484, y, 526, y + 36), (830, y + 44))
            for y in (206, 365, 523))
        metadata, images, visible = [], [], []
        for y, x, label, rank_bounds, portrait, target in specs:
            account = _match(words, r'Lv\.?\s*(\d{1,3})\s+(.+)', label)
            rank = _rank(words, rank_bounds)
            if not account or not rank or not 1 <= int(account[1]) <= 200:
                continue
            level, name = int(account[1]), account[2].strip()
            if not name or len(name) > 50:
                continue
            signature = _signature(frame, portrait)
            if np.frombuffer(bytes.fromhex(signature), np.uint8).std() < 8:
                continue
            indices = []
            for slot in range(6):
                card_x = x + 65 * slot
                if _hidden(frame, card_x, y, self.hidden_template):
                    # Only three defensive Strikers can be concealed.
                    if slot not in (1, 2, 3):
                        return ()
                    continue
                indices.append(len(visible))
                visible.append(level)
                images.extend(_level_crops(frame, card_x + 5, y + 1))
            metadata.append((level, name, rank, signature, target, indices))
        if not images:
            return ()
        from rapidocr.ch_ppocr_rec.typings import TextRecInput
        result = self.startup.ocr.text_rec(TextRecInput(images))
        if result.txts is None or len(result.txts) != len(images):
            return ()
        readings = list(zip(result.txts, result.scores))
        levels = [_read_level(readings[i * 13:(i + 1) * 13], maximum)
                  for i, maximum in enumerate(visible)]
        opponents = []
        for level, name, rank, signature, target, indices in metadata:
            values = tuple(levels[i] for i in indices)
            if not values or any(value is None for value in values):
                continue
            identity = hashlib.sha256(
                f'{_normalized_name(name)}\0{level}\0{signature}'.encode()).hexdigest()[:24]
            opponents.append(ObservedOpponent(Opponent(identity, rank, level, values),
                                              name, target, signature))
        if len({p.choice.rank for p in opponents}) != len(opponents):
            return ()
        return tuple(opponents)

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = tuple(self.startup.read(frame))
        return self.classify(frame, words, billing=billing)

    def classify(self, frame, words, *, billing=False):
        unknown = TacticalBattleScreen('unknown', words=tuple(words))
        if frame.shape[:2] != (720, 1280):
            return unknown
        if (has(words, 'tip', (270, 65, 430, 170))
                and has(words, 'buff', (430, 145, 630, 195))
                and has(words, 'debuff', (430, 210, 635, 265))
                and has(words, 'crowd control', (430, 280, 640, 337))
                and has(words, 'special effects', (430, 350, 645, 408))
                and has(words, 'confirm', (560, 620, 721, 691))
                and yellow(frame, (535, 626, 740, 690))):
            return TacticalBattleScreen('battle_tip', words=tuple(words), target=(640, 660))
        result_time = _match(words, r'(\d{2}):([0-5]\d)', (660, 420, 755, 470))
        result_hsv = cv2.cvtColor(frame[280:387, 480:801], cv2.COLOR_BGR2HSV)
        red = (((result_hsv[:, :, 0] <= 10) | (result_hsv[:, :, 0] >= 170))
               & (result_hsv[:, :, 1] >= 55) & (result_hsv[:, :, 2] >= 180))
        if (has(words, 'lose', (440, 240, 835, 420)) and red.mean() > .2
                and result_time and int(result_time[1]) <= 3
                and has(words, 'time', (550, 420, 638, 472))
                and has(words, 'confirm', (560, 620, 721, 691))
                and yellow(frame, (535, 626, 740, 690))):
            return TacticalBattleScreen('result', words=tuple(words),
                                        won=False, target=(640, 660))
        win_titles = [w for w in within(words, (440, 240, 835, 420))
                      if w.text.strip() == 'WIN' and w.confidence >= .97
                      and w.box[2] - w.box[0] >= 140 and w.box[3] - w.box[1] >= 70]
        if (len(win_titles) == 1 and red.mean() < .1
                and result_time and int(result_time[1]) <= 3
                and _match(words, r'Time', (550, 420, 638, 472))
                and _match(words, r'Confirm', (560, 620, 721, 691))
                and yellow(frame, (535, 626, 740, 690))):
            # The counterpart layout is covered synthetically until an actual
            # victory is captured. Require the large exact title and reject
            # the observed red defeat title even if its OCR says "WIN".
            return TacticalBattleScreen('result', words=tuple(words),
                                        won=True, target=(640, 660))
        timer = _match(words, r'(\d{2}):([0-5]\d)', (1070, 10, 1150, 64))
        if (timer and int(timer[1]) <= 3
                and _match(words, r'Lv\.?\s*\d{1,3}\s+.+', (270, 12, 600, 62))
                and _match(words, r'.+\s*Lv\.?\s*\d{1,3}', (675, 12, 1005, 62))
                and has(words, 'cost', (770, 632, 839, 681))
                and float(cv2.matchTemplate(frame[23:52, 619:661], self.versus_template,
                                             cv2.TM_CCOEFF_NORMED)[0, 0]) >= .9):
            # This is progress evidence only. Automatic PvP combat has no
            # manual input; cut-ins hide the HUD and remain unknown.
            return TacticalBattleScreen('battle', words=tuple(words),
                battle_seconds=int(timer[1]) * 60 + int(timer[2]))
        if (has(words, 'notice', (565, 138, 710, 190))
                and has(words, 'formation timed out', (490, 302, 790, 370))
                and has(words, 'confirm', (550, 470, 725, 535))
                and cyan(frame, (530, 474, 751, 530))):
            return TacticalBattleScreen('timeout_notice', words=tuple(words), target=(640, 505))
        if (has(words, 'notice', (565, 138, 710, 190))
                and 'you must select another opponent because matching has ended' in
                    ' '.join(w.normalized for w in within(words, (375, 230, 908, 405)))
                and has(words, 'confirm', (690, 473, 840, 535))
                and cyan(frame, (668, 473, 869, 535))):
            return TacticalBattleScreen('timeout_notice', words=tuple(words), target=(765, 504))
        if (has(words, 'battle opponent', (490, 70, 790, 126))
                and has(words, 'opponent info', (245, 294, 429, 335))
                and has(words, 'my info', (885, 307, 1000, 353))
                and has(words, 'attack formation', (520, 548, 760, 598))
                and yellow(frame, (528, 543, 751, 601))):
            change = _match(words, r'(\d+)\s*[→➜]\s*(\d+)', (680, 500, 763, 544))
            rank = _rank(words, (337, 368, 512, 427))
            opponents = self._opponents(frame, words, detail=True)
            if (change and rank and opponents and int(change[1]) - int(change[2]) == 1):
                return TacticalBattleScreen('opponent', words=tuple(words), rank=rank,
                    tickets=int(change[1]), after_tickets=int(change[2]),
                    opponents=opponents, target=(640, 575))
            return unknown
        if (any(re.fullmatch(r'attack formation(?: [0-9])?', w.normalized)
                for w in within(words, (90, 0, 410, 48)))
                and bright(frame, (395, 4, 440, 37))
                and has(words, 'quick formation', (1128, 185, 1270, 235))
                and has(words, 'mobilize', (1090, 635, 1250, 705))
                and has(words, 'skip battle', (1125, 575, 1270, 631))
                and yellow(frame, (1090, 642, 1250, 698))):
            # The cyan check is distinct from the muted blue empty checkbox.
            box = frame[590:617, 1100:1130]
            hsv = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
            checked = ((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 105)
                       & (hsv[:, :, 1] >= 170) & (hsv[:, :, 2] >= 150))
            plain = np.mean(np.abs(box.astype(float) - (181, 154, 114)), axis=2) < 12
            selected = True if checked.mean() > .1 else False if plain.mean() > .65 else None
            timer = _match(words, r'(\d{2}):(\d{2})', (60, 633, 145, 674))
            seconds = int(timer[1]) * 60 + int(timer[2]) if timer and int(timer[2]) < 60 else None
            return TacticalBattleScreen('formation', formation_seconds=seconds, words=tuple(words), target=(1168, 669),
                quick_target=(1200, 160), skip_target=(1115, 604), skip_selected=selected)
        home = not billing and classify(words, self.startup.matches(frame)).state == 'home'
        base = classify_tactical(frame, words, home=home)
        if base.kind == 'tactical':
            rank = _rank(words, (120, 280, 325, 346))
            cooldown = _cooldown(words)
            if (rank is None or cooldown is None
                    or not has(words, 'refresh list', (1110, 119, 1250, 171))):
                return unknown
            opponents = self._opponents(frame, words)
            ranks = tuple(_rank(words, (549, y - 7, 729, y + 45)) for y in (206, 365, 523))
            sampled = ranks if None not in ranks and len(set(ranks)) == 3 else ()
            all_ahead = bool(sampled) and all(value < rank for value in sampled)
            refresh_timer = _match(words, r'Time Left\s+(\d{2}):([0-5]\d)',
                                   (945, 125, 1100, 171))
            refresh_seconds = (int(refresh_timer[1]) * 60 + int(refresh_timer[2])
                               if refresh_timer else None)
            if refresh_seconds is not None and refresh_seconds > 120:
                refresh_seconds = None
            return TacticalBattleScreen('tactical', words=tuple(words), rank=rank,
                sampled_ranks=sampled, all_ahead=all_ahead,
                tickets=base.tickets, opponents=opponents, refresh_target=(1174, 147),
                refresh_seconds=refresh_seconds, cooldown=cooldown)
        if base.kind in {'home', 'campaign', 'receipt'}:
            return TacticalBattleScreen(base.kind, words=tuple(words), target=base.target,
                                       items=base.items)
        return unknown
