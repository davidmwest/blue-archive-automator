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
    ([3, 2, 3], [1, 2, 3], 'below three stars: 2 (2/3); replay them by hand'),
    ([3, 0, 3], [1, 2], 'Stopped at event Quest 2: not cleared'),
    ([3, 3, 3], [1, 2, 3], 'All event quests'),
])
def test_clear_tries_each_quest_once_and_stops_only_when_one_is_not_cleared(tmp_path, monkeypatch, results, expected, summary):
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


@pytest.mark.parametrize('quantity,after', [(1,743),(49,23)])
def test_sweep_quantity_crop_recovers_real_missed_digits(quantity, after, monkeypatch):
    from ba_automator.event_vision import QuestVision
    from ba_automator.vision import StartupVision
    frame, words = load(f'event-sweep-{quantity}')
    assert classify_quest(frame, words).cost is None
    monkeypatch.setattr('ba_automator.event_vision.read_game_words', lambda *args: words)
    png = (FIXTURES/f'event-sweep-{quantity}.png').read_bytes()
    screen = QuestVision(StartupVision(), None).analyze(png)
    assert (screen.stage, screen.stars, screen.count, screen.cost, screen.after) == (
        '5', 3, quantity, 15, after)
    assert screen.target == (937,405)


@pytest.mark.parametrize('reads', [
    [[], []],
    [[Word('49',.94,(0,0,20,20))]],
    [[Word('49',1,(0,0,20,20))], [Word('48',1,(0,0,20,20))]],
    [[Word('4g',1,(0,0,20,20))]],
])
def test_sweep_quantity_crop_rejects_uncertain_reads(reads):
    from ba_automator.event_vision import reread_sweep_count
    from ba_automator.vision import decode_native_frame
    png = (FIXTURES/'event-sweep-49.png').read_bytes()
    results = iter(reads)
    assert reread_sweep_count(decode_native_frame(png),
                             SimpleNamespace(read=lambda crop: next(results))) is None

def test_unreadable_reward_list_is_recorded_without_stranding_the_battle(monkeypatch, tmp_path):
    # Live, Quest 1's first-clear rewards filled a scrolling row the loot reader
    # could not page through. The win was real, but the job failed and left the
    # paid entry pending, which holds every later event spend.
    from types import SimpleNamespace
    from ba_automator import event_quests
    from ba_automator.runtime import TaskError

    def unreadable(runner, frame, evidence):
        raise TaskError('Reward receipt pages did not overlap; totals need review', tmp_path)

    monkeypatch.setattr(event_quests, 'inspect_receipt', unreadable)
    events, notes = [], []
    fresh = SimpleNamespace(screen=SimpleNamespace(kind='receipt', target=(775, 660)))
    runner = SimpleNamespace(
        journal=SimpleNamespace(record=lambda event, **fields: events.append((event, fields))),
        important=lambda action, detail, **fields: notes.append((action, detail)),
        wait=lambda kinds, timeout=40: fresh)
    result = event_quests.itemize_or_keep(runner, object(), tmp_path / 'quest-1-receipt.png', 'Event Quest 1')
    assert result is fresh
    assert events[0][0] == 'loot_receipt_incomplete' and 'did not overlap' in events[0][1]['detail']
    assert notes[0][0] == 'event_loot_incomplete' and notes[0][1].startswith('Event Quest 1:')


def test_readable_reward_list_is_returned_unchanged(monkeypatch, tmp_path):
    from ba_automator import event_quests
    inspected = object()
    monkeypatch.setattr(event_quests, 'inspect_receipt', lambda runner, frame, evidence: inspected)
    assert event_quests.itemize_or_keep(object(), object(), tmp_path / 'r.png', 'Event Quest 2') is inspected


# Challenges -------------------------------------------------------------------

