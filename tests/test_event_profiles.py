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
    """A Campaign whose event card rotates between another season and this one."""

    def __init__(self, cards, destinations):
        self.cards, self.destinations = list(cards), list(destinations)
        self.taps, self.time, self.observed = [], 0.0, []
        self.config = SimpleNamespace(package='game')

    def clock(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds

    def navigate(self, source, destination, target):
        assert (source, destination, target) == ('home', 'campaign', (1200, 641))

    def wait(self, kinds):
        self.time += 2
        if kinds == 'campaign':
            return SimpleNamespace(screen=SimpleNamespace(kind='campaign'),
                                   capture=SimpleNamespace(png=self.cards.pop(0)))
        kind, event = self.destinations.pop(0)
        return SimpleNamespace(screen=SimpleNamespace(kind=kind, event=event), capture=None)

    def tap(self, frame, target, detail):
        self.taps.append((target, detail))

    def fail(self, message):
        raise AssertionError(message)


@pytest.fixture
def campaign_words(monkeypatch):
    card = words('calm-campaign')
    other = [w for w in card if 'calm' not in w.normalized and 'storm' not in w.normalized]
    other.append(Word('A Flower Blooms', .99, (60, 183, 170, 197)))
    monkeypatch.setattr('ba_automator.event_inspection.read_game_words',
                        lambda png, startup: {'calm': card, 'other': other}[png])
    monkeypatch.setattr('ba_automator.event_state.observe',
                        lambda config, profile: None)


def profile():
    return available_event(datetime(2026, 10, 14, tzinfo=timezone.utc))


def test_campaign_card_is_tapped_only_while_it_names_this_event(campaign_words):
    runner = Campaign(['other', 'other', 'calm'], [('event_page', CALM)])
    frame = enter_from_campaign(runner, None, profile())
    assert frame.screen.kind == 'event_page'
    assert [target for target, _ in runner.taps] == [(100, 165)]


def test_another_seasons_page_is_left_without_spending(campaign_words):
    runner = Campaign(['calm', 'calm'], [('other_event', None), ('event_page', CALM)])
    enter_from_campaign(runner, None, profile())
    assert [target for target, _ in runner.taps] == [(100, 165), (1237, 24), (100, 165)]


def test_missing_card_stops_before_any_tap(campaign_words):
    runner = Campaign(['other'] * 60, [])
    with pytest.raises(AssertionError, match='was not found on Campaign'):
        enter_from_campaign(runner, None, profile())
    assert runner.taps == []
