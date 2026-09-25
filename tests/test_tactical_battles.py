"""The battle placeholder cannot spend a ticket or interact with the game."""

import json
import pytest
from ba_automator.config import Config
from ba_automator.tactical_battles import run_tactical_battles
from ba_automator.tasks import TASKS, task_plan


def test_battles_remain_deferred_and_unavailable_to_dispatch(tmp_path):
    config = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        run_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )

    class Untouchable:
        def __getattr__(self, name):
            pytest.fail(f"Battle stub touched {name}")

    result = run_tactical_battles(config, Untouchable(), Untouchable())
    assert result.status == "deferred" and result.actions == 0
    event = json.loads((result.run_dir / "events.jsonl").read_text())
    assert event["status"] == "deferred" and event["actions"] == 0
    assert not config.state_dir.exists()
    assert "tactical_battles" not in TASKS
    assert all("tactical_battles" not in task_plan(task, config) for task in TASKS)


def test_reward_route_has_no_battle_targets():
    from pathlib import Path
    from ba_automator.tactical_rewards import TacticalVision
    from ba_automator.vision import StartupVision

    path = Path(__file__).parent / "fixtures" / "ap-campaign-stable.png"
    result = TacticalVision(StartupVision()).analyze(path.read_bytes())
    assert result.kind == "campaign" and result.target == (868, 581)
