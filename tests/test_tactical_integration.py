"""Ticket spending settings survive snapshots and stay separate from badge claims."""

from dataclasses import dataclass, replace
from pathlib import Path
import sys
from types import ModuleType

import pytest

from ba_automator import cli
from ba_automator.config import Config, ConfigError
from ba_automator.home_badges import PRIORITY
from ba_automator.server import _config_document, _toml
from ba_automator.tasks import task_plan


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
                      tactical_battles_refresh_limit=7,
                      tactical_battles_confidence_percent=90.5)
    path = tmp_path / "snapshot.toml"
    path.write_text(_toml(_config_document(config)))
    loaded = Config.from_file(path)
    assert loaded.tactical_battles_preserve_tickets == reserve
    assert loaded.tactical_battles_enabled_in_daily is False
    assert loaded.tactical_battles_skip_battles is skip_battles
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


def test_daily_fights_before_rank_rewards_but_notifications_never_fight(tmp_path):
    config = selected(tmp_path)
    assert config.tactical_battles_preserve_tickets == 1
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
