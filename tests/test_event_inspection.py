from pathlib import Path
import json
import pytest
from ba_automator.event_inspection import classify_event
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
