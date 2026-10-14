"""Each reviewed season is recognized by its own identity and entered by its own route."""

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ba_automator.event_inspection import classify_event, enter_from_campaign
from ba_automator.event_priority import available_event, profiles
from ba_automator.event_story_vision import classify_story
from ba_automator.event_vision import classify_quest
from ba_automator.vision import Word

FIXTURES = Path(__file__).parent / 'fixtures/event_inspection'
CALM = 'calm-after-the-storm-global-2026'
AQUATIC = 'aquatic-showdown-global-2026-rerun'


def words(name):
    return [Word(w['text'], w['confidence'], tuple(w['box']))
            for w in json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))]


def page():
    # The fixtures hold OCR text only; recognition also needs the bright list panel.
    frame = np.full((720, 1280, 3), 70, np.uint8)
    frame[140:700, 690:1220] = 240
    frame[170:210, 480:620] = 240
    return frame


@pytest.mark.parametrize('at, expected', [
    ('2026-09-30T00:00:00+00:00', AQUATIC),
    ('2026-10-13T01:58:00+00:00', AQUATIC),
    ('2026-10-13T01:59:00+00:00', None),
    ('2026-10-13T02:00:00+00:00', CALM),
    ('2026-10-27T01:58:00+00:00', CALM),
    ('2026-10-27T01:59:00+00:00', None),
])
def test_one_season_is_playable_at_a_time(at, expected):
    profile = available_event(datetime.fromisoformat(at))
    assert (profile and profile['id']) == expected


def test_play_periods_do_not_overlap_and_callers_get_a_copy():
    spans = sorted((datetime.fromisoformat(p['starts_at']), datetime.fromisoformat(p['playable_until']))
                   for p in profiles())
    assert all(first[1] <= second[0] for first, second in zip(spans, spans[1:]))
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    available_event(now)['farm_order'].clear()
    assert available_event(now)['farm_order']


@pytest.mark.parametrize('name', ['calm-quests', 'calm-stories', 'calm-challenges'])
def test_new_event_page_is_identified_on_every_tab(name):
    screen = classify_event(page(), words(name))
    assert (screen.kind, screen.event, screen.ap) == ('event_page', CALM, 500)
    assert classify_event(page(), [Word(w.text, .1, w.box) for w in words(name)]) is None


def test_page_needs_this_events_title_and_card_draw_entrance():
    quests = words('calm-quests')
    untitled = [w for w in quests if 'calm' not in w.normalized and 'storm' not in w.normalized]
    assert classify_event(page(), untitled) is None
    no_entrance = [w for w in quests if w.normalized != 'draw a card']
    assert classify_event(page(), no_entrance) is None


def test_story_tab_rows_use_the_shared_story_layout():
    frame = page()
    frame[98:121, 740:785] = (40, 200, 250)     # selected Story tab
    frame[178:204, 1080:1172] = (250, 200, 90)  # Story 1's Enter button
    screen = classify_story(frame, words('calm-stories'))
    assert screen.kind == 'story_list'
    assert [(row.stage, row.target is not None, row.cleared) for row in screen.rows] == [
        (1, True, False), (2, False, False), (3, False, False),
        (4, False, False), (5, False, False), (6, False, False)]


def test_first_quest_mission_info_is_a_numbered_event_stage():
    screen = classify_quest(page(), words('calm-q1'))
    assert (screen.kind, screen.strategy, screen.stage, screen.ap) == ('detail', 'event', '1', 500)
    assert classify_event(page(), words('calm-q1')).kind == 'event_detail'


