"""Bounded Campaign retries when freshly loaded Home ignores early input."""

from types import SimpleNamespace

import pytest

from ba_automator.config import Config
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame, ShopRunner


@pytest.fixture
def navigation(tmp_path, monkeypatch):
    now = [0.0]
    sleeps, taps = [], []
    screens = []
    config = Config(serial='127.0.0.1:5695', package='com.nexon.bluearchive',
                    run_dir=tmp_path / 'runs')

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    def tap(x, y, *, deadline, monotonic):
        assert monotonic() <= deadline
        taps.append((x, y, now[0]))
        return True

    device = SimpleNamespace(foreground_package=lambda: config.package, tap=tap)
    runner = ShopRunner(config, device, None, vision=object(),
                        monotonic=lambda: now[0], sleep=sleep)

    def capture():
        # Repeat the last observation to exercise real bounded wait behavior.
        kind = screens.pop(0) if len(screens) > 1 else screens[0]
        return ShopFrame(Capture(b'frame', now[0], config.package),
                         SimpleNamespace(kind=kind))

    monkeypatch.setattr(runner, 'capture', capture)
    yield SimpleNamespace(runner=runner, screens=screens, now=now,
                          sleeps=sleeps, taps=taps, device=device)
    runner.journal.close()


def test_campaign_immediate_success_does_not_wait_ten_seconds(navigation):
    h = navigation
    h.screens[:] = ['home', 'campaign']
    assert h.runner.navigate('home', 'campaign', (1200, 641)).screen.kind == 'campaign'
    assert h.taps == [(1200, 641, 0)]
    assert 10 not in h.sleeps


def test_campaign_retries_are_paced_and_use_new_frames(navigation):
    h = navigation
    h.screens[:] = ['home', 'home', 'home', 'home', 'home', 'campaign']
    assert h.runner.navigate('home', 'campaign', (1200, 641)).screen.kind == 'campaign'
    assert [entry[:2] for entry in h.taps] == [(1200, 641)] * 3
    assert [entry[2] for entry in h.taps] == pytest.approx([0, 12.7, 25.4])
    assert h.sleeps.count(10) == 2


def test_campaign_still_stops_after_three_ignored_taps(navigation):
    h = navigation
    h.screens[:] = ['home']
    with pytest.raises(TaskError, match='did not reach campaign'):
        h.runner.navigate('home', 'campaign', (1200, 641))
    assert len(h.taps) == 3
    assert h.sleeps.count(10) == 2


def test_other_routes_keep_their_existing_retry_timing(navigation):
    h = navigation
    h.screens[:] = ['home', 'home', 'list']
    assert h.runner.navigate('home', 'list', (660, 658)).screen.kind == 'list'
    assert [entry[2] for entry in h.taps] == pytest.approx([0, 2.7])
    assert 10 not in h.sleeps


def test_changed_screen_during_campaign_settle_never_gets_a_retry(navigation):
    h = navigation
    h.screens[:] = ['home', 'home', 'unknown']
    with pytest.raises(TaskError, match='did not reach'):
        h.runner.navigate('home', 'campaign', (1200, 641))
    assert len(h.taps) == 1
    assert h.sleeps.count(10) == 1


@pytest.mark.parametrize('fault', ['stale', 'foreground'])
def test_campaign_retry_retains_input_guards(navigation, monkeypatch, fault):
    h = navigation
    h.screens[:] = ['home']
    original_wait = h.runner.wait
    waits = [0]

    def wait(*args, **kwargs):
        frame = original_wait(*args, **kwargs)
        waits[0] += 1
        if waits[0] == 3:  # The freshly observed source after the settling pause.
            if fault == 'stale':
                h.now[0] += 6
            else:
                h.device.foreground_package = lambda: 'com.android.settings'
        return frame

    monkeypatch.setattr(h.runner, 'wait', wait)
    with pytest.raises(TaskError, match='fresh recognized|Foreground changed'):
        h.runner.navigate('home', 'campaign', (1200, 641))
    assert len(h.taps) == 1
