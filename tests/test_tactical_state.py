"""Ticket reservations survive crashes, unknown results, and daily reset."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from ba_automator.tactical_state import (
    TacticalStateError,
    battle_history,
    begin_battle,
    block,
    complete_battle,
    empty_state,
    ensure_restart_safe,
    observe_ladder,
    record_outcome,
    reconcile_pending,
    read_state,
    save_identities,
    state_for_day,
    state_path,
    write_state,
)


NOW = datetime(2026, 9, 26, 21, 0, tzinfo=timezone.utc)
DAY = "2026-09-26"


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(serial="127.0.0.1:5695", package="com.nexon.bluearchive", state_dir=tmp_path)


def begin(config, **kwargs):
    return begin_battle(config, DAY, "opponent-a", 5, 1000, now=NOW, **kwargs)


def complete(config, intent, **kwargs):
    return complete_battle(config, intent, tickets_after=4, won=False, now=NOW, **kwargs)


def test_missing_state_is_read_only(config):
    assert read_state(config) == empty_state()
    assert not state_path(config).exists()


def test_restart_guard_without_battle_history_is_read_only(config):
    ensure_restart_safe(config)
    assert not state_path(config).exists()


def test_restart_guard_preserves_unproven_intent_and_its_only_result_screen(config):
    begin(config)
    saved = state_path(config).read_bytes()
    with pytest.raises(TacticalStateError, match="leave Blue Archive open"):
        ensure_restart_safe(config)
    assert state_path(config).read_bytes() == saved


@pytest.mark.parametrize("won", [False, True])
def test_restart_guard_allows_saved_result_without_consuming_its_recovery(config, won):
    intent = begin(config)
    record_outcome(config, intent, won=won, evidence="battle-result.png", now=NOW)
    saved = state_path(config).read_bytes()
    ensure_restart_safe(config)
    assert state_path(config).read_bytes() == saved
    recovered = reconcile_pending(config, DAY, tickets=4, rank=900, now=NOW)
    assert recovered["pending"] is None
    assert recovered["attempts"] == {"opponent-a": 1}


def test_restart_guard_preserves_conflicting_result_for_inspection(config):
    intent = begin(config)
    record_outcome(config, intent, won=True, evidence="victory.png", now=NOW)
    with pytest.raises(TacticalStateError, match="Conflicting"):
        record_outcome(config, intent, won=False, evidence="defeat.png", now=NOW)
    with pytest.raises(TacticalStateError, match="leave Blue Archive open"):
        ensure_restart_safe(config)


@pytest.mark.parametrize("contents", ["{invalid json", "{}"])
def test_restart_guard_fails_closed_on_unreadable_recovery_state(config, contents):
    state_path(config).write_text(contents)
    with pytest.raises(TacticalStateError, match="Cannot verify Tactical Challenge recovery state"):
        ensure_restart_safe(config)
    assert state_path(config).read_text() == contents


def test_state_is_isolated_by_instance_and_package(config):
    begin(config)
    for serial, package in (("127.0.0.1:5555", config.package), (config.serial, "different.package")):
        other = SimpleNamespace(serial=serial, package=package, state_dir=config.state_dir)
        assert state_path(other) != state_path(config)
        assert read_state(other) == empty_state()


def test_begin_is_durable_before_mobilize_and_blocks_duplicate_after_restart(config):
    intent = begin(config)
    persisted = json.loads(state_path(config).read_text())
    assert persisted["pending"] == {
        "id": intent, "opponent_id": "opponent-a", "tickets_before": 5,
        "day_key": DAY, "rank_before": 1000, "preserve": 1, "created_at": NOW.isoformat(),
    }
    assert persisted["attempts"] == {}
    with pytest.raises(TacticalStateError, match="unresolved"):
        begin(config)
    assert read_state(config) == persisted


@pytest.mark.parametrize("day", ["2026-09-25", "2026-09-27"])
def test_reservation_requires_current_game_day_without_resetting_existing_history(config, day):
    complete(config, begin(config))
    saved = read_state(config)
    with pytest.raises(TacticalStateError, match="day changed before battle reservation"):
        begin_battle(config, day, "another-opponent", 5, 1000, now=NOW)
    assert read_state(config) == saved


def test_verified_defeat_moves_to_another_opponent_without_retry(config):
    state = complete(config, begin(config), rank_after=1000)
    assert state["pending"] is None and state["blocked_reason"] is None
    assert state["attempts"] == {"opponent-a": 1} and state["retry_opponent_id"] is None
    with pytest.raises(TacticalStateError, match="already been fought"):
        begin_battle(config, DAY, "opponent-a", 4, 1000, now=NOW)
    assert begin_battle(config, DAY, "opponent-b", 4, 1000, now=NOW)


def test_verified_victory_clears_retry_without_losing_attempt_history(config):
    state = complete_battle(config, begin(config), tickets_after=4, won=True, rank_after=700, now=NOW)
    assert state["attempts"] == {"opponent-a": 1}
    assert state["retry_opponent_id"] is None and state["last_rank"] == 700
    assert "victory" in state["last_summary"]
    assert battle_history(state).attempts == {"opponent-a": 1}


def test_completed_intent_cannot_be_replayed(config):
    intent = begin(config)
    state = complete(config, intent)
    with pytest.raises(TacticalStateError, match="matching"):
        complete(config, intent)
    assert read_state(config) == state


@pytest.mark.parametrize("tickets,won,rank", [
    (5, True, 700), (3, True, 700), (None, True, 700), (True, True, 700),
    (4, None, 700), (4, 1, 700), (4, True, 0), (4, True, False),
])
def test_unknown_result_or_ticket_mismatch_keeps_intent_across_day_reset(config, tickets, won, rank):
    intent = begin(config)
    with pytest.raises(TacticalStateError, match="uncertain"):
        complete_battle(config, intent, tickets_after=tickets, won=won, rank_after=rank, now=NOW)
    state = read_state(config)
    assert state["pending"]["id"] == intent
    assert state["attempts"] == {} and state["blocked_reason"]
    with pytest.raises(TacticalStateError, match="unresolved"):
        state_for_day(config, "2026-09-27", now=NOW + timedelta(days=1))
    with pytest.raises(TacticalStateError, match="unresolved"):
        begin_battle(config, "2026-09-27", "opponent-b", 5, 700, now=NOW + timedelta(days=1))
    assert read_state(config) == state


def test_rank_improvement_is_not_a_substitute_for_proven_result(config):
    intent = begin(config)
    with pytest.raises(TacticalStateError, match="uncertain"):
        complete_battle(config, intent, tickets_after=4, won=None, rank_after=500, now=NOW)
    assert read_state(config)["pending"]["id"] == intent


def test_exact_reconciliation_can_resolve_a_previous_uncertain_hold(config):
    intent = begin(config)
    with pytest.raises(TacticalStateError):
        complete_battle(config, intent, tickets_after=None, won=True, now=NOW)
    state = complete_battle(config, intent, tickets_after=4, won=True, rank_after=700, now=NOW + timedelta(seconds=10))
    assert state["pending"] is None and state["blocked_reason"] is None
    assert state["attempts"] == {"opponent-a": 1}


def test_wrong_intent_does_not_clear_or_modify_pending(config):
    begin(config)
    state = read_state(config)
    with pytest.raises(TacticalStateError, match="matching"):
        complete(config, "not-the-pending-intent")
    assert read_state(config) == state


def test_new_day_resets_only_resolved_attempt_history(config):
    complete(config, begin(config))
    new = state_for_day(config, "2026-09-27", now=NOW + timedelta(days=1))
    assert new["day_key"] == "2026-09-27"
    assert new["attempts"] == {} and new["retry_opponent_id"] is None
    assert new["last_tickets"] is None and new["last_rank"] is None
    assert begin_battle(config, "2026-09-27", "opponent-a", 5, 1000, now=NOW + timedelta(days=1))


def test_same_day_read_preserves_history_and_timestamp(config):
    completed = complete(config, begin(config))
    assert state_for_day(config, DAY, now=NOW + timedelta(minutes=10)) == completed


@pytest.mark.parametrize("tickets,reserve", [(0, 1), (1, 1), (4, 5)])
def test_reserve_rejects_entry_before_creating_intent(config, tickets, reserve):
    with pytest.raises(TacticalStateError, match="reserve"):
        begin_battle(config, DAY, "a", tickets, 1000, preserve=reserve, now=NOW)
    assert read_state(config)["pending"] is None


def test_zero_reserve_can_use_last_ticket_and_clears_retry(config):
    intent = begin_battle(config, DAY, "a", 1, 1000, preserve=0, now=NOW)
    state = complete_battle(config, intent, tickets_after=0, won=False, now=NOW)
    assert state["attempts"] == {"a": 1}
    assert state["retry_opponent_id"] is None and state["last_tickets"] == 0


def test_reaching_default_reserve_clears_retry_but_keeps_history(config):
    intent = begin_battle(config, DAY, "a", 2, 1000, now=NOW)
    state = complete_battle(config, intent, tickets_after=1, won=False, now=NOW)
    assert state["attempts"] == {"a": 1} and state["retry_opponent_id"] is None


def test_updated_user_reserve_is_respected_on_completion(config):
    state = complete_battle(config, begin(config), tickets_after=4, won=False, preserve=4, now=NOW)
    assert state["retry_opponent_id"] is None
    assert state["attempts"] == {"opponent-a": 1}


def test_external_ticket_use_to_reserve_drops_retry_without_erasing_history(config):
    complete(config, begin(config))
    state = observe_ladder(config, DAY, tickets=1, rank=1100, now=NOW)
    assert state["attempts"] == {"opponent-a": 1} and state["retry_opponent_id"] is None
    assert state["last_tickets"] == 1 and state["last_rank"] == 1100


def test_ladder_observation_cannot_clear_an_unresolved_intent(config):
    begin(config)
    state = read_state(config)
    with pytest.raises(TacticalStateError, match="unresolved"):
        observe_ladder(config, DAY, tickets=1, rank=700, now=NOW)
    assert read_state(config) == state


def test_first_place_spends_nothing(config):
    with pytest.raises(TacticalStateError, match="Already first"):
        begin_battle(config, DAY, "a", 5, 1, now=NOW)
    assert read_state(config) == empty_state()


def test_manual_hold_retains_intent_and_attempts(config):
    intent = begin(config)
    blocked = block(config, "Inspect the interrupted battle", now=NOW)
    assert blocked["pending"]["id"] == intent and blocked["attempts"] == {}
    with pytest.raises(TacticalStateError, match="unresolved"):
        begin(config)


def test_manual_hold_without_pending_is_not_cleared_by_new_day(config):
    state_for_day(config, DAY, now=NOW)
    blocked = block(config, "Inspect the formation", now=NOW)
    with pytest.raises(TacticalStateError, match="Inspect the formation"):
        state_for_day(config, "2026-09-27", now=NOW)
    assert read_state(config) == blocked


@pytest.mark.parametrize("body", ["not json", "[]", '{}', '{"version":2}'])
def test_corrupt_state_fails_closed(config, body):
    state_path(config).write_text(body)
    with pytest.raises(TacticalStateError, match="blocked"):
        read_state(config)


@pytest.mark.parametrize("change", [
    lambda s: s.update(version=True),
    lambda s: s.update(attempts={"a": 3}),
    lambda s: s.update(retry_opponent_id=True),
    lambda s: s["pending"].update(tickets_before=1),
    lambda s: s["pending"].update(day_key="2026-09-27"),
    lambda s: s["pending"].update(created_at="2026-09-26T21:00:00"),
    lambda s: s["pending"].update(unexpected=True),
])
def test_invalid_persisted_schema_cannot_authorize_spending(config, change):
    begin(config)
    state = json.loads(state_path(config).read_text())
    change(state)
    state_path(config).write_text(json.dumps(state))
    with pytest.raises(TacticalStateError, match="Invalid"):
        read_state(config)


def test_failed_atomic_replace_keeps_previous_file_and_cleans_temp(config, monkeypatch):
    from pathlib import Path
    original = state_for_day(config, DAY, now=NOW)
    content = state_path(config).read_bytes()
    def fail_replace(*args, **kwargs):
        raise OSError("simulated write failure")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        begin(config)
    assert state_path(config).read_bytes() == content
    assert read_state(config) == original
    assert list(config.state_dir.glob(".*.tmp")) == []


def test_invalid_write_cannot_replace_good_state(config):
    original = state_for_day(config, DAY, now=NOW)
    with pytest.raises(ValueError):
        write_state(config, {"version": 2})
    assert read_state(config) == original


@pytest.mark.parametrize("won", [True, False])
def test_saved_result_recovers_after_restart_exactly_once(config, won):
    intent = begin(config)
    evidence = str(config.state_dir / "battle-result.png")
    pending = record_outcome(config, intent, won=won, evidence=evidence,
                             now=NOW + timedelta(seconds=60))
    assert pending["attempts"] == {}
    assert pending["pending"]["outcome"] == {
        "won": won, "evidence": evidence,
        "observed_at": (NOW + timedelta(seconds=60)).isoformat(),
    }
    # A fresh config stands in for another process; no in-memory object is used.
    restarted = SimpleNamespace(**vars(config))
    recovered = reconcile_pending(restarted, DAY, tickets=4, rank=700 if won else 1000,
                                  now=NOW + timedelta(seconds=90))
    assert recovered["pending"] is None
    assert recovered["attempts"] == {"opponent-a": 1}
    assert recovered["retry_opponent_id"] is None
    again = reconcile_pending(restarted, DAY, tickets=4, rank=700 if won else 1000,
                              now=NOW + timedelta(seconds=100))
    assert again["attempts"] == recovered["attempts"]


def test_recovery_without_proven_result_preserves_pending_even_if_rank_improved(config):
    begin(config)
    original = read_state(config)
    with pytest.raises(TacticalStateError, match="no proven outcome"):
        reconcile_pending(config, DAY, tickets=4, rank=500, now=NOW)
    assert read_state(config) == original


@pytest.mark.parametrize("tickets", [None, True, 5, 3])
def test_saved_result_cannot_recover_without_exact_ticket_decrement(config, tickets):
    intent = begin(config)
    record_outcome(config, intent, won=True, evidence="result.png", now=NOW)
    with pytest.raises(TacticalStateError, match="uncertain"):
        reconcile_pending(config, DAY, tickets=tickets, rank=700, now=NOW)
    pending = read_state(config)
    assert pending["pending"]["id"] == intent and pending["attempts"] == {}
    # An uncertain first menu read does not invalidate the proven result.
    recovered = reconcile_pending(config, DAY, tickets=4, rank=700, now=NOW)
    assert recovered["attempts"] == {"opponent-a": 1}


@pytest.mark.parametrize("entry_point", ["direct", "new_day", "stale_day"])
def test_saved_result_cannot_reconcile_across_daily_reset(config, entry_point):
    intent = begin(config)
    record_outcome(config, intent, won=True, evidence="result.png", now=NOW)
    original = read_state(config)
    later = NOW + timedelta(days=1)
    with pytest.raises(TacticalStateError, match="reset"):
        if entry_point == "direct":
            complete_battle(config, intent, tickets_after=4, won=True, now=later)
        else:
            day = "2026-09-27" if entry_point == "new_day" else DAY
            reconcile_pending(config, day, tickets=4, rank=700, now=later)
    assert read_state(config) == original


def test_recovered_result_respects_changed_reserve(config):
    intent = begin(config)
    record_outcome(config, intent, won=False, evidence="result.png", now=NOW)
    recovered = reconcile_pending(config, DAY, tickets=4, rank=1000, preserve=4, now=NOW)
    assert recovered["attempts"] == {"opponent-a": 1}
    assert recovered["retry_opponent_id"] is None


def test_outcome_reobservation_is_idempotent_and_keeps_first_evidence(config):
    intent = begin(config)
    original = record_outcome(config, intent, won=True, evidence="first.png", now=NOW)
    assert record_outcome(config, intent, won=True, evidence="second.png",
                          now=NOW + timedelta(seconds=5)) == original


@pytest.mark.parametrize("entry_point", ["record", "complete"])
def test_conflicting_outcome_remains_held_instead_of_choosing_one(config, entry_point):
    intent = begin(config)
    record_outcome(config, intent, won=False, evidence="first.png", now=NOW)
    with pytest.raises(TacticalStateError, match="Conflicting"):
        if entry_point == "record":
            record_outcome(config, intent, won=True, evidence="other.png", now=NOW)
        else:
            complete_battle(config, intent, tickets_after=4, won=True, now=NOW)
    with pytest.raises(TacticalStateError, match="Conflicting"):
        reconcile_pending(config, DAY, tickets=4, rank=700, now=NOW)
    assert read_state(config)["pending"]["outcome"]["won"] is False
    assert read_state(config)["attempts"] == {}


@pytest.mark.parametrize("won,evidence", [(None, "result.png"), (1, "result.png"), (True, "")])
def test_invalid_outcome_evidence_never_replaces_pending(config, won, evidence):
    intent = begin(config)
    original = read_state(config)
    with pytest.raises(ValueError):
        record_outcome(config, intent, won=won, evidence=evidence, now=NOW)
    assert read_state(config) == original


def test_outcome_requires_matching_intent_and_chronological_observations(config):
    intent = begin(config)
    with pytest.raises(TacticalStateError, match="matching"):
        record_outcome(config, "other", won=True, evidence="result.png", now=NOW)
    with pytest.raises(ValueError, match="predate"):
        record_outcome(config, intent, won=True, evidence="result.png", now=NOW - timedelta(seconds=1))
    original = record_outcome(config, intent, won=True, evidence="result.png",
                              now=NOW + timedelta(seconds=20))
    with pytest.raises(ValueError, match="predate"):
        reconcile_pending(config, DAY, tickets=4, rank=700, now=NOW + timedelta(seconds=10))
    assert read_state(config) == original


def identity_metadata():
    return {"choice": {"opponent_id": "opponent-a", "rank": 900, "level": 80,
                       "visible_levels": [65, 80]},
            "name": "Anonymous", "target": [600, 230], "signature": "7f" * 576}


def test_canonical_identity_evidence_survives_restart_and_resets_with_history(config):
    original = identity_metadata()
    saved = save_identities(config, DAY, {"opponent-a": original}, now=NOW)
    original["choice"]["rank"] = 123
    saved["identities"]["opponent-a"]["signature"] = "changed"
    assert read_state(config)["identities"] == {"opponent-a": identity_metadata()}
    complete(config, begin(config))
    restarted = SimpleNamespace(**vars(config))
    persisted = state_for_day(restarted, DAY, now=NOW + timedelta(minutes=3))
    assert persisted["identities"] == {"opponent-a": identity_metadata()}
    assert persisted["retry_opponent_id"] is None
    reset = state_for_day(restarted, "2026-09-27", now=NOW + timedelta(days=1))
    assert reset["identities"] == {} and reset["attempts"] == {}


def test_identity_replacement_cannot_drop_an_attempted_opponent(config):
    save_identities(config, DAY, {"opponent-a": identity_metadata()}, now=NOW)
    complete(config, begin(config))
    original = read_state(config)
    with pytest.raises(TacticalStateError, match="already attempted"):
        save_identities(config, DAY, {}, now=NOW)
    assert read_state(config) == original


def test_pending_intent_prevents_identity_replacement_or_reset(config):
    save_identities(config, DAY, {"opponent-a": identity_metadata()}, now=NOW)
    begin(config)
    original = read_state(config)
    with pytest.raises(TacticalStateError, match="unresolved"):
        save_identities(config, "2026-09-27", {}, now=NOW + timedelta(days=1))
    assert read_state(config) == original


@pytest.mark.parametrize("identities", [
    [], {"": {}}, {"a": []}, {"a": {"signature": b"bytes"}},
    {"a": {"number": float("nan")}}, {"a": {"signature": "a" * 8192}},
    {str(i): {} for i in range(1001)},
])
def test_identity_metadata_is_bounded_finite_json(config, identities):
    with pytest.raises(ValueError):
        save_identities(config, DAY, identities, now=NOW)
    assert read_state(config) == empty_state()


@pytest.mark.parametrize("pending", [True, False])
def test_older_state_without_identities_upgrades_without_erasing_pending(config, pending):
    if pending:
        begin(config)
    else:
        complete(config, begin(config))
    old = read_state(config)
    del old["identities"]
    serialized = json.dumps(old)
    state_path(config).write_text(serialized)
    upgraded = read_state(config)
    assert upgraded == dict(old, identities={})
    assert state_path(config).read_text() == serialized
    if pending:
        with pytest.raises(TacticalStateError, match="unresolved"):
            begin(config)
    else:
        assert upgraded["retry_opponent_id"] is None


@pytest.mark.parametrize("attempts", [1, 2])
def test_legacy_attempt_counts_survive_and_retry_hint_is_cleared(config, attempts):
    state = state_for_day(config, DAY, now=NOW)
    state.update(attempts={"opponent-a": attempts}, retry_opponent_id="opponent-a")
    write_state(config, state)
    restored = state_for_day(config, DAY, now=NOW)
    assert restored["attempts"] == {"opponent-a": attempts}
    assert restored["retry_opponent_id"] is None
    with pytest.raises(TacticalStateError, match="already been fought"):
        begin(config)


def test_legacy_already_entered_second_battle_can_be_reconciled_without_replaying(config):
    intent = begin(config)
    old = read_state(config)
    old.update(attempts={"opponent-a": 1}, retry_opponent_id="opponent-a")
    write_state(config, old)
    record_outcome(config, intent, won=False, evidence="legacy-result.png", now=NOW)
    recovered = reconcile_pending(config, DAY, tickets=4, rank=1000, now=NOW)
    assert recovered["attempts"] == {"opponent-a": 2}
    assert recovered["pending"] is None and recovered["retry_opponent_id"] is None
    with pytest.raises(TacticalStateError, match="already been fought"):
        begin(config)