CALM_NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def challenge_runner(tmp_path, monkeypatch, details, battles, *, challenge_count=2, ap_floor=100):
    """All quests are three-starred; ``details`` scripts each Challenge inspection."""
    from ba_automator.event_quests import EventClearRunner
    c = Config(serial='localhost:5695', package='com.nexon.bluearchive', ap_floor=ap_floor,
               state_dir=tmp_path/'state', run_dir=tmp_path/'runs')
    r = EventClearRunner(c, None, None, vision=object(), wall_clock=lambda: CALM_NOW)
    profile = dict(available_event(CALM_NOW), quest_count=2, challenge_count=challenge_count)
    monkeypatch.setattr('ba_automator.event_quests.available_event', lambda _: profile)
    monkeypatch.setattr('ba_automator.event_quests.enter_event', lambda *args: None)

    def frame(kind='detail', stars=3, ap=220):
        return SimpleNamespace(screen=SimpleNamespace(kind=kind, stars=stars, ap=ap),
                               capture=SimpleNamespace(png=b''))
    opened = []

    def detail(runner, stage, *, allow_unavailable=False, tab='quest'):
        opened.append((tab, stage))
        if tab == 'quest':
            return frame()
        stars = details[stage - 1]
        return None if stars is None else frame(stars=stars)
    monkeypatch.setattr('ba_automator.event_quests.quest_detail', detail)
    monkeypatch.setattr('ba_automator.event_quests.battle_cost', lambda *args: 5)
    monkeypatch.setattr('ba_automator.event_quests.close_detail', lambda *args: frame('event_list'))
    monkeypatch.setattr('ba_automator.event_quests.event_home', lambda *args: None)
    r.wait = lambda kinds, **kw: frame(kinds if isinstance(kinds, str) else (
        'event_quick' if 'event_quick' in kinds else 'event_list'))
    r.journal.save_image = lambda *args: None
    r.finish = lambda: 'finished'
    r.inputs, r.fought, r.opened = [], [], opened

    def tap(f, target, detail):
        r.inputs.append(detail)
        if detail.startswith('Mobilize'):
            pending = event_state.read_state(c)['pending']
            assert pending['mode'] == 'challenge' and pending['cost'] == 5
    r.tap = tap

    def finish_battle(stage, tab='quest'):
        r.fought.append((tab, stage))
        return frame(stars=battles[stage - 1], ap=215)
    r.finish_battle = finish_battle
    return r, c


def test_challenges_follow_three_star_quests_and_stop_at_the_first_shortfall(tmp_path, monkeypatch):
    r, c = challenge_runner(tmp_path, monkeypatch, details=[0, 0, 0], battles=[3, 1, 3], challenge_count=3)
    try:
        assert r.run() == 'finished'
        assert r.fought == [('challenge', 1), ('challenge', 2)]
        assert r.inputs.count('Use default Auto Formation') == 2
        assert 'Mobilize event Challenge 2' in r.inputs
        saved = event_state.read_state(c)
        assert saved['challenges'] == {'1': 3, '2': 1} and saved['pending'] is None
        assert saved['summary'] == 'Stopped at event Challenge 2: 1/3 stars'
    finally:
        r.journal.close()


def test_cleared_challenges_are_not_replayed_and_a_recorded_shortfall_is_left_to_the_player(tmp_path, monkeypatch):
    r, c = challenge_runner(tmp_path, monkeypatch, details=[3, 1], battles=[3, 3])
    state = event_state.observe(c, available_event(CALM_NOW))
    state['challenges'] = {'2': 1}
    event_state.write_state(c, state)
    try:
        assert r.run() == 'finished'
        assert r.fought == [] and not any(d.startswith('Mobilize') for d in r.inputs)
        saved = event_state.read_state(c)
        assert saved['challenges'] == {'1': 3, '2': 1}
        assert 'Challenge 2 stays at 1/3 stars' in saved['summary']
    finally:
        r.journal.close()


