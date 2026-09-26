"""Disposable progress cannot become a confidence or ticket-spending shortcut."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_automator import tactical_state, tactical_survey as survey
from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_refresh import RefreshPlanner


NOW = datetime(2026, 9, 26, 21, tzinfo=timezone.utc).timestamp()
DAY = "2026-09-26"


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(state_dir=tmp_path, serial="127.0.0.1:5695",
                           package="com.nexon.bluearchive",
                           tactical_battles_confidence_percent=99,
                           tactical_battles_refresh_limit=50,
                           tactical_battles_preserve_tickets=1)


def identity(key="opponent-a", rank=100):
    return {"choice": asdict(Opponent(key, rank, 80, (80, 75))),
            "name": "Anonymous", "target": [1000, 250], "signature": "0123" * 288}


def arguments(**overrides):
    person = identity()
    values = dict(day_key=DAY, own_rank=571, confidence=.99, pilot=50,
                  planner=RefreshPlanner(), candidates=[person["choice"]],
                  identities={"opponent-a": person}, now=NOW)
    values.update(overrides)
    return values


def save(config, **overrides):
    survey.save_survey(config, **arguments(**overrides))


def load(config, **overrides):
    values = dict(day_key=DAY, own_rank=571, confidence=.99, pilot=50, now=NOW)
    values.update(overrides)
    return survey.load_survey(config, **values)


def corrupt(config, edit):
    path = survey.survey_path(config)
    value = json.loads(path.read_text())
    edit(value)
    path.write_text(json.dumps(value))


def completed_planner():
    planner = RefreshPlanner()
    for _ in range(50):
        planner.observe((100, 200, 300))
    return planner


def test_missing_survey_is_read_only(config):
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW)
    survey.clear_survey(config)
    assert list(config.state_dir.iterdir()) == []


def test_progress_replays_and_preserves_mutable_candidate_copies(config):
    options = arguments()
    options["planner"].observe((100, 200, 300))
    survey.save_survey(config, **options)
    options["identities"]["opponent-a"]["name"] = "Changed after save"
    restored = load(config)
    assert restored["planner"].plan() == options["planner"].plan()
    assert restored["identities"]["opponent-a"]["name"] == "Anonymous"
    assert restored["created_at"] == restored["updated_at"] == NOW
    assert restored["due_at"] == NOW + 60
    restored["planner"].observe((101, 201, 301))
    assert load(config)["planner"].physical_refreshes == 1


def test_checkpoint_is_isolated_by_instance_and_package(config):
    save(config)
    for change in ({"serial": "127.0.0.1:5555"}, {"package": "another.game"}):
        other = SimpleNamespace(**(vars(config) | change))
        assert survey.survey_path(other) != survey.survey_path(config)
        assert load(other) is None
    assert survey.survey_path(config) != tactical_state.state_path(config)


def test_interrupted_resumed_survey_produces_same_coverage_and_lookup(config):
    uninterrupted, resumed = RefreshPlanner(), RefreshPlanner()
    for index in range(1000):
        ranks = (100 + index % 10, 200 + index % 20, 300 + index % 30)
        expected = uninterrupted.observe(ranks)
        assert resumed.observe(ranks) == expected
        if index in (17, 69, 199):
            save(config, planner=resumed, now=NOW + index)
            resumed = load(config, now=NOW + index)["planner"]
            assert resumed.plan() == expected
        if expected.status == "complete":
            break
    assert expected.status == "complete"
    assert resumed.lookup_plan(100) == uninterrupted.lookup_plan(100)
    assert resumed.to_dict() == uninterrupted.to_dict()


@pytest.mark.parametrize("changed", [
    {"own_rank": 570}, {"day_key": "2026-09-27"}, {"confidence": .9}, {"pilot": 25},
])
def test_mismatched_context_cannot_resume(config, changed):
    save(config)
    assert load(config, **changed) is None


def test_continuation_requires_due_time_and_current_settings(config):
    save(config)
    assert not survey.continuation_due(config, now=NOW + 59)
    assert survey.continuation_due(config, now=NOW + 60)
    config.tactical_battles_confidence_percent = 90
    assert not survey.continuation_due(config, now=NOW + 60)
    config.tactical_battles_confidence_percent = 99
    config.tactical_battles_refresh_limit = 25
    assert not survey.continuation_due(config, now=NOW + 60)


def test_repeated_saves_never_extend_original_four_hour_lifetime(config):
    save(config)
    save(config, now=NOW + 3 * 3600)
    value = load(config, now=NOW + 3 * 3600 + 60)
    assert value["created_at"] == NOW
    assert value["updated_at"] == NOW + 3 * 3600
    assert survey.continuation_due(config, now=NOW + 4 * 3600 - 1)
    assert load(config, now=NOW + 4 * 3600) is None
    assert not survey.continuation_due(config, now=NOW + 4 * 3600)
    survey.clear_survey(config)
    save(config, now=NOW + 4 * 3600)
    assert load(config, now=NOW + 4 * 3600)["created_at"] == NOW + 4 * 3600


def test_server_reset_expires_survey_even_if_only_minutes_old(config):
    before_reset = datetime(2026, 9, 27, 18, 58, tzinfo=timezone.utc).timestamp()
    save(config, now=before_reset)
    assert survey.continuation_due(config, now=before_reset + 60)
    assert load(config, now=before_reset + 120) is None
    assert not survey.continuation_due(config, now=before_reset + 120)


def test_lookup_count_survives_restart_without_getting_a_new_budget(config):
    save(config, planner=completed_planner(),
         lookup={"opponent_id": "opponent-a", "valid_draws": 1})
    restored = load(config)
    assert restored["lookup"] == {"opponent_id": "opponent-a", "valid_draws": 1}
    assert restored["planner"].lookup_plan(100).required_refreshes == 1


@pytest.mark.parametrize("lookup", [
    {"opponent_id": "missing", "valid_draws": 0},
    {"opponent_id": "opponent-a", "valid_draws": -1},
    {"opponent_id": "opponent-a", "valid_draws": True},
    {"opponent_id": "opponent-a", "valid_draws": 2},
])
def test_invalid_lookup_cannot_reset_or_expand_search_budget(config, lookup):
    with pytest.raises(ValueError):
        save(config, planner=completed_planner(), lookup=lookup)
    assert load(config) is None


def test_unfinished_survey_cannot_claim_to_be_relocating_a_certified_target(config):
    with pytest.raises(ValueError, match="confidence"):
        save(config, lookup={"opponent_id": "opponent-a", "valid_draws": 0})


@pytest.mark.parametrize("edit", [
    lambda v: v.update(version=True),
    lambda v: v.update(created_at=float("nan")),
    lambda v: v.update(updated_at=NOW + 100),
    lambda v: v.update(created_at=NOW + 1),
    lambda v: v.update(due_at=NOW - 1),
    lambda v: v.update(confidence=.9),
    lambda v: v.update(pilot=25),
    lambda v: v["planner"].update(complete=True),
    lambda v: v["planner"].update(draws=[[100, 100, 300]]),
    lambda v: v["identities"]["opponent-a"].update(signature="00"),
    lambda v: v["identities"]["opponent-a"].update(target=[1000, 720]),
    lambda v: v["identities"]["opponent-a"].update(target=[True, 100]),
    lambda v: v["identities"]["opponent-a"].update(name=""),
    lambda v: v["identities"]["opponent-a"]["choice"].update(opponent_id="wrong"),
    lambda v: v["candidates"].append(deepcopy(v["candidates"][0])),
    lambda v: v["candidates"][0].update(opponent_id="missing"),
    lambda v: v["candidates"][0].update(rank=572),
    lambda v: v["candidates"][0].update(visible_levels=[81]),
])
def test_corrupt_or_inconsistent_evidence_is_discarded(config, edit):
    save(config)
    corrupt(config, edit)
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW + 60)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null", b'{"version": 1}'])
def test_bad_json_is_ignored(config, raw):
    survey.survey_path(config).write_bytes(raw)
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW + 60)


def test_file_size_limit_applies_before_json_read(config, monkeypatch):
    monkeypatch.setattr(survey, "MAX_FILE_BYTES", 10)
    survey.survey_path(config).write_bytes(b" " * 11)
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW + 60)


def test_nonregular_file_and_symlink_are_rejected(config, tmp_path):
    path = survey.survey_path(config)
    path.mkdir()
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW + 60)
    path.rmdir()
    save(config)
    other = tmp_path / "elsewhere.json"
    path.replace(other)
    path.symlink_to(other)
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW + 60)


def test_platform_without_unix_open_flags_can_read_regular_checkpoint(config, monkeypatch):
    save(config)
    monkeypatch.delattr(survey.os, "O_NOFOLLOW", raising=False)
    monkeypatch.delattr(survey.os, "O_NONBLOCK", raising=False)
    assert load(config)["created_at"] == NOW


def test_atomic_write_failure_retains_previous_checkpoint_and_cleans_temporary(config, monkeypatch):
    save(config)
    original = survey.survey_path(config).read_bytes()
    def fail_replace(self, target):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        save(config, now=NOW + 60)
    assert survey.survey_path(config).read_bytes() == original
    assert list(config.state_dir.glob("*.tmp")) == []


def test_invalid_save_cannot_replace_good_checkpoint(config):
    save(config)
    original = survey.survey_path(config).read_bytes()
    with pytest.raises(ValueError):
        save(config, identities={})
    assert survey.survey_path(config).read_bytes() == original


@pytest.mark.parametrize("guard", ["pending", "blocked", "reserve", "unreadable"])
def test_due_survey_never_bypasses_battle_history_guards(config, guard):
    save(config)
    when = datetime.fromtimestamp(NOW, timezone.utc)
    if guard == "pending":
        tactical_state.begin_battle(config, DAY, "opponent-a", 5, 571, now=when)
    elif guard == "blocked":
        tactical_state.observe_ladder(config, DAY, tickets=5, rank=571, now=when)
        tactical_state.block(config, "Unresolved battle", now=when)
    elif guard == "reserve":
        tactical_state.observe_ladder(config, DAY, tickets=1, rank=571, now=when)
    else:
        tactical_state.state_path(config).write_text("bad json")
    before = tactical_state.state_path(config).read_bytes()
    assert not survey.continuation_due(config, now=NOW + 60)
    survey.clear_survey(config)
    assert tactical_state.state_path(config).read_bytes() == before


def test_exhausted_refresh_cap_does_not_schedule_an_endless_continuation(config):
    planner = RefreshPlanner(pilot=50, max_refreshes=50)
    for index in range(50):
        planner.observe((100 + index, 200 + index, 300 + index))
    save(config, planner=planner)
    assert load(config)["planner"].plan().status == "deferred"
    assert not survey.continuation_due(config, now=NOW + 60)


def test_scheduler_replays_only_when_atomic_snapshot_changes(config, monkeypatch):
    save(config)
    calls = []
    original = survey._validate
    def observed(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(survey, "_validate", observed)
    assert survey.continuation_due(config, now=NOW + 60)
    assert survey.continuation_due(config, now=NOW + 61)
    assert len(calls) == 1
    # Rewriting the same path must not reuse prior metadata or due time.
    save(config, now=NOW + 60, delay_seconds=180)
    calls.clear()
    assert not survey.continuation_due(config, now=NOW + 61)
    assert not survey.continuation_due(config, now=NOW + 62)
    assert len(calls) == 1
    assert survey.continuation_due(config, now=NOW + 240)


def test_identity_count_is_bounded(config, monkeypatch):
    monkeypatch.setattr(survey, "MAX_ENTRIES", 1)
    people = {key: identity(key) for key in ("opponent-a", "opponent-b")}
    with pytest.raises(ValueError, match="Too many"):
        save(config, identities=people)
