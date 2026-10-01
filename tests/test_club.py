"""The pending Club feature must not impersonate a completed game action."""

from datetime import datetime
import json

import pytest

from ba_automator import cli
from ba_automator.club import game_day, run_club
from ba_automator.config import Config
from ba_automator.tasks import task_plan


@pytest.mark.parametrize(("timestamp", "expected"), [
    ("2026-09-24T18:59:59+00:00", "2026-09-23"),
    ("2026-09-24T19:00:00+00:00", "2026-09-24"),
    ("2026-09-24T12:00:00-07:00", "2026-09-24"),
    ("2026-12-24T10:59:59-08:00", "2026-12-23"),
    ("2026-12-24T11:00:00-08:00", "2026-12-24"),
    ("2027-01-01T00:00:00+00:00", "2026-12-31"),
])
def test_global_game_day_uses_reset_instead_of_local_midnight(timestamp, expected):
    assert game_day(datetime.fromisoformat(timestamp)) == expected


def test_game_day_rejects_ambiguous_local_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        game_day(datetime(2026, 9, 24, 12))


def test_late_login_receipt_is_inspected_before_acknowledgement(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ba_automator import club
    from ba_automator.shop_runtime import ShopRunner
    r = object.__new__(club.ClubRunner)
    clock = [0]
    r.clock = lambda: clock[0]
    r.run_dir = tmp_path
    r.journal = Mock()
    r.login_receipts = 0
    original = SimpleNamespace(screen=SimpleNamespace(kind='receipt', target=(640, 630)),
                               capture=SimpleNamespace(png=b'receipt'))
    refreshed = SimpleNamespace(screen=original.screen)
    home = SimpleNamespace(screen=SimpleNamespace(kind='home'))
    wait = Mock(side_effect=[original, home])
    def inspect_slow_receipt(*args):
        clock[0] += 60
        return refreshed
    inspect = Mock(side_effect=inspect_slow_receipt)
    monkeypatch.setattr(ShopRunner, 'wait', wait)
    monkeypatch.setattr(club, 'inspect_receipt', inspect)
    def tap(frame, target, detail):
        inspect.assert_called_once_with(r, original, tmp_path / 'login-reward-1.png')
        assert frame is refreshed
    r.tap = tap
    assert r.wait('home') is home
    assert wait.call_args.kwargs['timeout'] == 40
    r.journal.save_image.assert_called_once_with('login-reward-1.png', b'receipt')


def test_club_never_blindly_dismisses_unknown_or_repeated_overlays(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ba_automator.club import ClubRunner
    from ba_automator.shop_runtime import ShopRunner
    r = object.__new__(ClubRunner)
    r.clock = lambda: 0
    r.login_receipts = 3
    r.tap = Mock()
    r.fail = Mock(side_effect=RuntimeError('bounded'))
    wait = Mock(return_value=SimpleNamespace(screen=SimpleNamespace(kind='receipt')))
    monkeypatch.setattr(ShopRunner, 'wait', wait)
    with pytest.raises(RuntimeError, match='bounded'):
        r.wait('home')
    assert wait.call_args.args[0] == {'home', 'receipt'}
    r.tap.assert_not_called()