def test_all_challenges_cleared_and_locked_challenge_summaries(tmp_path, monkeypatch):
    r, c = challenge_runner(tmp_path, monkeypatch, details=[0, 0], battles=[3, 3])
    try:
        r.run()
        assert event_state.read_state(c)['summary'] == 'All event quests and challenges have three-star clears'
    finally:
        r.journal.close()
    r, c = challenge_runner(tmp_path / 'locked', monkeypatch, details=[3, None], battles=[3, 3])
    try:
        r.run()
        assert r.fought == [] and event_state.read_state(c)['summary'] == 'Event Challenge 2 is not open yet'
    finally:
        r.journal.close()


def test_challenge_keeps_the_ap_floor(tmp_path, monkeypatch):
    r, c = challenge_runner(tmp_path, monkeypatch, details=[0, 0], battles=[3, 3], ap_floor=216)
    try:
        r.run()
        assert r.fought == []
        assert 'before Challenge 1: keeping 216 AP' in event_state.read_state(c)['summary']
    finally:
        r.journal.close()


def test_lost_battle_is_confirmed_and_read_back_from_the_stage(tmp_path, monkeypatch):
    from ba_automator import event_quests
    c = Config(serial='localhost:5695', package='com.nexon.bluearchive',
               state_dir=tmp_path/'state', run_dir=tmp_path/'runs')
    r = event_quests.EventClearRunner(c, None, None, vision=object(), wall_clock=lambda: CALM_NOW)
    lost = SimpleNamespace(screen=SimpleNamespace(kind='event_defeat', target=(640, 659)),
                           capture=SimpleNamespace(png=b'', deadline=float('inf')))
    r.capture = lambda: lost
    r.journal.save_image = lambda *args: None
    taps, notes, read_back = [], [], []
    r.tap = lambda frame, target, detail: taps.append((target, detail))
    r.important = lambda action, detail, **fields: notes.append(action)
    monkeypatch.setattr(event_quests, 'quest_detail',
                        lambda runner, stage, **kw: read_back.append((stage, kw)) or 'detail')
    try:
        assert r.finish_battle(2, 'challenge') == 'detail'
        assert taps == [((640, 659), 'Confirm lost event battle')]
        assert notes == ['event_battle_lost'] and read_back == [(2, {'tab': 'challenge'})]
    finally:
        r.journal.close()


def test_defeat_page_and_open_tab_are_recognized():
    import numpy as np
    from ba_automator import event_quests
    frame = np.full((720, 1280, 3), 60, np.uint8)
    words = [Word('DEFEAT', .99, (470, 150, 810, 250)), Word('Confirm', .99, (590, 640, 690, 678))]
    assert classify_quest(frame, words) is None            # Confirm is not lit
    frame[635:680, 540:735] = (40, 200, 250)
    screen = classify_quest(frame, words)
    assert (screen.kind, screen.target) == ('event_defeat', (640, 659))
    assert classify_quest(frame, words[1:]) is None

    page = np.full((720, 1280, 3), 243, np.uint8)
    page[82:138, 1030:1210] = 60                            # Challenge is the open tab
    original = event_quests.decode_frame
    event_quests.decode_frame = lambda png: page
    try:
        assert event_quests.tab_selected(b'', 'challenge') and not event_quests.tab_selected(b'', 'quest')
    finally:
        event_quests.decode_frame = original


