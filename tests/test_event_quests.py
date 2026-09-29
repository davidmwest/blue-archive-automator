from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from ba_automator.config import Config
from ba_automator import event_state
from ba_automator.event_priority import available_event
from ba_automator.event_vision import classify_quest
from ba_automator.event_quests import quest_target, battle_cost
from ba_automator.vision import Word,decode_frame
from ba_automator.tasks import task_plan

FIXTURES=Path(__file__).parent/'fixtures/event_quests'


def load(name):
    return (decode_frame((FIXTURES/(name+'.png')).read_bytes()),
            [Word(w['text'],w['confidence'],tuple(w['box'])) for w in json.loads((FIXTURES/(name+'.json')).read_text())])


@pytest.mark.parametrize('name,kind',[
    ('event-q1','detail'),('event-q1-auto-confirm','event_formation'),
    ('event-q1-quick','event_quick'),('event-q1-current','event_complete'),
    ('event-q1-rewards','event_bonus'),('event-q1-final','receipt')])
def test_actual_event_screens(name,kind):
    frame,words=load(name)
    result=classify_quest(frame,words)
    assert result.kind==kind
    assert classify_quest(frame,[]) is None
    assert classify_quest(frame,[Word(w.text,.1,w.box) for w in words]) is None
    if kind=='detail':
        assert result.stage=='1' and result.stars==0 and result.target is None


def test_quest_number_is_not_ocr_baseline_order():
    frame,words=load('event-q1')
    words=[Word('12',w.confidence,w.box) if w.text=='01' else w for w in words]
    assert classify_quest(frame,words).stage=='12'


def test_quest_entry_must_match_number_and_row():
    words=[Word('03',1,(711,390,750,425)),Word('Enter',1,(1090,400,1170,430))]
    assert quest_target(words,3)==(1130,415)
    assert quest_target(words,2) is None
    assert quest_target(words+[Word('Enter',1,(1090,410,1170,440))],3) is None


def test_pending_battle_blocks_replay_and_season_change(tmp_path):
    c=Config(serial='localhost:5695',package='com.nexon.bluearchive',state_dir=tmp_path)
    event=available_event(datetime(2026,9,30,tzinfo=timezone.utc))
    state=event_state.observe(c,event)
    state['pending']=dict(stage='1',ap_before=220,cost=10)
    event_state.write_state(c,state)
    with pytest.raises(RuntimeError,match='unresolved'):
        event_state.ensure_safe(c)
    with pytest.raises(RuntimeError,match='unresolved'):
        event_state.observe(c,dict(event,id='new-season'))


def test_clear_does_not_schedule_other_game_work(tmp_path):
    c=Config(serial='localhost:5695',package='com.nexon.bluearchive',state_dir=tmp_path)
    assert task_plan('clear_event',c)==('restart','clear_event')


@pytest.mark.parametrize('results,expected,summary', [
    ([3, 2, 3], [1, 2], '2/3 stars'),
    ([3, 3, 3], [1, 2, 3], 'All event quests'),
])
def test_clear_uses_auto_each_time_and_stops_below_three_stars(tmp_path, monkeypatch, results, expected, summary):
    from ba_automator.event_quests import EventClearRunner
    c = Config(serial='localhost:5695', package='com.nexon.bluearchive',
               state_dir=tmp_path/'state', run_dir=tmp_path/'runs')
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    r = EventClearRunner(c, None, None, vision=object(), wall_clock=lambda: now)
    profile = dict(available_event(now), quest_count=3)
    monkeypatch.setattr('ba_automator.event_quests.available_event', lambda _: profile)
    monkeypatch.setattr('ba_automator.event_quests.enter_event', lambda *args: None)
    def frame(kind='detail', stars=0, ap=220):
        return SimpleNamespace(screen=SimpleNamespace(kind=kind, stars=stars, ap=ap),
                               capture=SimpleNamespace(png=b''))
    monkeypatch.setattr('ba_automator.event_quests.quest_detail', lambda *args: frame())
    monkeypatch.setattr('ba_automator.event_quests.battle_cost', lambda *args: 10)
    monkeypatch.setattr('ba_automator.event_quests.close_detail', lambda *args: frame('event_list'))
    homes = []
    monkeypatch.setattr('ba_automator.event_quests.event_home', lambda *args: homes.append(True))
    r.wait = lambda kinds, **kw: frame(kinds if isinstance(kinds,str) else 'event_list')
    r.journal.save_image = lambda *args: None
    r.finish = lambda: 'finished'
    inputs, battles = [], []
    def tap(f, target, detail):
        inputs.append(detail)
        if detail.startswith('Mobilize'):
            # The durable intent must exist before any AP-spending input.
            assert event_state.read_state(c)['pending']['stage'] == str(len(battles)+1)
    r.tap = tap
    def finish_battle(stage):
        battles.append(stage)
        return frame(stars=results[stage-1],ap=210)
    r.finish_battle = finish_battle
    try:
        assert r.run() == 'finished'
        assert battles == expected
        assert inputs.count('Use default Auto Formation') == len(expected)
        saved = event_state.read_state(c)
        assert saved['pending'] is None
        assert summary in saved['summary']
        assert saved['declined'] and homes == [True]
    finally:
        r.journal.close()


def test_corrupt_event_state_blocks_spending(tmp_path):
    c = Config(serial='localhost:5695',package='com.nexon.bluearchive',state_dir=tmp_path)
    event_state.state_path(c).write_text('{broken')
    with pytest.raises(RuntimeError,match='Invalid event state'):
        event_state.ensure_safe(c)


def test_empty_pending_intent_is_corrupt_not_permission_to_spend(tmp_path):
    c = Config(serial='localhost:5695', package='com.nexon.bluearchive', state_dir=tmp_path)
    state = event_state.read_state(c)
    state['pending'] = {}
    event_state.write_state(c, state)
    with pytest.raises(RuntimeError, match='Invalid event state'):
        event_state.ensure_safe(c)
