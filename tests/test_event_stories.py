"""Story recognition and the paid-entry/resume contract."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import json

import pytest

from ba_automator import event_state, event_stories
from ba_automator.config import Config
from ba_automator.event_priority import available_event
from ba_automator.event_story_vision import classify_story, StoryScreen, StoryRow
from ba_automator.runtime import TaskError
from ba_automator.vision import Word, decode_frame

REAL_FINISH_STORY = event_stories.finish_story

FIXTURES = Path(__file__).parent / 'fixtures/event_stories'


def load(name):
    return (decode_frame((FIXTURES / f'{name}.png').read_bytes()),
            [Word(w['text'], w['confidence'], tuple(w['box']))
             for w in json.loads((FIXTURES / f'{name}.json').read_text())])


@pytest.mark.parametrize('name,kind', [('detail','story_detail'),('dialogue','story_dialogue'),
                                       ('menu','story_menu'),('skip','story_skip')])
def test_live_story_layouts(name, kind):
    frame, words = load(name)
    result = classify_story(frame, words)
    assert result.kind == kind
    assert classify_story(frame, []) is None
    assert classify_story(frame, [Word(w.text,.1,w.box) for w in words]) is None
    assert classify_story(frame // 3, words) is None
    if kind == 'story_detail':
        assert result.stage == 1 and result.cost == 10 and result.ap-result.after == 10


def test_unreadable_or_conflicting_story_cost_cannot_start():
    frame, words = load('detail')
    words = [w for w in words if not (660 <= w.box[0] and w.box[2] <= 780 and 449 <= w.box[1] < 490)]
    result = classify_story(frame, words)
    assert result.kind == 'story_detail' and result.cost is None and result.target is None


def test_completed_story_icon_is_distinct_from_new_and_locked_rows():
    frame, words = load('cleared')
    result = classify_story(frame, words)
    assert result.kind == 'story_list'
    assert result.rows[0].cleared and result.rows[0].stage == 1
    assert not result.rows[1].cleared and result.rows[1].target
    assert all(not row.cleared and row.target is None for row in result.rows[2:])
    assert classify_story(frame // 3, words) is None


def test_completed_battle_sword_is_distinct_from_uncleared_story_book():
    frame, words = load('battle_cleared')
    result = classify_story(frame, words)
    assert result.kind == 'story_list'
    assert [(row.stage, row.cleared) for row in result.rows[:3]] == [(1, True), (2, True), (3, False)]
    assert result.rows[2].target
    assert all(not row.cleared and row.target is None for row in result.rows[3:])
    assert classify_story(frame // 3, words) is None


def setup(tmp_path, monkeypatch, *, ap=130, count=3, completed=(), proof=True):
    c = Config(serial='localhost:5695', package='com.nexon.bluearchive', ap_floor=100,
               state_dir=tmp_path/'state', run_dir=tmp_path/'runs')
    profile = dict(available_event(datetime(2026,9,30,tzinfo=timezone.utc)), story_count=count)
    state = event_state.observe(c, profile)
    state['stories'] = {str(i): True for i in completed}
    event_state.write_state(c, state)
    frames, taps, events = [], [], []
    ctx = dict(ap=ap,stage=1,done=set(completed))
    def frame(screen):
        return NS(screen=screen,capture=NS(png=b'frame'))
    def row(r,stage):
        ctx['stage']=stage
        return frame(StoryScreen('story_list',ap=ctx['ap'])), StoryRow(stage,(1130,190),stage in ctx['done'])
    def wait(kinds, **kwargs):
        if isinstance(kinds,set) and 'story_detail' in kinds:
            return frame(StoryScreen('story_detail',ap=ctx['ap'],stage=ctx['stage'],cost=10,target=(640,520)))
        return frame(StoryScreen('story_list',ap=ctx['ap']))
    def tap(f,target,detail):
        taps.append(detail)
        if detail.startswith('Start event Story'):
            pending=event_state.read_state(c)['pending']
            assert pending['mode']=='story' and pending['stage']==str(ctx['stage'])
            assert pending['ap_before']-pending['cost']>=100
            ctx['ap']-=10
    def finish(r,stage):
        if proof:ctx['done'].add(stage)
    def fail(message):raise TaskError(message,tmp_path)
    runner = NS(config=c, vision=object(), startup=None, started=0, actions=0, clock=lambda:0,
                run_dir=tmp_path, journal=NS(save_image=lambda *a:None), wait=wait,
                tap=tap, important=lambda *a,**kw:events.append((a,kw)), fail=fail)
    monkeypatch.setattr(event_stories,'story_row',row)
    monkeypatch.setattr(event_stories,'finish_story',finish)
    return runner,profile,frame(StoryScreen('event_list')),ctx,taps,events


def test_story_sequence_persists_before_spending_and_resumes(tmp_path,monkeypatch):
    r,p,f,ctx,taps,events=setup(tmp_path,monkeypatch,ap=130,completed=(1,))
    original=r.vision
    assert event_stories.complete_stories(r,p,f) is None
    assert ctx['ap']==110
    assert [x for x in taps if x.startswith('Start event Story')]==['Start event Story 2','Start event Story 3']
    assert event_state.read_state(r.config)['stories']=={'1':True,'2':True,'3':True}
    assert event_state.read_state(r.config)['pending'] is None
    assert r.vision is original


def test_floor_stops_stories_and_keeps_farming_on_hold(tmp_path,monkeypatch):
    r,p,f,ctx,taps,_=setup(tmp_path,monkeypatch,ap=115)
    summary=event_stories.complete_stories(r,p,f)
    assert 'floor' in summary and 'hold' in summary
    assert ctx['ap']==105 and event_state.read_state(r.config)['stories']=={'1':True}
    assert len([x for x in taps if x.startswith('Start event Story')])==1


def test_uncertain_completion_keeps_pending_intent(tmp_path,monkeypatch):
    r,p,f,ctx,taps,_=setup(tmp_path,monkeypatch,proof=False)
    with pytest.raises(TaskError,match='completion did not verify'):
        event_stories.complete_stories(r,p,f)
    assert event_state.read_state(r.config)['stories']=={}
    with pytest.raises(RuntimeError,match='unresolved'):
        event_state.ensure_safe(r.config)


def test_visit_budget_defers_without_spending(tmp_path,monkeypatch):
    r,p,f,ctx,taps,_=setup(tmp_path,monkeypatch)
    r.clock=lambda:400
    summary=event_stories.complete_stories(r,p,f)
    assert r.event_deferred and 'next visit' in summary and ctx['ap']==130
    assert event_state.read_state(r.config)['pending'] is None


def test_season_change_resets_story_progress(tmp_path,monkeypatch):
    r,p,*_=setup(tmp_path,monkeypatch,completed=(1,2))
    assert event_state.observe(r.config,dict(p,id='new-event'))['stories']=={}


def test_locked_guest_team_can_mobilize_without_quick_formation():
    frame, words = load('preset')
    result = classify_story(frame, words)
    assert result.kind == 'event_formation' and result.preset and result.ap == 131
    assert classify_story(frame // 3, words) is None
    assert classify_story(frame, []) is None
    incomplete = [w for w in words if w.text != 'Lv.25']
    assert classify_story(frame, incomplete) is None


@pytest.mark.parametrize('preset', [True, False])
def test_battle_story_handles_dialogue_then_formation_before_rewards(tmp_path, monkeypatch, preset):
    r, profile, _, _, taps, _ = setup(tmp_path, monkeypatch)
    state = event_state.read_state(r.config)
    state['pending'] = dict(mode='story',stage='2',ap_before=130,cost=10,run_dir=str(tmp_path))
    event_state.write_state(r.config,state)
    def frame(kind, **kw):
        return NS(screen=NS(kind=kind, **kw),capture=NS(png=b'frame'))
    frames = iter([frame('story_dialogue',target=(1200,40)),
                   frame('story_menu',target=(1210,120)),
                   frame('story_skip',target=(770,520)),
                   frame('event_formation',preset=preset,ap=131),
                   frame('event_battle',auto=False),
                   frame('event_complete',target=(1170,661)),
                   frame('receipt',target=(640,631))])
    r.capture=lambda:next(frames)
    r.sleep=lambda _:None
    r.wait=lambda kind,**_:frame(kind,ap=131)
    r.tap=lambda f,t,detail:taps.append(detail)
    monkeypatch.setattr(event_stories,'inspect_receipt',lambda r,f,p:f)
    # setup replaces finish_story to test the outer sequence; restore the real
    # function for the dialogue/formation integration test.
    result = REAL_FINISH_STORY(r,2)
    assert result.screen.kind == 'story_list'
    assert any('Mobilize' in x for x in taps)
    assert any('Enable Auto' in x for x in taps)
    assert any('Quick Formation' in x for x in taps) is not preset
    assert event_state.read_state(r.config)['pending']['ap_before'] == 131


def test_story_floor_is_rechecked_after_narrative_before_mobilizing(tmp_path, monkeypatch):
    r,*_=setup(tmp_path,monkeypatch)
    state=event_state.read_state(r.config)
    state['pending']=dict(mode='story',stage='2',ap_before=110,cost=10,run_dir=str(tmp_path))
    event_state.write_state(r.config,state)
    r.capture=lambda:NS(screen=StoryScreen('event_formation',ap=109,preset=True))
    r.sleep=lambda _:None
    r.tap=lambda *a:pytest.fail('Must not mobilize below the AP floor')
    with pytest.raises(TaskError,match='AP changed'):
        REAL_FINISH_STORY(r,2)
    assert event_state.read_state(r.config)['pending']
