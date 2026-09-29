"""Observed English Joint Firing Drill screens, using native-resolution OCR."""
from dataclasses import dataclass, replace
import re

import cv2

from .assault_vision import classify_assault, read_formation
from .assault_battle_vision import _clock, auto_state
from .crafting_vision import bright, cyan, yellow
from .shop_vision import has, text_in
from .vision import decode_frame, decode_native_frame, read_game_words, read_game_region, Word


@dataclass(frozen=True)
class DrillScreen:
    kind: str
    words: tuple = ()
    target: tuple | None = None
    team: tuple = ()
    tickets: int | None = None
    period: str | None = None
    stage: int | None = None
    mock: bool = False
    active: bool = False
    score: int | None = None
    remaining_seconds: float | None = None
    won: bool | None = None
    auto_on: bool | None = None
    elapsed_seconds: float | None = None
    quick_target: tuple | None = None
    remaining_rounds: int | None = None
    tickets_after: int | None = None
    count: int | None = None


def classify_drill(frame, words, *, home=False):
    words = tuple(words)
    sweep_notice = re.fullmatch(r'Use (\d+) of Drill Ticket to proceed with (\d+) Sweep\(s\)\?',
                                text_in(words, (400, 295, 890, 380)).strip())
    if (sweep_notice and sweep_notice[1] == sweep_notice[2]
            and has(words, 'Confirm', (680, 480, 865, 535))
            and cyan(frame, (700, 480, 870, 525))):
        return DrillScreen('sweep_confirm', words, target=(770, 505), count=int(sweep_notice[1]))
    if (has(words, 'Sweep Count', (790, 260, 1000, 315))
            and has(words, 'Expected Rewards', (320, 390, 550, 445))
            and has(words, 'Start Sweep', (780, 435, 1010, 500))
            and yellow(frame, (770, 450, 800, 490))):
        count = _points(text_in(words, (865, 335, 925, 385)))
        delta = re.fullmatch(r'(\d+)\s*→\s*(\d+)', text_in(words, (940, 398, 1060, 445)).strip())
        score = _points(text_in(words, (345, 310, 515, 370)))
        if count == 0 and score and re.fullmatch(r'0/\d+', text_in(words, (1020, 93, 1115, 125)).strip()):
            return DrillScreen('sweep_empty', words, target=(1087, 196), count=0)
        if delta and count and score and int(delta[1]) - int(delta[2]) == count:
            return DrillScreen('sweep', words, target=(895, 466), count=count,
                               tickets=int(delta[1]), tickets_after=int(delta[2]), score=score)
    if (has(words, 'Drill End', (470, 100, 820, 160))
            and has(words, 'Mock Battle', (405, 185, 570, 220))
            and has(words, 'Confirm', (530, 490, 750, 560))
            and cyan(frame, (560, 500, 720, 550))):
        points = re.fullmatch(r'([\d,]+) Points', text_in(words, (480, 220, 805, 285)).strip())
        if points:
            return DrillScreen('mock_settlement', words, target=(640, 524), mock=True,
                               score=int(points[1].replace(',', '')))
    if (has(words, 'Drill End', (470, 100, 820, 160))
            and all(has(words, label, (400, 325, 550, 470)) for label in
                    ('1st Formation', '2nd Formation', '3rd Formation'))
            and not has(words, 'Mock Battle', (405, 185, 570, 220))
            and has(words, 'Confirm', (530, 490, 750, 560))
            and cyan(frame, (560, 500, 720, 550))):
        points = re.fullmatch(r'([\d,]+) Points', text_in(words, (480, 220, 805, 285)).strip())
        if points:
            return DrillScreen('settlement', words, target=(640, 524),
                               score=int(points[1].replace(',', '')))
    if (has(words, 'Use Drill Tickets to begin the drill?', (400, 310, 900, 370))
            and has(words, 'Used tickets cannot be returned.', (400, 355, 890, 405))
            and has(words, 'Confirm', (650, 460, 900, 550))
            and yellow(frame, (690, 480, 720, 520))):
        delta = re.fullmatch(r'(\d+)\s*→\s*(\d+)', text_in(words, (810, 438, 880, 470)).strip())
        if delta and int(delta[1]) == int(delta[2]) + 1:
            return DrillScreen('entry_confirm', words, target=(770, 501),
                               tickets=int(delta[1]), tickets_after=int(delta[2]))
    if (has(words, 'Tip!', (0, 0, 1280, 720))
            and has(words, 'Confirm', (550, 625, 730, 695))
            and yellow(frame, (540, 635, 735, 680))):
        return DrillScreen('tip', words, target=(640, 659))
    if (has(words, 'DEFEAT', (350, 100, 950, 310))
            and has(words, 'Time', (500, 280, 630, 345))
            and has(words, 'Confirm', (550, 625, 730, 695))
            and yellow(frame, (540, 635, 735, 680))):
        return DrillScreen('result', words, target=(640, 659), won=False,
                           elapsed_seconds=_clock(text_in(words, (640, 280, 790, 345)).strip()))
    elapsed = _clock(text_in(words, (1130, 18, 1260, 65)).strip())
    if (has(words, 'Battle Complete', (20, 0, 500, 90))
            and has(words, 'Total Points', (650, 0, 950, 80))
            and elapsed is not None
            and has(words, 'Confirm', (1090, 630, 1245, 694))
            and yellow(frame, (50, 15, 470, 66))
            and cyan(frame, (1130, 641, 1228, 691))):
        return DrillScreen('result', words, target=(1170, 665), won=True,
                           elapsed_seconds=elapsed,
                           score=_points(text_in(words, (890, 18, 1020, 65))))
    clock = _clock(text_in(words, (1065, 10, 1210, 59)).strip())
    stage = re.search(r'Shooting Drill Stage ([1-4])', text_in(words, (0, 0, 360, 65)))
    if stage and clock is not None and has(words, 'BOSS', (300, 60, 470, 115)):
        return DrillScreen('battle', words, stage=int(stage[1]), remaining_seconds=clock,
                           auto_on=auto_state(frame, words))
    if (has(words, 'Starting Skill', (480, 65, 820, 125))
            and has(words, 'Cancel', (430, 540, 610, 610))
            and has(words, 'Confirm', (670, 540, 830, 610))):
        return DrillScreen('skills', words, target=(1040, 90))
    # Modal controls take precedence over the still-readable page underneath.
    if (has(words, 'Create a Mock Battle room?', (350, 280, 940, 410))
            and has(words, 'Confirm', (650, 460, 900, 550))
            and yellow(frame, (690, 480, 720, 520))):
        return DrillScreen('mock_confirm', words, target=(770, 501))
    if (has(words, 'Drill Information', (480, 120, 805, 185))
            and bright(frame, (480, 135, 510, 175))):
        match = re.search(r'Stage ([1-4])\b', text_in(words, (400, 195, 805, 255)))
        target = ((643, 506) if has(words, 'Drill Start', (540, 475, 735, 540))
                  and (cyan(frame, (570, 490, 710, 525))
                       or yellow(frame, (570, 490, 710, 525))) else None)
        rounds = re.search(r'(\d)/3', text_in(words, (350, 400, 930, 475)))
        return DrillScreen('detail', words, target=target, stage=int(match[1]) if match else None,
                           remaining_rounds=int(rounds[1]) if rounds else None)
    if has(words, 'Using an Assistant', (480, 140, 800, 185)):
        hsv = cv2.cvtColor(frame[369:397, 588:615], cv2.COLOR_BGR2HSV)
        blue = ((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 115)
                & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 100))
        gold = ((hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 40)
                & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 100))
        if float(blue.mean()) >= .15 and float(gold.mean()) < .025:
            # Any unique equipment requires base rarity five, regardless of
            # which blue weapon-star digit is currently displayed.
            words = tuple(w for w in words if not (588 <= w.center[0] <= 615
                                                   and 369 <= w.center[1] <= 397))
            words += (Word('5', 1.0, (590, 371, 613, 395)),)
    shared = classify_assault(frame, words, home=home)
    if shared.kind in ('home', 'quick', 'receipt', 'assistant_confirm'):
        return shared
    title = has(words, 'Joint Firing Drill', (80, 0, 370, 55))
    clear = bright(frame, (350, 5, 390, 35))
    if title and clear and has(words, 'Quick Formation', (1120, 200, 1275, 250)):
        if has(words, 'Mobilize', (1060, 615, 1270, 705)):
            return DrillScreen('formation', words, target=(1177, 659), quick_target=(1200, 180))
    if title and clear and has(words, 'Drill Ticket', (1000, 65, 1130, 100)):
        counter = re.fullmatch(r'(\d+)/(\d+)', text_in(words, (1020, 93, 1115, 125)).strip())
        dates = [text_in(words, box).strip().lstrip('- ') for box in
                 ((1150, 65, 1275, 97), (1150, 95, 1275, 123))]
        period = '|'.join(dates) if all(re.fullmatch(r'\d{2}/\d{2} \d{2}:\d{2}', d) for d in dates) else None
        score = re.search(r"Today's Top Score\s*(?:e\s*)?(\d[\d,]*)\b", text_in(words, (20, 610, 350, 655)))
        return DrillScreen('menu', words, tickets=int(counter[1]) if counter else None,
                           period=period, score=int(score[1].replace(',', '')) if score else None,
                           active=has(words, 'Forfeit', (1080, 625, 1250, 685)),
                           mock=has(words, 'Mock Battle', (880, 605, 1060, 643)))
    if title and clear and has(words, 'Drill Open', (50, 220, 265, 270)):
        if has(words, 'Shooting Drill', (50, 350, 290, 410)) and has(words, 'Enter', (90, 410, 240, 465)):
            return DrillScreen('lobby', words, target=(165, 435))
    if has(words, 'Campaign', (80, 0, 350, 55)) and clear:
        targets = [w.center for w in words if w.normalized == 'joint firing drill']
        if len(targets) == 1:
            return DrillScreen('campaign', words, target=targets[0])
    return DrillScreen('unknown', words)


def _points(text):
    text = text.strip()
    return int(text.replace(',', '')) if re.fullmatch(r'\d[\d,]*', text) else None


class DrillVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = read_game_words(png, self.startup)
        home = {'home_left', 'home_right'} <= self.startup.matches(frame).keys()
        screen = classify_drill(frame, words, home=home)
        if screen.kind == 'assistant_confirm' and screen.confirm_target is None:
            # Full-screen OCR can miss the outlined level on the portrait.
            bounds = (590, 300, 654, 336)
            local = read_game_region(png, self.startup, bounds)
            extra = [Word(w.text, w.confidence, (w.box[0]+590, w.box[1]+300,
                                               w.box[2]+590, w.box[3]+300)) for w in local]
            words = tuple(w for w in words if not (590 <= w.center[0] <= 654
                                                   and 300 <= w.center[1] <= 336)) + tuple(extra)
            screen = classify_drill(frame, words, home=home)
        if screen.kind == 'formation' and not any(w.normalized == 'empty' for w in words):
            screen = replace(screen, team=read_formation(frame, words, startup=self.startup,
                                                        native_frame=decode_native_frame(png)))
        return screen