def test_a_won_battle_is_confirmed_from_a_fresh_frame(tmp_path, monkeypatch):
    # Live, saving the native result image before tapping let the frame's input
    # window lapse; the tap was refused and the won battle was left pending.
    from ba_automator import event_quests
    c = Config(serial='localhost:5695', package='com.nexon.bluearchive',
               state_dir=tmp_path/'state', run_dir=tmp_path/'runs')
    r = event_quests.EventClearRunner(c, None, None, vision=object(), wall_clock=lambda: CALM_NOW)
    now = [100.0]
    r.clock = lambda: now[0]
    frames = iter([0.5, 4.0])            # seconds of input window left on each capture

    def capture():
        left = next(frames)
        return SimpleNamespace(screen=SimpleNamespace(kind='event_complete', target=(1170, 661)),
                               capture=SimpleNamespace(png=b'', deadline=now[0] + left))
    r.capture = capture
    taps, saved = [], []
    r.tap = lambda frame, target, detail: taps.append(
        getattr(frame.capture, 'deadline', now[0]) - now[0])
    r.journal.save_image = lambda name, png: saved.append(name)
    receipt = SimpleNamespace(screen=SimpleNamespace(kind='receipt', target=(775, 660)),
                              capture=SimpleNamespace(png=b''))
    r.wait = lambda kinds, **kw: receipt if 'receipt' in kinds else SimpleNamespace(
        screen=SimpleNamespace(kind='event_list'))
    monkeypatch.setattr(event_quests, 'itemize_or_keep', lambda runner, frame, path, label: frame)
    monkeypatch.setattr(event_quests, 'quest_detail', lambda runner, stage, **kw: 'detail')
    try:
        assert r.finish_battle(10) == 'detail'
        assert taps[0] == 4.0 and saved[0] == 'quest-10-result.png'
    finally:
        r.journal.close()


def test_a_recorded_star_shortfall_is_not_replayed(tmp_path, monkeypatch):
    # Live, Quest 11 cleared at 2/3 stars with default Auto Formation.
    r, c = challenge_runner(tmp_path, monkeypatch, details=[3, 3], battles=[3, 3])
    state = event_state.observe(c, available_event(CALM_NOW))
    state['clears'] = {'1': 3, '2': 2}
    event_state.write_state(c, state)
    monkeypatch.setattr('ba_automator.event_quests.quest_detail',
                        lambda runner, stage, **kw: SimpleNamespace(
                            screen=SimpleNamespace(kind='detail', stars=2 if (kw.get('tab', 'quest'), stage) == ('quest', 2) else 3, ap=220),
                            capture=SimpleNamespace(png=b'')))
    try:
        assert r.run() == 'finished'
        assert r.fought == [] and not any(d.startswith('Mobilize') for d in r.inputs)
        saved = event_state.read_state(c)
        assert saved['clears'] == {'1': 3, '2': 2}
        assert saved['summary'] == 'Event Quests below three stars: 2 (2/3); replay them by hand'
    finally:
        r.journal.close()


def test_a_challenges_fixed_team_is_kept_when_quick_formation_does_not_open(tmp_path, monkeypatch):
    # Live, Challenge 1 fielded four event-provided Lv.50 students; Quick
    # Formation did nothing, and the job failed waiting for its dialog.
    from ba_automator import event_quests
    r, c = challenge_runner(tmp_path, monkeypatch, details=[0, 3], battles=[3, 3])
    now = [0.0]
    r.clock = lambda: now[0]
    r.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    formation = SimpleNamespace(screen=SimpleNamespace(kind='event_formation', stars=0, ap=220),
                                capture=SimpleNamespace(png=b''))
    plain = r.wait
    r.wait = lambda kinds, **kw: formation if 'event_quick' in kinds else plain(kinds, **kw)
    team = [Word('Lv.50', .99, (x - 30, 545, x + 30, 570)) for x in (232, 456, 680, 904)]
    monkeypatch.setattr(event_quests, 'read_game_words', lambda png, startup: team)
    try:
        assert r.run() == 'finished'
        assert r.fought == [('challenge', 1)]
        assert 'Use default Auto Formation' not in r.inputs
        assert 'Mobilize event Challenge 1' in r.inputs
    finally:
        r.journal.close()
    monkeypatch.setattr(event_quests, 'read_game_words', lambda png, startup: team[:3])
    r, c = challenge_runner(tmp_path / 'partial', monkeypatch, details=[0, 3], battles=[3, 3])
    r.clock = lambda: now[0]
    r.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    plain = r.wait
    r.wait = lambda kinds, **kw: formation if 'event_quick' in kinds else plain(kinds, **kw)
    try:
        with pytest.raises(Exception, match='no full team is placed'):
            r.run()
        assert r.fought == [] and event_state.read_state(c)['pending'] is None
    finally:
        r.journal.close()
