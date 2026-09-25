"""Offline coverage for the assault settings and serial task dispatch contract."""

from dataclasses import dataclass, replace
from pathlib import Path
import sys
from types import ModuleType

import pytest

from ba_automator import cli
from ba_automator.config import Config, ConfigError
from ba_automator.server import _config_document, _toml
from ba_automator.tasks import task_plan


def selected(tmp_path, **changes):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                  run_dir=tmp_path / "runs", lock_dir=tmp_path / "locks",
                  state_dir=tmp_path / "state", **changes)


@pytest.mark.parametrize("difficulty", [
    "normal", "hard", "very_hard", "hardcore", "extreme", "insane", "torment", "lunatic",
])
def test_total_assault_config_snapshot_roundtrip(tmp_path, difficulty):
    config = selected(tmp_path, total_assault_difficulty=difficulty,
                      total_assault_enabled_in_daily=True, total_assault_comfort_seconds=45)
    source = tmp_path / "snapshot.toml"
    source.write_text(_toml(_config_document(config)))
    loaded = Config.from_file(source)
    assert loaded.total_assault_difficulty == difficulty
    assert loaded.total_assault_enabled_in_daily is True
    assert loaded.total_assault_comfort_seconds == 45
    assert loaded.serial == config.serial and loaded.state_dir == config.state_dir


@pytest.mark.parametrize(("field", "value"), [
    ("total_assault_difficulty", "Hardcore"), ("total_assault_difficulty", "unknown"),
    ("total_assault_difficulty", None), ("total_assault_difficulty", []),
    ("total_assault_enabled_in_daily", "true"), ("total_assault_enabled_in_daily", 1),
    ("total_assault_comfort_seconds", True), ("total_assault_comfort_seconds", -1),
    ("total_assault_comfort_seconds", 301), ("total_assault_comfort_seconds", 1.5),
])
def test_total_assault_rejects_invalid_settings(tmp_path, field, value):
    with pytest.raises(ConfigError):
        selected(tmp_path, **{field: value})


def test_total_assault_is_opt_in_daily_and_does_not_run_with_cafe(tmp_path):
    config = selected(tmp_path)
    assert config.total_assault_difficulty == "hardcore"
    assert config.total_assault_comfort_seconds == 30
    assert "total_assault" not in task_plan("daily", config)
    assert "assault_rewards" in task_plan("daily", config)
    assert task_plan("assault_rewards", config) == ("restart", "assault_rewards", "red_dots")
    assert task_plan("total_assault", config) == ("restart", "total_assault", "assault_rewards", "red_dots")
    enabled = replace(config, total_assault_enabled_in_daily=True, ap_schedule_enabled=True)
    assert task_plan("daily", enabled)[-6:] == (
        "lessons", "total_assault", "assault_rewards", "spend_ap", "tasks", "red_dots",
    )
    assert "total_assault" not in task_plan("cafe", enabled)


def test_rewards_cli_never_dispatches_combat(monkeypatch, tmp_path):
    from ba_automator import restart, red_dots

    config = selected(tmp_path, total_assault_enabled_in_daily=True)
    monkeypatch.setattr(cli.Config, "from_file", lambda _: config)
    monkeypatch.setattr(cli, "AdbDevice", lambda _: object())
    monkeypatch.setattr(cli, "StartupVision", object)
    calls = []

    def runner(name):
        def run(*args):
            calls.append(name)
            return Result("success", tmp_path / f"{name}-test")
        return run

    rewards = ModuleType("ba_automator.assault_rewards")
    rewards.run_assault_rewards = runner("assault_rewards")
    monkeypatch.setitem(sys.modules, "ba_automator.assault_rewards", rewards)
    combat = ModuleType("ba_automator.total_assault")
    combat.run_total_assault = lambda *a: pytest.fail("rewards must not enter combat")
    monkeypatch.setitem(sys.modules, "ba_automator.total_assault", combat)
    monkeypatch.setattr(restart, "run_restart", runner("restart"))
    monkeypatch.setattr(red_dots, "run_red_dots", runner("red_dots"))
    assert cli.main(["assault_rewards"]) == 0
    assert calls == ["restart", "assault_rewards", "red_dots"]


@dataclass
class Result:
    status: str
    run_dir: Path
    duration: float = 1.0
    actions: int = 1


@pytest.mark.parametrize("restart_fails", [False, True])
def test_total_assault_cli_dispatch_preserves_settings_and_startup_gate(monkeypatch, tmp_path, restart_fails):
    from ba_automator import restart, red_dots

    config = selected(tmp_path, total_assault_difficulty="hardcore", total_assault_comfort_seconds=45)
    monkeypatch.setattr(cli.Config, "from_file", lambda _: config)
    device, vision = object(), object()
    monkeypatch.setattr(cli, "AdbDevice", lambda _: device)
    monkeypatch.setattr(cli, "StartupVision", lambda: vision)
    calls = []

    def runner(name):
        def run(given_config, given_device, given_vision):
            assert given_config.total_assault_difficulty == "hardcore"
            assert given_config.total_assault_comfort_seconds == 45
            assert given_config.auto_download is False
            assert given_device is device and given_vision is vision
            calls.append(name)
            if restart_fails and name == "restart":
                raise RuntimeError("login required")
            return Result("success", tmp_path / f"{name}-test")
        return run

    module = ModuleType("ba_automator.total_assault")
    module.run_total_assault = runner("total_assault")
    monkeypatch.setitem(sys.modules, "ba_automator.total_assault", module)
    rewards = ModuleType("ba_automator.assault_rewards")
    rewards.run_assault_rewards = runner("assault_rewards")
    monkeypatch.setitem(sys.modules, "ba_automator.assault_rewards", rewards)
    monkeypatch.setattr(restart, "run_restart", runner("restart"))
    monkeypatch.setattr(red_dots, "run_red_dots", runner("red_dots"))
    assert cli.main(["total_assault", "--no-downloads"]) == (1 if restart_fails else 0)
    assert calls == (["restart"] if restart_fails else ["restart", "total_assault", "assault_rewards", "red_dots"])
