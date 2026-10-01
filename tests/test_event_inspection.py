from pathlib import Path
import json
import pytest
import cv2
from types import SimpleNamespace
from ba_automator.event_inspection import (
    classify_event, enter_event, event_banner_centered,
)
from ba_automator.vision import decode_frame, Word

FIXTURES = Path(__file__).parent/'fixtures/event_inspection'


@pytest.mark.parametrize('name,kind', [('event-quests','event_page'),
                                     ('event-q1','event_detail'),
                                     ('event-treasure','event_board')])
def test_live_sanitized_screens(name, kind):
    frame = decode_frame((FIXTURES/(name+'.png')).read_bytes())
    words = [Word(w['text'],w['confidence'],tuple(w['box']))
             for w in json.loads((FIXTURES/(name+'.json')).read_text())]
    result = classify_event(frame, words)
    assert result.kind == kind
    if kind == 'event_detail': assert result.stars == 0
    if kind == 'event_board': assert result.round == 1
    assert classify_event(frame, []) is None
    assert classify_event(frame, [Word(w.text,.1,w.box) for w in words]) is None


def test_expired_concurrent_event_is_not_aquatic_showdown():
    frame = decode_frame((FIXTURES/'expired-event.png').read_bytes())
    words = [Word(w['text'], w['confidence'], tuple(w['box']))
             for w in json.loads((FIXTURES/'expired-event.json').read_text())]
    assert classify_event(frame, words).kind == 'other_event'


def banner_fixture(name):
    path = FIXTURES / 'banner' / name
    words = [Word(w['text'], w['confidence'], tuple(w['box']))
             for w in json.loads(path.with_suffix('.json').read_text())]
    return cv2.imread(str(path.with_suffix('.png'))), words


def test_banner_template_rejects_sliding_cards():
    for name in ('settled', 'settled-next'):
        assert event_banner_centered(banner_fixture(name)[0])
    for name in ('arriving', 'departing'):
        assert not event_banner_centered(banner_fixture(name)[0])


@pytest.mark.parametrize('expired_attempt', [False, True])
def test_entrance_waits_for_fresh_centered_arrival(monkeypatch, expired_attempt):
    # The old detector clicked while the event slid out and opened Recruitment.
    # Ignore a card already on screen; take the next arrival before it ages.
    names = ['settled', 'settled-next', 'departing', 'arriving', 'settled']
    if expired_attempt:
        names += ['settled-next', 'departing', 'arriving', 'settled']
    state = SimpleNamespace(time=0, captures=0, taps=[], saved=[], expired=False)
    def screenshot():
        name = names[state.captures]
        state.captures += 1
        state.time += .4
        return name
    def clock():
        return state.time
    def sleep(seconds):
        state.time += seconds
    def decode(name):
        import numpy as np
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[490:580, 20:295] = banner_fixture(name)[0]
        return frame
    def read(crop):
        pytest.fail('Carousel input must not wait for OCR')
    def tap(*args, **kwargs):
        if expired_attempt and not state.expired:
            assert state.captures == 5
            state.expired = True
            state.time += 2
            return False  # ADB declined the expired input; wait for a new cycle.
        assert state.captures == len(names)
        assert clock() <= kwargs['deadline']
        state.taps.append(args)
        return True
    def fail(message):
        pytest.fail(message)
    runner = SimpleNamespace(
        clock=clock, sleep=sleep, wall_clock=clock, actions=0, fail=fail,
        config=SimpleNamespace(package='game'),
        device=SimpleNamespace(screenshot=screenshot, foreground_package=lambda: 'game', tap=tap),
        journal=SimpleNamespace(record=lambda *a, **k: None,
                                save_image=lambda *args: state.saved.append(args)),
        wait=lambda expected: SimpleNamespace(screen=SimpleNamespace(kind='event_page')),
    )
    monkeypatch.setattr('ba_automator.event_inspection.decode_frame', decode)
    monkeypatch.setattr('ba_automator.event_priority.available_event', lambda now: {'id': 'event'})
    monkeypatch.setattr('ba_automator.event_state.observe', lambda *a: None)
    assert enter_event(runner, SimpleNamespace(read=read)).screen.kind == 'event_page'
    assert state.taps == [(150, 535)]
    assert state.saved == [('event-banner-1.png', 'settled')]
