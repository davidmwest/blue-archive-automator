"""Search checkpoints preserve the time budget without authorizing a battle."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_automator import tactical_state, tactical_survey as survey
from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_search import SearchPolicy, SearchState


NOW = datetime(2026, 9, 26, 21, tzinfo=timezone.utc).timestamp()
DAY = "2026-09-26"


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(state_dir=tmp_path, serial="127.0.0.1:5695",
                           package="com.nexon.bluearchive",
                           tactical_battles_search_minutes=10,
                           tactical_battles_preserve_tickets=1)


def identity(key="opponent-a", rank=100):
    return {"choice": asdict(Opponent(key, rank, 80, (80, 75))),
            "name": "Anonymous", "target": [1000, 250], "signature": "0123" * 288}


def search_state(elapsed=20):
    search = SearchState(SearchPolicy(600))
    search.observe((Opponent(**identity()["choice"]),), refreshed=False)
    search.advance(elapsed)
    return search


def arguments(**overrides):
    values = dict(day_key=DAY, own_rank=571, search=search_state(),
                  identities={"opponent-a": identity()}, now=NOW)
    values.update(overrides)
    return values


def save(config, **overrides):
    survey.save_survey(config, **arguments(**overrides))


def load(config, **overrides):
    values = dict(day_key=DAY, own_rank=571, time_budget_seconds=600, now=NOW)
    values.update(overrides)
    return survey.load_survey(config, **values)


def corrupt(config, edit):
    path = survey.survey_path(config)
    value = json.loads(path.read_text())
    edit(value)
    path.write_text(json.dumps(value))


def test_missing_survey_is_read_only(config):
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW)
    assert not survey.block_survey(config, now=NOW)
    survey.clear_survey(config)
    assert list(config.state_dir.iterdir()) == []


def test_progress_replays_and_preserves_mutable_candidate_copies(config):
    options = arguments()
    options["search"].observe((Opponent(**identity()["choice"]),))
    survey.save_survey(config, **options)
    options["identities"]["opponent-a"]["name"] = "Changed after save"
    restored = load(config)
    assert restored["search"].to_dict() == options["search"].to_dict()
    assert restored["identities"]["opponent-a"]["name"] == "Anonymous"
    assert restored["created_at"] == restored["updated_at"] == NOW
    assert restored["due_at"] == NOW + 60
    restored["search"].advance(100)
    assert load(config)["search"].elapsed_seconds == 20


@pytest.mark.parametrize("version", [1, 2])
def test_old_confidence_checkpoint_is_discarded_without_touching_battle_history(config, version):
    save(config)
    corrupt(config, lambda value: value.update(version=version))
    tactical_state.begin_battle(config, DAY, "opponent-a", 5, 571,
                                now=datetime.fromtimestamp(NOW, timezone.utc))
    durable = tactical_state.state_path(config).read_bytes()
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW + 60)
    save(config, now=NOW + 120)
    restored = load(config, now=NOW + 120)
    assert restored["version"] == 4
    assert restored["created_at"] == NOW + 120
    assert tactical_state.state_path(config).read_bytes() == durable


def test_checkpoint_is_isolated_by_instance_and_package(config):
    save(config)
    for change in ({"serial": "127.0.0.1:5555"}, {"package": "another.game"}):
        other = SimpleNamespace(**(vars(config) | change))
        assert survey.survey_path(other) != survey.survey_path(config)
        assert load(other) is None
    assert survey.survey_path(config) != tactical_state.state_path(config)


def test_interrupted_search_keeps_elapsed_budget_scores_and_decisions(config):
    uninterrupted, resumed = search_state(), search_state()
    person = Opponent(**identity()["choice"])
    for index in range(30):
        for search in (uninterrupted, resumed):
            search.advance(19)
            search.observe((person,))
        if index in (3, 14, 23):
            save(config, search=resumed, now=NOW + index)
            resumed = load(config, now=NOW + index)["search"]
        assert resumed.to_dict() == uninterrupted.to_dict()
        assert resumed.decide((person,)) == uninterrupted.decide((person,))


@pytest.mark.parametrize("changed", [
    {"day_key": "2026-09-27"}, {"time_budget_seconds": 300},
])
def test_mismatched_context_cannot_resume(config, changed):
    save(config)
    assert load(config, **changed) is None


@pytest.mark.parametrize("new_rank", [1, 570, 600])
@pytest.mark.parametrize("elapsed", [250, 600])
def test_passive_rank_change_retains_budget_benchmark_and_age(config, new_rank, elapsed):
    original = search_state(elapsed)
    save(config, search=original)
    restored = load(config, own_rank=new_rank, now=NOW + 60)
    assert restored["search"].to_dict() == original.to_dict()
    save(config, own_rank=new_rank, search=restored["search"], now=NOW + 60)
    updated = load(config, own_rank=new_rank, now=NOW + 60)
    assert updated["own_rank"] == new_rank
    assert updated["created_at"] == NOW
    assert updated["search"].elapsed_seconds == elapsed
    with pytest.raises(ValueError, match="already used"):
        save(config, own_rank=999, search=search_state(elapsed - 1), now=NOW + 120)


def test_continuation_requires_due_time_and_current_settings(config):
    save(config)
    assert not survey.continuation_due(config, now=NOW + 59)
    assert survey.continuation_due(config, now=NOW + 60)
    config.tactical_battles_search_minutes = 5
    assert not survey.continuation_due(config, now=NOW + 60)


def test_repeated_saves_do_not_extend_active_evidence_lifetime(config):
    save(config)
    save(config, now=NOW + 3 * 3600)
    value = load(config, now=NOW + 3 * 3600 + 60)
    assert value["created_at"] == NOW
    assert value["updated_at"] == NOW + 3 * 3600
    assert survey.continuation_due(config, now=NOW + 4 * 3600 - 1)
    expired = load(config, now=NOW + 4 * 3600)
    assert expired["status"] == "blocked"
    assert expired["search"].elapsed_seconds == 20
    assert not survey.continuation_due(config, now=NOW + 4 * 3600)
    save(config, now=NOW + 4 * 3600)
    restored = load(config, now=NOW + 4 * 3600)
    assert restored["created_at"] == NOW
    assert restored["status"] == "blocked"
    with pytest.raises(ValueError, match="already used"):
        save(config, now=NOW + 5 * 3600, search=search_state(0))


@pytest.mark.parametrize("exhausted", [False, True])
def test_server_reset_expires_even_an_exhausted_survey(config, exhausted):
    before_reset = datetime(2026, 9, 27, 18, 58, tzinfo=timezone.utc).timestamp()
    save(config, now=before_reset, search=search_state(600 if exhausted else 20))
    assert load(config, now=before_reset + 60) is not None
    assert load(config, now=before_reset + 120) is None
    assert not survey.continuation_due(config, now=before_reset + 120)


def test_deadline_checkpoint_continues_overtime_without_resetting_elapsed_time(config):
    save(config, search=search_state(600))
    assert survey.continuation_due(config, now=NOW + 60)
    expired = load(config, now=NOW + 5 * 3600)
    assert expired["search"].deadline_reached
    assert expired["status"] == "blocked"
    assert not survey.continuation_due(config, now=NOW + 5 * 3600)
    original = survey.survey_path(config).read_bytes()
    with pytest.raises(ValueError, match="already used"):
        save(config, now=NOW + 5 * 3600, search=search_state(0))
    assert survey.survey_path(config).read_bytes() == original


@pytest.mark.parametrize("status", ["active", "blocked"])
def test_budget_cannot_go_backwards_on_retry_or_restart(config, status):
    save(config, search=search_state(250), status=status)
    with pytest.raises(ValueError, match="already used"):
        save(config, search=search_state(249), now=NOW + 60)
    assert load(config)["search"].elapsed_seconds == 250


def test_blocking_retains_budget_and_identity_but_stops_automatic_continuation(config):
    save(config, search=search_state(275))
    assert survey.continuation_due(config, now=NOW + 60)
    assert survey.block_survey(config, now=NOW + 60)
    restored = load(config, now=NOW + 60)
    assert restored["status"] == "blocked"
    assert restored["search"].elapsed_seconds == 275
    assert restored["identities"] == {"opponent-a": identity() | {
        "choice": identity()["choice"] | {"visible_levels": [80, 75]}}}
    assert not survey.continuation_due(config, now=NOW + 120)
    resumed = restored["search"]
    resumed.advance(25)
    save(config, search=resumed, now=NOW + 900)
    assert load(config, now=NOW + 900)["search"].elapsed_seconds == 300
    assert survey.continuation_due(config, now=NOW + 960)


@pytest.mark.parametrize("edit", [
    lambda v: v.update(version=True),
    lambda v: v.update(created_at=float("nan")),
    lambda v: v.update(updated_at=NOW + 100),
    lambda v: v.update(created_at=NOW + 1),
    lambda v: v.update(due_at=NOW - 1),
    lambda v: v.update(status="finished"),
    lambda v: v.update(status=[]),
    lambda v: v["search"].update(elapsed_seconds=-1),
    lambda v: v["search"].update(elapsed_seconds=601),
    lambda v: v["search"].update(benchmark_observations=5),
    lambda v: v["search"]["scores"].update({"missing": 20}),
    lambda v: v["search"].update(benchmark_ids=["missing"]),
    lambda v: v["identities"]["opponent-a"].update(signature="00"),
    lambda v: v["identities"]["opponent-a"].update(target=[1000, 720]),
    lambda v: v["identities"]["opponent-a"].update(target=[True, 100]),
    lambda v: v["identities"]["opponent-a"].update(name=""),
    lambda v: v["identities"]["opponent-a"]["choice"].update(opponent_id="wrong"),
])
def test_corrupt_or_inconsistent_evidence_is_discarded(config, edit):
    save(config)
    corrupt(config, edit)
    assert load(config) is None
    assert not survey.continuation_due(config, now=NOW + 60)
    original = survey.survey_path(config).read_bytes()
    assert not survey.block_survey(config, now=NOW + 60)
    assert survey.survey_path(config).read_bytes() == original


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
@pytest.mark.parametrize("elapsed", [20, 600])
def test_due_survey_never_bypasses_battle_history_guards(config, guard, elapsed):
    save(config, search=search_state(elapsed))
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


@pytest.mark.parametrize("version", [3, 4])
def test_legacy_exhausted_checkpoint_resumes_same_search_with_stable_identity(config, version):
    save(config, search=search_state(600))
    def downgrade(value):
        value["version"] = version
        if version == 3:
            value.pop("search_id")
        value["search"]["version"] = 1
        value["search"].pop("overtime_refreshes")
        value["search"].pop("overtime_threshold")
    corrupt(config, downgrade)
    old_checkpoint = survey.survey_path(config).read_bytes()
    original = load(config)
    assert original["search_id"] == load(config)["search_id"]
    assert original["search"].elapsed_seconds == 600
    assert original["search"].overtime_refreshes == 0
    assert survey.survey_path(config).read_bytes() == old_checkpoint
    when = datetime.fromtimestamp(NOW, timezone.utc)
    tactical_state.observe_ladder(config, DAY, tickets=4, rank=571, now=when)
    assert not survey.continuation_due(config, now=NOW + 5 * 3600)
    tactical_state.record_search_timeout(config, DAY, original["search_id"], now=when)
    assert survey.continuation_due(config, now=NOW + 60)
    resumed = original["search"]
    resumed.observe((Opponent(**identity()["choice"]),), refreshed=True)
    save(config, search=resumed, now=NOW + 60)
    restored = load(config, now=NOW + 60)
    assert restored["search_id"] == original["search_id"]
    assert restored["search"].remaining_seconds == 0
    assert restored["search"].overtime_refreshes == 1
    assert restored["created_at"] == NOW
    assert tactical_state.read_state(config)["timed_out_searches"] == [original["search_id"]]


@pytest.mark.parametrize("elapsed", [200, 600])
def test_rotation_cannot_reset_search_even_with_legacy_timeout_audit(config, elapsed):
    when = datetime.fromtimestamp(NOW, timezone.utc)
    tactical_state.observe_ladder(config, DAY, tickets=4, rank=571, now=when)
    save(config, search=search_state(elapsed))
    with pytest.raises(ValueError, match="same search identity"):
        save(config, search=search_state(0), search_id="c" * 32)
    tactical_state.record_search_timeout(config, DAY, load(config)["search_id"], now=when)
    with pytest.raises(ValueError, match="same search identity"):
        save(config, search=search_state(0), search_id="c" * 32)
    assert load(config)["search"].elapsed_seconds == elapsed


def test_last_ticket_above_reserve_continues_after_historical_timeout(config):
    when = datetime.fromtimestamp(NOW, timezone.utc)
    tactical_state.observe_ladder(config, DAY, tickets=2, rank=571, now=when)
    save(config, search=search_state(600))
    assert survey.continuation_due(config, now=NOW + 60)
    tactical_state.record_search_timeout(config, DAY, load(config)["search_id"], now=when)
    assert survey.continuation_due(config, now=NOW + 60)
    with pytest.raises(ValueError, match="same search identity"):
        save(config, search=search_state(0), search_id="d" * 32)


def test_failed_atomic_overtime_save_keeps_prior_progress_resumable(config, monkeypatch):
    when = datetime.fromtimestamp(NOW, timezone.utc)
    tactical_state.observe_ladder(config, DAY, tickets=4, rank=571, now=when)
    save(config, search=search_state(600))
    before = load(config)
    tactical_state.record_search_timeout(config, DAY, before["search_id"], now=when)
    def failed(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(Path, "replace", failed)
    progressed = before["search"]
    progressed.observe((Opponent(**identity()["choice"]),), refreshed=True)
    with pytest.raises(OSError, match="disk failure"):
        save(config, search=progressed, now=NOW + 60)
    assert load(config)["search_id"] == before["search_id"]
    assert load(config)["search"].exhausted
    assert load(config)["search"].overtime_refreshes == 0
    assert survey.continuation_due(config, now=NOW + 60)


@pytest.mark.parametrize("field", ["overtime_refreshes", "overtime_threshold"])
def test_overtime_checkpoint_cannot_move_backwards(config, field):
    search = search_state(600)
    search.observe((Opponent(**identity()["choice"]),), refreshed=True)
    save(config, search=search)
    before = survey.survey_path(config).read_bytes()
    setattr(search, field, 0 if field == "overtime_refreshes" else None)
    with pytest.raises(ValueError, match="overtime progress"):
        save(config, search=search, now=NOW + 60)
    assert survey.survey_path(config).read_bytes() == before


def test_clearing_resolved_search_allows_fresh_timer_without_resetting_battle_history(config):
    when = datetime.fromtimestamp(NOW, timezone.utc)
    tactical_state.observe_ladder(config, DAY, tickets=4, rank=571, now=when)
    save(config, search=search_state(600))
    old_id = load(config)["search_id"]
    history = tactical_state.state_path(config).read_bytes()
    survey.clear_survey(config)
    save(config, search=search_state(0), now=NOW + 60)
    restored = load(config, now=NOW + 60)
    assert restored["search_id"] != old_id
    assert restored["search"].remaining_seconds == 600
    assert tactical_state.state_path(config).read_bytes() == history


def test_overtime_does_not_dispatch_from_a_previous_days_ticket_observation(config):
    previous = datetime.fromtimestamp(NOW - 86400, timezone.utc)
    tactical_state.observe_ladder(config, "2026-09-25", tickets=4, rank=571, now=previous)
    save(config, search=search_state(600))
    assert not survey.continuation_due(config, now=NOW + 60)
