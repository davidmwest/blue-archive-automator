from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from ba_automator.config import Config, ConfigError
from ba_automator.event_priority import active_event
from ba_automator.ap_state import read_state, write_state
from ba_automator.spend_ap import APRunner
from ba_automator.ap_vision import APScreen
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame


def config(tmp_path, **kwargs):
    return Config(serial='127.0.0.1:5695', package='com.nexon.bluearchive',
                  state_dir=tmp_path/'state', run_dir=tmp_path/'runs', **kwargs)


@pytest.mark.parametrize('at,active', [
    ('2026-09-29T01:59:59+00:00', False),
    ('2026-09-29T02:00:00+00:00', True),
    ('2026-10-13T01:59:00+00:00', False),
])
def test_reservation_only_during_playable_window(tmp_path, at, active):
    c = config(tmp_path, ap_event_priority=True)
    assert bool(active_event(c, datetime.fromisoformat(at))) is active
    assert active_event(replace(c, ap_event_priority=False), datetime.fromisoformat(at)) is None


def test_priority_is_boolean(tmp_path):
    with pytest.raises(ConfigError):
        config(tmp_path, ap_event_priority='true')


@pytest.mark.parametrize('strategy', ['elephs', 'reports', 'credits'])
def test_event_hold_prevents_all_normal_spending(tmp_path, monkeypatch, strategy):
    c = config(tmp_path, ap_event_priority=True, ap_strategy=strategy)
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    r = APRunner(c, None, None, vision=object(), wall_clock=lambda: now)
    f = ShopFrame(Capture(b'', 0, c.package), APScreen('home', ap=220))
    r.wait = lambda *args, **kwargs: f
    r.finish = lambda: None
    calls = []
    monkeypatch.setattr('ba_automator.event_quests.farm_event',
                        lambda *args: calls.append('inspect') or 'Needs setup: clear quests')
    try:
        r.run()
        assert calls == ['inspect']
        saved = read_state(c)
        assert 'Needs setup' in saved['last_summary']
        assert saved['last_ap'] == 220
        assert saved['pending'] is None
    finally:
        r.journal.close()


def test_pending_sweep_blocks_even_event_inspection(tmp_path, monkeypatch):
    c = config(tmp_path, ap_event_priority=True)
    state = read_state(c)
    state['pending'] = dict(strategy='elephs', stage='1-1', ap_before=220, cost=20, count=1, floor=100)
    write_state(c, state)
    r = APRunner(c, None, None, vision=object())
    try:
        with pytest.raises(TaskError, match='unresolved'):
            r.run()
    finally:
        r.journal.close()


def test_failed_event_inspection_never_falls_back_to_normal_farming(tmp_path, monkeypatch):
    c = config(tmp_path, ap_event_priority=True)
    r = APRunner(c, None, None, vision=object(),
                 wall_clock=lambda: datetime(2026, 9, 30, tzinfo=timezone.utc))
    r.wait = lambda *a, **k: ShopFrame(Capture(b'', 0, c.package), APScreen('home', ap=220))
    def failed(*args):
        raise TaskError('Wrong event', r.run_dir)
    monkeypatch.setattr('ba_automator.event_quests.farm_event', failed)
    try:
        with pytest.raises(TaskError, match='Wrong event'):
            r.run()
        assert r.state['pending'] is None
    finally:
        r.journal.close()


@pytest.mark.parametrize('round_number,available,expected_stage', [
    (4, {9: 3}, None),  # observed goal completion releases normal farming
    (3, {9: 3}, 9),
    (1, {9: None, 5: 3}, 5),
    (1, {9: 2, 5: 1, 1: 3}, 1),
    (1, {9: None, 5: None, 1: 2}, None),
])
def test_event_farm_goal_and_verified_stage_selection(tmp_path, monkeypatch, round_number, available, expected_stage):
    from ba_automator.event_quests import farm_event
    monkeypatch.setattr('ba_automator.event_stories.complete_stories', lambda *a: None)
    from ba_automator.event_priority import available_event
    c = config(tmp_path, ap_event_priority=True, ap_floor=100)
    old_vision = object()
    def frame(kind, **kwargs):
        return SimpleNamespace(screen=SimpleNamespace(kind=kind, **kwargs))
    homes, swept = [], []
    runner = SimpleNamespace(config=c, vision=old_vision, startup=None)
    runner.tap = lambda *args: None
    runner.wait = lambda kind: frame('event_board', round=round_number)
    def sweep(f, count):
        swept.append((f.screen.stage, count))
        assert f.screen.stars == 3 and f.screen.ap-count*f.screen.cost >= c.ap_floor
        return f
    runner.sweep = sweep
    monkeypatch.setattr('ba_automator.event_quests.enter_event', lambda *args: frame('event_list'))
    monkeypatch.setattr('ba_automator.event_quests.event_home', lambda *args: homes.append(True))
    monkeypatch.setattr('ba_automator.event_quests.close_detail', lambda *args: None)
    def detail(r, stage, **kwargs):
        assert kwargs['allow_unavailable']
        stars = available.get(stage)
        return None if stars is None else frame('detail', stage=stage, stars=stars, ap=225, cost=15)
    monkeypatch.setattr('ba_automator.event_quests.quest_detail', detail)
    result = farm_event(runner, available_event(datetime(2026,9,30,tzinfo=timezone.utc)))
    assert runner.vision is old_vision and homes == [True]
    assert swept == ([(expected_stage, 8)] if expected_stage else [])
    assert (result is None) is (round_number > 3)
    if round_number <= 3 and expected_stage is None:
        assert 'Normal AP farming remains on hold' in result


