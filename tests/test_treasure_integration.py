"""Treasure spends only on the independently enabled serial task path."""
from dataclasses import replace

import pytest

from ba_automator.config import Config, ConfigError
from ba_automator.server import _config_document, _toml
from ba_automator.tasks import RUN_PREFIXES, TASKS, task_plan


def config(tmp_path, **kwargs):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                  state_dir=tmp_path / "state", run_dir=tmp_path / "runs", **kwargs)


def test_treasure_is_independent_opt_in_and_strict_boolean(tmp_path):
    base = config(tmp_path, ap_event_priority=True)
    assert not base.ap_event_treasure_enabled
    assert "event_treasure" not in task_plan("daily", base)
    assert "event_treasure" not in task_plan("spend_ap", base)
    with pytest.raises(ConfigError):
        replace(base, ap_event_treasure_enabled="true")
    enabled = replace(base, ap_event_treasure_enabled=True, ap_event_priority=False)
    daily = task_plan("daily", enabled)
    assert daily.count("event_treasure") == 1
    assert task_plan("spend_ap", enabled) == (
        "restart", "spend_ap", "event_treasure", "red_dots",
    )
    assert task_plan("event_treasure", enabled) == ("restart", "event_treasure", "red_dots")
    assert "event_treasure" in TASKS
    assert "event_treasure-" in RUN_PREFIXES


def test_treasure_config_survives_job_snapshot(tmp_path):
    original = config(tmp_path, ap_event_treasure_enabled=True)
    path = tmp_path / "config.toml"
    path.write_text(_toml(_config_document(original)))
    loaded = Config.from_file(path)
    assert loaded.ap_event_treasure_enabled
    assert not loaded.ap_event_priority
    assert loaded.ap_floor == original.ap_floor
