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
    monkeypatch.setattr('ba_automator.event_inspection.inspect_event',
                        lambda *args: calls.append('inspect') or 'needs_first_clears')
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
    monkeypatch.setattr('ba_automator.event_inspection.inspect_event', failed)
    try:
        with pytest.raises(TaskError, match='Wrong event'):
            r.run()
        assert r.state['pending'] is None
    finally:
        r.journal.close()
