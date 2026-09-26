"""Ticket spending settings survive snapshots and stay separate from badge claims."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import ModuleType

import pytest

from ba_automator import cli
from ba_automator.config import Config, ConfigError
from ba_automator.home_badges import PRIORITY
from ba_automator.server import _config_document, _toml
from ba_automator.tasks import task_plan
from ba_automator import tactical_state


def selected(tmp_path, **changes):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                  run_dir=tmp_path / "runs", lock_dir=tmp_path / "locks",
                  state_dir=tmp_path / "state", **changes)


@pytest.mark.parametrize("reserve", range(6))
@pytest.mark.parametrize("skip_battles", [True, False])
def test_snapshot_preserves_tactical_battle_settings(tmp_path, reserve, skip_battles):
    config = selected(tmp_path, tactical_battles_enabled_in_daily=False,
                      tactical_battles_skip_battles=skip_battles,
                      tactical_battles_preserve_tickets=reserve,
                      tactical_battles_search_minutes=12.5,
                      tactical_battles_refresh_limit=7,
                      tactical_battles_confidence_percent=90.5)
    path = tmp_path / "snapshot.toml"
    path.write_text(_toml(_config_document(config)))
    loaded = Config.from_file(path)
    assert loaded.tactical_battles_preserve_tickets == reserve
    assert loaded.tactical_battles_enabled_in_daily is False
    assert loaded.tactical_battles_skip_battles is skip_battles
    assert loaded.tactical_battles_search_minutes == 12.5
    assert loaded.tactical_battles_refresh_limit == 7
    assert loaded.tactical_battles_confidence_percent == 90.5


@pytest.mark.parametrize(("name", "value"), [
    ("tactical_battles_preserve_tickets", True),
    ("tactical_battles_preserve_tickets", -1),
    ("tactical_battles_preserve_tickets", 6),
    ("tactical_battles_preserve_tickets", 1.5),
    ("tactical_battles_preserve_tickets", "1"),
    ("tactical_battles_enabled_in_daily", "true"),
    ("tactical_battles_enabled_in_daily", 1),
    ("tactical_battles_skip_battles", "false"),
    ("tactical_battles_skip_battles", 0),
    ("tactical_battles_skip_battles", None),
    ("tactical_battles_search_minutes", True),
    ("tactical_battles_search_minutes", "10"),
    ("tactical_battles_search_minutes", None),
    ("tactical_battles_search_minutes", 0.9),
    ("tactical_battles_search_minutes", 30.1),
    ("tactical_battles_search_minutes", float("nan")),
    ("tactical_battles_search_minutes", float("inf")),
    ("tactical_battles_search_minutes", float("-inf")),
    ("tactical_battles_refresh_limit", True),
    ("tactical_battles_refresh_limit", 0),
    ("tactical_battles_refresh_limit", 101),
    ("tactical_battles_refresh_limit", 1.5),
    ("tactical_battles_confidence_percent", True),
    ("tactical_battles_confidence_percent", "90"),
    ("tactical_battles_confidence_percent", None),
    ("tactical_battles_confidence_percent", 79.9),
    ("tactical_battles_confidence_percent", 100),
    ("tactical_battles_confidence_percent", float("nan")),
    ("tactical_battles_confidence_percent", float("inf")),
])
def test_invalid_tactical_settings_reject_before_dispatch(tmp_path, name, value):
    with pytest.raises(ConfigError):
        selected(tmp_path, **{name: value})


@pytest.mark.parametrize("percent", [80, 90, 99, 99.9])
def test_confidence_accepts_supported_percentages(tmp_path, percent):
    assert selected(tmp_path, tactical_battles_confidence_percent=percent).tactical_battles_confidence_percent == percent


@pytest.mark.parametrize("minutes", [1, 1.5, 10, 30])
def test_search_minutes_round_trip_numeric_boundaries(tmp_path, minutes):
    config = selected(tmp_path, tactical_battles_search_minutes=minutes)
    path = tmp_path / "snapshot.toml"
    path.write_text(_toml(_config_document(config)))
    assert Config.from_file(path).tactical_battles_search_minutes == minutes


def test_old_scouting_settings_remain_readable_with_default_search_time(tmp_path):
    document = _config_document(selected(tmp_path))
    document["tactical_battles"].pop("search_minutes")
    document["tactical_battles"].update(refresh_limit=7, confidence_percent=90)
    path = tmp_path / "old.toml"
    path.write_text(_toml(document))
    loaded = Config.from_file(path)
    assert loaded.tactical_battles_search_minutes == 10
    assert loaded.tactical_battles_refresh_limit == 7
    assert loaded.tactical_battles_confidence_percent == 90


def test_daily_fights_before_rank_rewards_but_notifications_never_fight(tmp_path):
    config = selected(tmp_path)
    assert config.tactical_battles_preserve_tickets == 1
    assert config.tactical_battles_search_minutes == 10
    assert config.tactical_battles_refresh_limit == 50
    assert config.tactical_battles_skip_battles is True
    assert config.tactical_battles_confidence_percent == 99
    daily = task_plan("daily", config)
    assert daily.index("tactical_battles") < daily.index("tactical_rewards")
    assert task_plan("tactical_battles", config) == (
        "restart", "tactical_battles", "tactical_rewards", "red_dots")
    assert task_plan("tactical_rewards", config) == (
        "restart", "tactical_rewards", "red_dots")
    assert "tactical_battles" not in PRIORITY
    assert "tactical_battles" not in task_plan("cafe", config)
    disabled = replace(config, tactical_battles_enabled_in_daily=False)
    assert "tactical_battles" not in task_plan("daily", disabled)
    assert "tactical_rewards" in task_plan("daily", disabled)


def pending_battle(config, *, proven=False):
    now = datetime(2026, 9, 26, 21, tzinfo=timezone.utc)
    pending = tactical_state.begin_battle(
        config, "2026-09-26", "opponent-a", 3, 571, now=now)
    if proven:
        tactical_state.record_outcome(config, pending, won=True,
                                      evidence="battle-result.png", now=now)
    return pending


@pytest.mark.parametrize("proven", [False, True])
def test_pending_battle_routes_explicit_retry_before_restart(tmp_path, proven):
    config = selected(tmp_path)
    pending_battle(config, proven=proven)
    saved = tactical_state.state_path(config).read_bytes()
    assert task_plan("tactical_battles", config) == (
        "tactical_battles", "tactical_rewards", "red_dots")
    # Previewing or dispatching a plan cannot prove or discard an outcome.
    assert tactical_state.state_path(config).read_bytes() == saved
    for other_task in ("restart", "daily", "cafe", "tactical_rewards"):
        assert task_plan(other_task, config)[0] == "restart"


def test_unreadable_pending_state_keeps_restart_guard_in_plan(tmp_path):
    config = selected(tmp_path)
    path = tactical_state.state_path(config)
    path.parent.mkdir(parents=True)
    path.write_text("incomplete state")
    assert task_plan("tactical_battles", config)[0] == "restart"
    with pytest.raises(tactical_state.TacticalStateError, match="leave Blue Archive open"):
        tactical_state.ensure_restart_safe(config)
    assert path.read_text() == "incomplete state"


@dataclass
class Result:
    status: str
    run_dir: Path
    duration: float = 1
    actions: int = 1


@pytest.mark.parametrize("restart_fails", [False, True])
def test_cli_keeps_startup_gate_and_reserve(monkeypatch, tmp_path, restart_fails):
    from ba_automator import restart, red_dots

    config = selected(tmp_path, tactical_battles_preserve_tickets=2)
    monkeypatch.setattr(cli.Config, "from_file", lambda _: config)
    monkeypatch.setattr(cli, "AdbDevice", lambda _: object())
    monkeypatch.setattr(cli, "StartupVision", object)
    calls = []

    def runner(name):
        def run(given_config, *_):
            assert given_config.tactical_battles_preserve_tickets == 2
            assert given_config.auto_download is False
            calls.append(name)
            if restart_fails and name == "restart":
                raise RuntimeError("login required")
            return Result("success", tmp_path / name)
        return run

    combat = ModuleType("ba_automator.tactical_runtime")
    combat.run_tactical_battles = runner("tactical_battles")
    monkeypatch.setitem(sys.modules, "ba_automator.tactical_runtime", combat)
    rewards = ModuleType("ba_automator.tactical_rewards")
    rewards.run_tactical_rewards = runner("tactical_rewards")
    monkeypatch.setitem(sys.modules, "ba_automator.tactical_rewards", rewards)
    monkeypatch.setattr(restart, "run_restart", runner("restart"))
    monkeypatch.setattr(red_dots, "run_red_dots", runner("red_dots"))
    assert cli.main(["tactical_battles", "--no-downloads"]) == (1 if restart_fails else 0)
    assert calls == (["restart"] if restart_fails else
                     ["restart", "tactical_battles", "tactical_rewards", "red_dots"])


@pytest.mark.parametrize("recognized", [False, True])
def test_cli_pending_battle_recovery_runs_before_any_other_game_action(
        monkeypatch, tmp_path, recognized):
    from ba_automator import restart, red_dots

    config = selected(tmp_path)
    intent = pending_battle(config)
    saved = tactical_state.state_path(config).read_bytes()
    monkeypatch.setattr(cli.Config, "from_file", lambda _: config)
    monkeypatch.setattr(cli, "AdbDevice", lambda _: object())
    monkeypatch.setattr(cli, "StartupVision", object)
    calls = []

    def recover(given_config, *_):
        calls.append("recover")
        assert tactical_state.read_state(given_config)["pending"]["id"] == intent
        if not recognized:
            raise tactical_state.TacticalStateError("Battle result is still unknown")
        now = datetime(2026, 9, 26, 21, 1, tzinfo=timezone.utc)
        tactical_state.record_outcome(given_config, intent, won=True,
                                      evidence="verified-win.png", now=now)
        tactical_state.complete_battle(given_config, intent, tickets_after=2,
                                       rank_after=500, won=True, now=now)
        return Result("success", tmp_path / "tactical_battles")

    def after_recovery(name):
        def run(given_config, *_):
            assert tactical_state.read_state(given_config)["pending"] is None
            calls.append(name)
            return Result("success", tmp_path / name)
        return run

    combat = ModuleType("ba_automator.tactical_runtime")
    combat.run_tactical_battles = recover
    monkeypatch.setitem(sys.modules, "ba_automator.tactical_runtime", combat)
    rewards = ModuleType("ba_automator.tactical_rewards")
    rewards.run_tactical_rewards = after_recovery("rewards")
    monkeypatch.setitem(sys.modules, "ba_automator.tactical_rewards", rewards)
    monkeypatch.setattr(red_dots, "run_red_dots", after_recovery("red_dots"))
    monkeypatch.setattr(restart, "run_restart", lambda *_: pytest.fail(
        "Restart must not precede pending-result recovery"))

    assert cli.main(["tactical_battles"]) == (0 if recognized else 1)
    assert calls == (["recover", "rewards", "red_dots"] if recognized else ["recover"])
    if not recognized:
        assert tactical_state.state_path(config).read_bytes() == saved
    else:
        assert tactical_state.read_state(config)["attempts"] == {"opponent-a": 1}
