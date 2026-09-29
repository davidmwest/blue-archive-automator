"""Event quest details and battle setup; all coordinates are canonical."""
import re
from dataclasses import dataclass
from .ap_vision import APScreen, ap_value, mission_stars, projected_ap
from .crafting_vision import bright, cyan, yellow, number, within
from .shop_vision import has, text_in
from .vision import decode_frame, read_game_words
from .event_inspection import classify_event
from .assault_battle_vision import auto_state


@dataclass(frozen=True)
class BattleScreen:
    kind: str
    ap: int | None = None
    auto: bool | None = None
    target: tuple | None = None
    cost: int | None = None


def classify_quest(frame, words):
    words = [w for w in words if w.confidence >= .9]
    ap = ap_value(words)
    if (has(words, 'reward acquired', (190,100,1090,210))
            and has(words,'go to lobby',(390,620,630,700))
            and has(words,'confirm',(650,620,900,700))
            and yellow(frame,(700,640,850,680))):
        return BattleScreen('receipt',target=(775,660))
    if (has(words,'final rewards earned',(300,410,1000,460))
            and has(words,'confirm',(550,630,760,705))
            and yellow(frame,(570,650,720,690))):
        return BattleScreen('event_bonus',target=(640,665))
    if (has(words,'battle complete',(15,0,490,90))
            and has(words,'confirm',(1090,625,1260,705))
            and cyan(frame,(1120,640,1220,688))):
        return BattleScreen('event_complete',target=(1170,661))
    if (has(words, 'mission info' , (430,110,870,170))
            and has(words, 'start mission', (760,495,1120,570))
            and bright(frame,(500,180,600,200))):
        title = ' '.join(w.text for w in sorted(within(words,(130,185,640,245)),key=lambda w:w.box[0]))
        # Prefer the longest match (10–12 must not become quest 1).
        match = re.match(r'^(0[1-9]|1[0-2]|[1-9])(?=[^0-9])', title)
        if match:
            count = number(words,(904,277,970,330))
            projection = projected_ap(words)
            capacity = number(words,(493,0,620,48),r'(\d+)/(\d+)')
            qty = count[0] if count else 0
            total = projection[0]-projection[1] if projection else 0
            cost = total // qty if qty and total > 0 and total % qty == 0 else None
            return APScreen('detail', ap=ap, ap_capacity=capacity[1] if capacity else None,
                            strategy='event', stage=str(int(match[1])), stars=mission_stars(frame),
                            count=qty, cost=cost, after=projection[1] if projection else None,
                            target=(937,405) if (cost and projection[0] == ap
                                and cyan(frame,(810,387,1050,430))) else None)
    if (has(words,'quick formation',(470,62,850,120))
            and has(words,'auto',(565,550,680,635))
            and has(words,'confirm',(1065,550,1245,635))
            and cyan(frame,(1110,565,1220,620))):
        return BattleScreen('event_quick',ap)
    if (any(w.normalized.startswith('formation') for w in within(words,(70,0,330,65)))
            and has(words,'mobilize',(1050,625,1260,705))
            and has(words,'quick formation',(1120,175,1280,235))):
        return BattleScreen('event_formation',ap)
    if (has(words, 'event', (80,0,260,60))
            and has(words, 'quest', (860,80,1020,140))
            and has(words, 'treasure hunt', (430,610,605,710))
            and has(words, 'a flower blooms among the hundred', (0,120,670,600))
            and bright(frame, (700,150,740,170))
            and classify_event(frame,words) is None):
        # This layout can be shared by reruns. Only enter_event's unique
        # destination check can establish identity before quest navigation.
        return BattleScreen('event_list', ap=ap)
    auto = auto_state(frame,words)
    if auto is not None:
        return BattleScreen('event_battle', auto=auto)
    return None


class QuestVision:
    def __init__(self, startup, fallback):
        self.startup, self.fallback = startup, fallback

    def analyze(self,png,*,billing=False):
        frame = decode_frame(png)
        words = read_game_words(png,self.startup)
        return (classify_quest(frame,words) or classify_event(frame,words)
                or self.fallback.analyze(png,billing=billing))
