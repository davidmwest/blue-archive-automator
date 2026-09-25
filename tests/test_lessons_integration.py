from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sys
from types import ModuleType

import pytest

from ba_automator import cli
from ba_automator.config import Config, ConfigError
from ba_automator.tasks import task_plan


def selected(**changes):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive", **changes)


@pytest.mark.parametrize(("field", "value"), [
    ("lessons_strategy", "random"), ("lessons_strategy", None),
    ("lessons_max_tickets", True), ("lessons_max_tickets", -1),
    ("lessons_max_tickets", 100), ("lessons_max_tickets", 2.5),
    ("lessons_enabled_in_daily", 1), ("lessons_enabled_in_daily", "false"),
    ("lessons_locations", "Gehenna"), ("lessons_locations", [""]),
    ("lessons_locations", ["  "]), ("lessons_locations", ["Gehenna\nTrinity"]),
    ("lessons_locations", ["x" * 101]), ("lessons_locations", [False]),
    ("lessons_locations", [str(i) for i in range(51)]),
    ("lessons_locations", ["Gehenna", " gehenna "]),
])
def test_invalid_lesson_settings_reject_before_any_device_input(field, value):
    with pytest.raises(ConfigError):
        selected(**{field: value})


def test_lessons_toml_roundtrip_and_immutable_allowlist(tmp_path):
    source = tmp_path / "local.toml"
    source.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                      '[lessons]\nstrategy="school_rank"\nmax_tickets=3\n'
                      'locations=["  Gehenna Academy ", "Trinity General School"]\n'
                      'enabled_in_daily=false\n', encoding="utf-8")
    config = Config.from_file(source)
    assert config.lessons_strategy == "school_rank"
    assert config.lessons_max_tickets == 3
    assert config.lessons_locations == ("Gehenna Academy", "Trinity General School")
    assert config.lessons_enabled_in_daily is False
    names = ["Gehenna Academy"]
    config = selected(lessons_locations=names)
    names.append("Trinity General School")
    assert config.lessons_locations == ("Gehenna Academy",)


@pytest.mark.parametrize("table", [
    '[lessons]\nlessons_strategy="relationship"\n',
    '[restart]\nstrategy="relationship"\n',
    '[lessons]\nmax_ticket=1\n',
])
def test_lesson_config_typos_do_not_silently_use_defaults(tmp_path, table):
    source = tmp_path / "local.toml"
    source.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n' + table)
    with pytest.raises(ConfigError, match="Unknown"):
        Config.from_file(source)


def test_default_plans_include_lessons_only_in_daily_or_explicit_lessons_job():
    config = selected()
    assert config.lessons_strategy == "relationship"
    assert config.lessons_max_tickets == 0
    assert config.lessons_locations == ()
    assert task_plan("daily", config) == ("restart", "club", "free_pack", "mail", "cafe", "bounties", "scrimmages", "tactical_rewards", "lessons", "tasks", "red_dots")
    assert task_plan("cafe", config) == ("restart", "club", "mail", "cafe", "red_dots")
    assert task_plan("lessons", config) == ("restart", "lessons", "red_dots")
    disabled = replace(config, lessons_enabled_in_daily=False)
    assert task_plan("daily", disabled) == ("restart", "club", "free_pack", "mail", "cafe", "bounties", "scrimmages", "tactical_rewards", "tasks", "red_dots")
    assert task_plan("lessons", disabled) == ("restart", "lessons", "red_dots")


@dataclass
class Result:
    status: str
    run_dir: Path
    duration: float = 1.0
    actions: int = 1


@pytest.mark.parametrize(("command", "enabled", "expected"), [
    ("daily", True, ["restart", "club", "free_pack", "mail", "cafe", "bounties", "scrimmages", "tactical_rewards", "lessons", "tasks", "red_dots"]),
    ("daily", False, ["restart", "club", "free_pack", "mail", "cafe", "bounties", "scrimmages", "tactical_rewards", "tasks", "red_dots"]),
    ("lessons", True, ["restart", "lessons", "red_dots"]),
    ("lessons", False, ["restart", "lessons", "red_dots"]),
    ("cafe", True, ["restart", "club", "mail", "cafe", "red_dots"]),
])
def test_cli_dispatches_sequential_plan_and_passes_lesson_config(monkeypatch, tmp_path, command, enabled, expected):
    from ba_automator import cafe, club, mail, restart, tickets, task_rewards, free_pack, red_dots, tactical_rewards

    config = selected(lessons_enabled_in_daily=enabled, lessons_strategy="school_rank", lessons_max_tickets=3)
    monkeypatch.setattr(cli.Config, "from_file", lambda _: config)
    device, vision = object(), object()
    monkeypatch.setattr(cli, "AdbDevice", lambda _: device)
    monkeypatch.setattr(cli, "StartupVision", lambda: vision)
    calls = []

    def runner(name):
        def run(given_config, given_device=None, given_vision=None):
            assert given_config.lessons_strategy == "school_rank"
            assert given_config.lessons_max_tickets == 3
            assert given_config.auto_download is False
            assert given_device is device and given_vision is vision
            calls.append(name)
            return Result("success", tmp_path / f"{name}-test")
        return run

    module = ModuleType("ba_automator.lessons")
    module.run_lessons = runner("lessons")
    monkeypatch.setitem(sys.modules, "ba_automator.lessons", module)
    monkeypatch.setattr(restart, "run_restart", runner("restart"))
    monkeypatch.setattr(cafe, "run_cafe", runner("cafe"))
    monkeypatch.setattr(club, "run_club", runner("club"))
    monkeypatch.setattr(mail, "run_mail", runner("mail"))
    monkeypatch.setattr(free_pack, "run_free_pack", runner("free_pack"))
    monkeypatch.setattr(red_dots, "run_red_dots", runner("red_dots"))
    monkeypatch.setattr(tactical_rewards, "run_tactical_rewards", runner("tactical_rewards"))
    monkeypatch.setattr(task_rewards, "run_task_rewards", runner("tasks"))
    monkeypatch.setattr(tickets, "run_bounties", runner("bounties"))
    monkeypatch.setattr(tickets, "run_scrimmages", runner("scrimmages"))
    assert cli.main([command, "--no-downloads"]) == 0
    assert calls == expected


def test_restart_failure_prevents_lesson_ticket_use(monkeypatch, capsys):
    from ba_automator import restart

    monkeypatch.setattr(cli.Config, "from_file", lambda _: selected())
    monkeypatch.setattr(cli, "AdbDevice", lambda _: object())
    monkeypatch.setattr(cli, "StartupVision", object)
    def fail(*args):
        raise RuntimeError("login required")
    monkeypatch.setattr(restart, "run_restart", fail)
    module = ModuleType("ba_automator.lessons")
    module.run_lessons = lambda *args: pytest.fail("lessons ran after restart failed")
    monkeypatch.setitem(sys.modules, "ba_automator.lessons", module)
    assert cli.main(["lessons"]) == 1
    assert "login required" in capsys.readouterr().err
