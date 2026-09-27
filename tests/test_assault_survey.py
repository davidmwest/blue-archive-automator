"""Overlapping raid pages preserve definite difficulty-lock observations."""
from types import SimpleNamespace

import pytest

from ba_automator.assault_policy import AssaultPlanningError, next_difficulty
from ba_automator.assault_vision import AssaultScreen, AssaultStage
from ba_automator.config import Config
from ba_automator.runtime import TaskError
from ba_automator.total_assault import TotalAssaultRunner


def stage(difficulty, locked):
    return AssaultStage(difficulty, "Drumbarka", (1157, 300) if locked is False else None, locked)


def menu(*stages):
    return SimpleNamespace(screen=AssaultScreen(
        "menu", boss="Drumbarka", event_period="09/21 19:00 – 09/28 11:59",
        tickets=6, stages=stages))


@pytest.fixture
def runner(tmp_path):
    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    run_dir=tmp_path / "runs", state_dir=tmp_path / "state",
                    lock_dir=tmp_path / "locks")
    value = TotalAssaultRunner(config, object(), object(), vision=object())
    value.swipe = lambda *args: None
    yield value
    value.journal.close()


def survey_pair(runner, first, second):
    initial = menu(stage("normal", False), first)
    final = menu(second, stage("lunatic", True))
    runner.wait = lambda kind: final
    observed, frame = runner.survey(initial)
    assert frame is final
    return observed


@pytest.mark.parametrize("locked", [False, True])
def test_partially_visible_row_can_become_definitely_readable(runner, locked):
    complete = stage("hardcore", locked)
    observed = survey_pair(runner, stage("hardcore", None), complete)
    assert observed["hardcore"] is complete


@pytest.mark.parametrize("locked", [False, True])
def test_later_clipped_row_cannot_erase_definite_lock_evidence(runner, locked):
    complete = stage("hardcore", locked)
    observed = survey_pair(runner, complete, stage("hardcore", None))
    assert observed["hardcore"] is complete


@pytest.mark.parametrize("first,second", [(False, True), (True, False)])
def test_conflicting_definite_lock_observations_still_stop_the_survey(runner, first, second):
    with pytest.raises(TaskError, match="difficulty lock changed"):
        survey_pair(runner, stage("hardcore", first), stage("hardcore", second))


def test_repeated_unknown_lock_is_not_inferred_unlocked(runner):
    observed = survey_pair(runner, stage("hardcore", None), stage("hardcore", None))
    assert observed["hardcore"].locked is None
    with pytest.raises(AssaultPlanningError, match="hardcore lock is unreadable"):
        next_difficulty("hardcore", {key: value.locked for key, value in observed.items()})