def test_unreadable_event_cost_retries_instead_of_reporting_low_ap(tmp_path, monkeypatch):
    from ba_automator.event_quests import farm_event
    monkeypatch.setattr('ba_automator.event_stories.complete_stories', lambda *a: None)
    from ba_automator.event_priority import available_event
    c = config(tmp_path, ap_event_priority=True)
    old_vision = object()
    runner = SimpleNamespace(config=c, vision=old_vision, startup=None,
                             tap=lambda *a: None)
    calls = []
    def wait(kind, **kwargs):
        calls.append(kind)
        if kind == 'event_board':
            return SimpleNamespace(screen=SimpleNamespace(round=1))
        if kind == 'detail':
            predicate = kwargs['predicate']
            assert not predicate(APScreen('detail', strategy='event', stage='5', stars=3, ap=758))
            assert predicate(APScreen('detail', strategy='event', stage='5', stars=3, ap=758, cost=15))
            assert not predicate(APScreen('detail', strategy='event', stage='9', stars=3, ap=758, cost=15))
            raise TaskError('Unreadable event detail', tmp_path)
    runner.wait = wait
    monkeypatch.setattr('ba_automator.event_quests.enter_event', lambda *a: None)
    monkeypatch.setattr('ba_automator.event_quests.quest_detail', lambda *a, **kw:
        SimpleNamespace(screen=APScreen('detail', strategy='event', stage='5', stars=3, ap=758)))
    profile = dict(available_event(datetime(2026,9,30,tzinfo=timezone.utc)), farm_order=[5])
    with pytest.raises(TaskError, match='Unreadable'):
        farm_event(runner, profile)
    assert 'detail' in calls and runner.vision is old_vision


def test_unfinished_stories_hold_quest_goal_and_normal_farming(tmp_path, monkeypatch):
    from ba_automator.event_quests import farm_event
    from ba_automator.event_priority import available_event
    c = config(tmp_path, ap_event_priority=True)
    old_vision = object()
    frame = SimpleNamespace(screen=SimpleNamespace(kind='event_list'))
    homes = []
    runner = SimpleNamespace(config=c, vision=old_vision, startup=None,
                             wait=lambda *a:frame)
    runner.tap = lambda *a:pytest.fail('Must not inspect the treasure goal or quests before Stories finish')
    monkeypatch.setattr('ba_automator.event_quests.enter_event', lambda *a:frame)
    monkeypatch.setattr('ba_automator.event_stories.complete_stories',
                        lambda *a:'Stories waiting for AP above the floor')
    monkeypatch.setattr('ba_automator.event_quests.event_home', lambda *a:homes.append(True))
    result = farm_event(runner, available_event(datetime(2026,9,30,tzinfo=timezone.utc)))
    assert 'Stories waiting' in result
    assert homes == [True] and runner.vision is old_vision


def test_story_visit_budget_requests_a_prompt_continuation(tmp_path, monkeypatch):
    c = config(tmp_path, ap_event_priority=True)
    now = datetime(2026,9,30,tzinfo=timezone.utc)
    r = APRunner(c,None,None,vision=object(),wall_clock=lambda:now)
    r.wait=lambda *a,**kw:ShopFrame(Capture(b'',0,c.package),APScreen('home',ap=500))
    r.finish=lambda:None
    def deferred(r, profile):
        r.event_deferred=True
        return 'Event Stories will continue next visit'
    monkeypatch.setattr('ba_automator.event_quests.farm_event',deferred)
    try:
        r.run()
        saved=read_state(c)
        assert (datetime.fromisoformat(saved['next_check_at'])-now).total_seconds()==60
        assert 'Stories' in saved['last_summary']
    finally:
        r.journal.close()
