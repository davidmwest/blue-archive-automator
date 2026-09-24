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