class Campaign:
    """A Campaign whose event card alternates between another season and this one."""

    def __init__(self, cards, destinations, *, capture_seconds=1.0):
        self.cards, self.destinations = list(cards), list(destinations)
        self.card_taps, self.taps, self.saved, self.time = [], [], [], 0.0
        self.capture_seconds, self.actions = capture_seconds, 0
        self.config = SimpleNamespace(package='game')
        self.device = SimpleNamespace(screenshot=self.screenshot, tap=self.device_tap,
                                      foreground_package=lambda: 'game')
        self.journal = SimpleNamespace(record=lambda *a, **k: None,
                                       save_image=lambda *args: self.saved.append(args))

    def clock(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds

    def navigate(self, source, destination, target):
        assert (source, destination, target) == ('home', 'campaign', (1200, 641))

    def screenshot(self):
        self.time += self.capture_seconds
        return self.cards.pop(0) if len(self.cards) > 1 else self.cards[0]

    def device_tap(self, x, y, *, deadline, monotonic):
        assert monotonic() <= deadline
        self.card_taps.append((x, y))
        return True

    def wait(self, kinds):
        kind, event = self.destinations.pop(0)
        return SimpleNamespace(screen=SimpleNamespace(kind=kind, event=event), capture=None)

    def tap(self, frame, target, detail):
        self.taps.append(target)

    def fail(self, message):
        raise AssertionError(message)


def profile():
    return available_event(datetime(2026, 10, 14, tzinfo=timezone.utc))


@pytest.fixture
def cards(monkeypatch):
    from ba_automator.event_inspection import card_template
    identity = profile()['identity']
    x1, y1, x2, y2 = identity['card_box']

    def decode(name):
        frame = np.zeros((720, 1280, 3), np.uint8)
        if name == 'calm':
            frame[y1:y2, x1:x2] = card_template(identity['card_template'])
        elif name == 'sliding':
            frame[y1:y2, x1 + 30:x2] = card_template(identity['card_template'])[:, :-30]
        return frame

    monkeypatch.setattr('ba_automator.event_inspection.decode_frame', decode)
    monkeypatch.setattr('ba_automator.event_state.observe', lambda config, profile: None)


def test_card_template_is_packaged_and_matches_only_itself(cards):
    from ba_automator.event_inspection import card_settled, decode_frame
    assert card_settled(decode_frame('calm'), profile())
    assert not card_settled(decode_frame('sliding'), profile())
    assert not card_settled(decode_frame('other'), profile())


def test_only_a_fresh_arrival_of_this_events_card_is_tapped(cards):
    # A card already on screen may be about to leave; wait for it to come back.
    runner = Campaign(['calm', 'other', 'sliding', 'calm'], [('event_page', CALM)])
    frame = enter_from_campaign(runner, None, profile())
    assert frame.screen.kind == 'event_page'
    assert runner.card_taps == [(100, 165)] and runner.taps == []
    assert runner.saved == [('event-card-1.png', 'calm')]


def test_slow_capture_is_not_trusted(cards):
    runner = Campaign(['other', 'calm'], [], capture_seconds=2.4)
    with pytest.raises(AssertionError, match='was not found on Campaign'):
        enter_from_campaign(runner, None, profile())
    assert runner.card_taps == []


def test_another_seasons_page_is_left_without_spending(cards):
    runner = Campaign(['other', 'calm', 'other', 'calm'],
                      [('other_event', None), ('event_page', CALM)])
    enter_from_campaign(runner, None, profile())
    assert runner.card_taps == [(100, 165), (100, 165)]
    assert runner.taps == [(1237, 24)]


def test_first_visit_play_guide_is_closed_before_verifying_the_page(cards):
    runner = Campaign(['other', 'calm'], [('event_guide', None), ('event_page', CALM)])
    enter_from_campaign(runner, None, profile())
    assert runner.taps == [(1185, 155)]


def test_missing_card_stops_before_any_tap(cards):
    runner = Campaign(['other'], [])
    with pytest.raises(AssertionError, match='was not found on Campaign'):
        enter_from_campaign(runner, None, profile())
    assert runner.card_taps == [] and runner.taps == []


def test_play_guide_overlay_is_its_own_screen():
    guide = [Word('Event', .99, (104, 9, 185, 39)),
             Word('Play Guide(1/7)', .99, (535, 135, 759, 174)),
             Word('This screen is a sample image.', .99, (743, 605, 984, 631))]
    assert classify_event(page(), guide).kind == 'event_guide'
    assert classify_event(page(), guide[:2]) is None
    assert classify_event(page(), [guide[0], Word('Play Guide', .99, (92, 90, 200, 116)), guide[2]]) is None

def test_episode_info_accepts_a_number_joined_to_its_title():
    # Live at 1440p the divider between "01" and the title was not read as a space.
    frame = page()
    frame[503:535, 560:720] = (40, 200, 250)  # yellow Enter Episode button
    detail = [Word('Event', .99, (104, 9, 185, 39)), Word('500/224', 1.0, (508, 9, 588, 37)),
              Word('Episode Info', .99, (544, 140, 737, 186)),
              Word('01Genryumon Main Room', 1.0, (348, 201, 742, 244)),
              Word('Expected Rewards', 1.0, (548, 271, 733, 298)),
              Word('500→490', 1.0, (665, 459, 761, 482)),
              Word('Enter Episode', 1.0, (534, 498, 746, 540))]
    screen = classify_story(frame, detail)
    assert (screen.kind, screen.stage, screen.cost, screen.after) == ('story_detail', 1, 10, 490)
    assert screen.target == (640, 520)
    twelve = [Word('12The Last Day', 1.0, w.box) if w.text.startswith('01') else w for w in detail]
    assert classify_story(frame, twelve).stage == 12
