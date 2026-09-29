"""Read-only Aquatic Showdown navigation, shared with the serial AP runner."""
from dataclasses import dataclass
import re

from .ap_vision import ap_value, mission_stars
from .crafting_vision import bright, within
from .shop_vision import has
from .vision import decode_frame, read_game_words


@dataclass(frozen=True)
class EventScreen:
    kind: str
    ap: int | None = None
    stars: int | None = None
    round: int | None = None


def classify_event(frame, words):
    words = [w for w in words if w.confidence >= .9]
    def text(bounds):
        return ''.join(w.normalized for w in within(words, bounds)).replace(' ', '')
    ap = ap_value(words)
    if (has(words, 'treasure hunt', (70, 0, 370, 60))
            and 'remainingslots' in text((700, 130, 1040, 200))):
        matches = re.findall(r'currentroundround(\d+)', text((650, 120, 1100, 170)))
        if len(matches) == 1:
            return EventScreen('event_board', ap=ap, round=int(matches[0]))
    if (has(words, 'mission info', (430, 110, 870, 170))
            and 'sunkissedbeach' in text((130, 180, 650, 250))
            and has(words, 'start mission', (760, 495, 1120, 560))
            and bright(frame, (500, 180, 600, 200))):
        return EventScreen('event_detail', ap=ap, stars=mission_stars(frame))
    if (has(words, 'event', (80, 0, 260, 60))
            and ('fairandsquareaquaticshowdown' in text((0, 120, 670, 600))
                 or ('aflowerbloomsamongthehundred' in text((0, 120, 670, 600))
                     and has(words, 'sun-kissed beach', (770, 160, 1040, 235))))
            and has(words, 'quest', (860, 80, 1020, 140))
            and 'treasurehunt' in text((430, 610, 605, 710))
            and bright(frame, (700, 150, 740, 170))):
        return EventScreen('event_page', ap=ap)
    if has(words, 'recruitment', (70, 0, 350, 65)) or (has(words, 'event', (80, 0, 260, 60)) and 'eventperiodconcluded' in text((650, 80, 1200, 550))):
        return EventScreen('other_event', ap=ap)
    return None


class EventVision:
    def __init__(self, startup, fallback):
        self.startup, self.fallback = startup, fallback

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = read_game_words(png, self.startup)
        return classify_event(frame, words) or self.fallback.analyze(png, billing=billing)


def inspect_event(runner, startup):
    """Caller holds the instance lock. No AP, currency, or reward inputs exist."""
    previous = runner.vision
    runner.vision = EventVision(startup, previous)
    try:
        for entrance_attempt in range(3):
            runner.wait('home')
            # The Campaign carousel contains concurrent and expired events. Use
            # the home banner and verify the unique destination before proceeding.
            end = runner.clock() + 45
            while True:
                at = runner.clock()
                png = runner.device.screenshot()
                words = startup.read(decode_frame(png)[475:585, 20:295])
                banner = ''.join(w.normalized for w in words if w.confidence >= .9).replace(' ', '')
                if 'amongthehundred' in banner and runner.clock() - at < 1.5:
                    break
                if runner.clock() >= end:
                    runner.fail('Aquatic Showdown home banner was not verified; event AP remains reserved')
                runner.sleep(.2)
            if runner.device.foreground_package() != runner.config.package:
                runner.fail('Foreground changed during event entrance inspection')
            runner.journal.record('intent', operation='tap', detail='Open event home banner', target=(150, 535))
            if not runner.device.tap(150, 535, deadline=at + 2, monotonic=runner.clock):
                runner.fail('Event banner input expired')
            runner.actions += 1
            frame = runner.wait({'event_page', 'other_event'})
            if frame.screen.kind == 'event_page':
                break
            runner.tap(frame, (1237, 24), 'Leave concurrent event without spending')
        else:
            runner.fail('Event banner changed during entry; no resources spent')
        runner.tap(frame, (937, 110), 'Inspect event quests')
        frame = runner.wait('event_page')
        # Verify the top quest independently before opening its unpaid details.
        words = read_game_words(frame.capture.png, startup)
        if not has(words, 'sun-kissed beach', (770, 160, 1040, 235)):
            runner.fail('First event quest is not visible; event AP remains reserved')
        runner.tap(frame, (1126, 198), 'Inspect event Quest 1 prerequisites')
        frame = runner.wait('event_detail')
        stars = frame.screen.stars
        runner.phase(f'Event Quest 1: {stars}/3 stars; no resources spent')
        runner.tap(frame, (1127, 139), 'Close event quest details')
        frame = runner.wait('event_page')
        runner.tap(frame, (515, 663), 'Inspect Treasure Hunt round')
        frame = runner.wait('event_board')
        round_number = frame.screen.round
        evidence = str(runner.run_dir / runner.last_frame) if runner.last_frame else None
        runner.important('event_inspected',
                         f'Aquatic Showdown: Quest 1 has {stars}/3 stars; Treasure Hunt round {round_number}. No resources spent.',
                         stars=stars, round=round_number, evidence=evidence)
        runner.tap(frame, (1237, 24), 'Return home after event inspection')
        runner.wait('home')
        return 'needs_first_clears' if stars < 3 else 'needs_executor'
    finally:
        runner.vision = previous
