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


def test_stub_records_deferral_without_device_access_attendance_or_reward(tmp_path, monkeypatch, capsys):
    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    run_dir=tmp_path / "runs", state_dir=tmp_path / "state")

    class Untouchable:
        def __getattr__(self, name):
            pytest.fail(f"Club stub accessed {name}")

    result = run_club(config, Untouchable(), Untouchable())
    assert result.status == "deferred" and result.actions == 0
    events = [json.loads(line) for line in (result.run_dir / "events.jsonl").read_text().splitlines()]
    assert events[-1]["status"] == "deferred"
    assert events[-1]["attendance_recorded"] is False
    assert not config.state_dir.exists()
    assert task_plan("club", config) == ("club",)
    monkeypatch.setattr(cli.Config, "from_file", lambda _: config)
    monkeypatch.setattr(cli, "AdbDevice", lambda _: Untouchable())
    monkeypatch.setattr(cli, "StartupVision", lambda: pytest.fail("stub initialized OCR"))
    assert cli.main(["club"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "deferred"
    assert not config.state_dir.exists()
