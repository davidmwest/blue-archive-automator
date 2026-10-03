"""Replay a real account-level overlay before and during reward inspection."""

from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from ba_automator import account_level
from ba_automator.ap_state import read_state
from ba_automator.config import Config
from ba_automator.loot_receipts import ReceiptReader
from ba_automator.runtime import Capture, TaskError
from ba_automator.spend_ap import APRunner
from ba_automator.vision import StartupVision, decode_frame, read_game_words

FIXTURES = Path(__file__).parent / 'fixtures'
OVERLAY = (FIXTURES / 'ap-account-level-up.png').read_bytes()
RECEIPT = (FIXTURES / 'ap-hard-receipt.png').read_bytes()


@pytest.fixture(scope='module')
def vision():
    return StartupVision()


@pytest.mark.parametrize('native', [True, False])
def test_real_account_level_overlay(vision, native):
    png = OVERLAY if native else cv2.imencode('.png', decode_frame(OVERLAY))[1].tobytes()
    assert account_level.read_sweep_level_up(png, vision) == 81
    assert account_level.read_sweep_level_up(RECEIPT, vision) is None


@pytest.mark.parametrize('missing', ['sweep complete', 'level up', 'touch to continue', '81'])
def test_incomplete_overlay_never_authorizes_dismissal(vision, monkeypatch, missing):
    words = read_game_words(OVERLAY, vision)
    monkeypatch.setattr(account_level, 'read_game_words',
                        lambda *_: [w for w in words if w.normalized != missing])
    assert account_level.read_sweep_level_up(OVERLAY, vision) is None


@pytest.fixture
def runner(tmp_path, vision):
    clock = SimpleNamespace(now=0.)
    taps, screenshots = [], [OVERLAY, RECEIPT]
    config = Config(serial='127.0.0.1:5695', package='com.nexon.bluearchive',
                    state_dir=tmp_path / 'state', run_dir=tmp_path / 'runs')
    device = SimpleNamespace(foreground_package=lambda: config.package,
                             screenshot=lambda: screenshots.pop(0),
                             tap=lambda *xy, **kw: taps.append(xy) or True)
    r = APRunner(config, device, vision, monotonic=lambda: clock.now,
                 sleep=lambda t: setattr(clock, 'now', clock.now + t))
    r.state = read_state(config)
    r.state['pending'] = dict(strategy='event', stage='9', ap_before=185,
                             cost=20, count=4, floor=100)
    yield r, clock, taps
    r.journal.close()


@pytest.mark.parametrize('in_reader', [False, True])
def test_overlay_before_or_during_receipt_reads_dismisses_without_replaying_spend(runner, in_reader):
    r, _, taps = runner
    pending = dict(r.state['pending'])
    cap = (ReceiptReader(r, r.startup, r.run_dir / 'receipt.png').capture()
           if in_reader else r.capture().capture)
    assert cap.png == RECEIPT
    assert taps == [(640, 360)]
    assert r.state['pending'] == pending  # Actual receipt/balances still must reconcile.
    assert (r.run_dir / 'account-level-up-01.png').exists()


@pytest.mark.parametrize('fault', ['no_pending', 'stale', 'foreground', 'repeat'])
def test_level_up_recovery_retains_guards(runner, fault):
    r, clock, taps = runner
    cap = Capture(OVERLAY, clock.now, r.config.package)
    if fault == 'no_pending':
        r.state['pending'] = None
        assert not r.handle_receipt_overlay(cap)
    elif fault == 'stale':
        clock.now = 10
        assert r.handle_receipt_overlay(cap)
    else:
        if fault == 'foreground':
            r.device.foreground_package = lambda: 'com.android.settings'
        else:
            r.level_up_dismissals = 3
        with pytest.raises(TaskError):
            r.handle_receipt_overlay(cap)
    assert taps == []
