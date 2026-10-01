"""Reviewed event Story layouts; recognition never authorizes a quest sweep."""
from dataclasses import dataclass
import re
from functools import lru_cache
from pathlib import Path
import cv2

from .ap_vision import ap_value
from .crafting_vision import bright, cyan, yellow, within
from .shop_vision import has, text_in, repair_receipt_prompt
from .vision import decode_frame, read_game_words


@dataclass(frozen=True)
class StoryRow:
    stage: int
    target: tuple | None
    cleared: bool = False


@dataclass(frozen=True)
class StoryScreen:
    kind: str
    ap: int | None = None
    stage: int | None = None
    cost: int | None = None
    after: int | None = None
    target: tuple | None = None
    rows: tuple = ()
    preset: bool = False


@lru_cache(maxsize=1)
def skip_template():
    return cv2.imread(str(Path(__file__).parent / 'assets/event-story-skip.png'))


def completed_icon(frame, y):
    """The thinner sword covers less of the marker box than a story book."""
    icon = cv2.cvtColor(frame[y+17:y+46, 719:746], cv2.COLOR_BGR2HSV)
    gold = ((icon[:, :, 0] >= 15) & (icon[:, :, 0] <= 40)
            & (icon[:, :, 1] >= 100) & (icon[:, :, 2] >= 180))
    return float(gold.mean()) > .2


def classify_story(frame, words):
    words = [w for w in words if w.confidence >= .9]
    ap = ap_value(words)
    from .shop_vision import classify_shop
    receipt = classify_shop(frame, words)
    if receipt.kind == 'receipt':
        return receipt
    # Story battles can supply a locked guest team. The disabled grey icon
    # differs from the active blue Quick Formation icon in ordinary quests.
    # Guest teams need not fill every slot: Story 10 supplies five students.
    # Use the disabled control and an occupied striker slot as evidence, not
    # a six-student count that would send us into an unavailable editor.
    if (has(words, 'formation', (70,0,330,65))
            and has(words, 'mobilize', (1050,625,1260,705))
            and has(words, 'quick formation', (1120,175,1280,235))
            and yellow(frame, (1120,630,1230,680))):
        icon = cv2.cvtColor(frame[159:198,1185:1220], cv2.COLOR_BGR2HSV)
        grey = ((icon[:,:,1] < 30) & (icon[:,:,2] >= 130) & (icon[:,:,2] <= 200)).mean()
        levels = [w for w in words if re.fullmatch(r'Lv\.?\s*[1-9]\d?', w.text.strip(), re.I)
                  and 500 <= w.center[1] <= 700]
        if grey > .15 and any(200 <= w.center[0] <= 1100 and w.center[1] < 590
                              for w in levels):
            return StoryScreen('event_formation', ap=ap, preset=True)
    if (has(words, 'summary', (540,110,740,165))
            and has(words, 'skip this story?', (530,420,770,470))
            and has(words, 'confirm', (670,490,860,550))
            and cyan(frame, (700,502,855,541))):
        return StoryScreen('story_skip', target=(770,520))
    if (has(words, 'auto', (1025,15,1135,65))
            and has(words, 'menu', (1140,15,1260,65))
            and bright(frame, (1050,20,1100,28))):
        score = cv2.matchTemplate(frame[94:146,1184:1239], skip_template(), cv2.TM_CCOEFF_NORMED)
        if float(score.max()) >= .92:
            return StoryScreen('story_menu', target=(1210,120))
        if bright(frame, (1160,20,1230,28)):
            return StoryScreen('story_dialogue', target=(1200,40))
    if (has(words, 'episode info', (440,130,830,195))
            and has(words, 'expected rewards', (460,260,810,310))
            and has(words, 'enter episode', (470,485,820,550))
            and yellow(frame,(560,503,720,535))):
        title = text_in(words,(345,199,915,248))
        stage = re.match(r'^(0[1-9]|1[0-2]|[1-9])\s', title)
        projection = re.fullmatch(r'(\d+)[→➜](\d+)',
                                  text_in(words,(660,449,780,490)).replace(' ',''))
        cost = int(projection[1])-int(projection[2]) if projection else None
        if stage:
            verified = projection and int(projection[1]) == ap and cost == 10
            return StoryScreen('story_detail', ap, int(stage[1]),
                               cost if verified else None,
                               int(projection[2]) if verified else None,
                               (640,520) if verified else None)
    if (has(words,'event',(80,0,260,60))
            and has(words,'story',(700,80,830,140))
            and has(words,'quest',(860,80,1020,140))
            and yellow(frame,(740,98,785,121))
            and bright(frame,(700,150,740,160))):
        rows = []
        for number in within(words,(700,150,772,690)):
            if not re.fullmatch(r'0[1-9]|1[0-2]',number.text.strip()):
                continue
            y = number.center[1]
            entries = [w for w in within(words,(1060,150,1200,690))
                       if w.normalized == 'enter' and abs(w.center[1]-y) < 35
                       and cyan(frame,(1080,w.center[1]-10,1170,w.center[1]+10))]
            target = entries[0].center if len(entries) == 1 else None
            # Completed episodes turn the book/sword below their number gold.
            # The row must also be unlocked; a tinted locked row is not proof.
            cleared = target is not None and completed_icon(frame, y)
            rows.append(StoryRow(int(number.text), target, cleared))
        if rows and len({r.stage for r in rows}) == len(rows):
            return StoryScreen('story_list',ap=ap,rows=tuple(rows))
    return None


class StoryVision:
    def __init__(self, startup, fallback):
        self.startup, self.fallback = startup, fallback

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = repair_receipt_prompt(frame, read_game_words(png, self.startup), self.startup)
        return (classify_story(frame, words)
                or self.fallback.analyze(png,billing=billing))
