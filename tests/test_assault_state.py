from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from ba_automator.assault_policy import AssaultContext, MockResult, TeamMember
from ba_automator.assault_state import (
    AssaultStateError, PROOF_LIFETIME_SECONDS, begin_entry, begin_sweep, block,
    complete_entry, complete_sweep, read_state, record_mock, state_path,
)


NOW = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
CONTEXT = AssaultContext("season-1", "Binah", "hardcore", "2026-09-25")
TEAM = tuple(TeamMember(f"student-{slot}", slot, "striker" if slot < 4 else "special",
                        "piercing", 3, 80) for slot in range(6))
MOCK = MockResult(True, 60, tuple(f"student-{slot}" for slot in range(4)), {})


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(serial="127.0.0.1:5695", package="com.nexon.bluearchive", state_dir=tmp_path)


def qualify(config, **kwargs):
    return record_mock(config, CONTEXT, TEAM, MOCK, "run-a", now=NOW, **kwargs)


def enter(config):
    qualify(config)
    return begin_entry(config, CONTEXT, TEAM, "run-a", 3, now=NOW)


def clear(config):
    intent = enter(config)
    return complete_entry(config, intent, tickets_after=2, won=True, now=NOW)


def test_entry_persists_intent_and_consumes_qualification_before_device_input(config):
    qualify(config)
    intent = begin_entry(config, CONTEXT, TEAM, "run-a", 3, now=NOW)
    persisted = json.loads(state_path(config).read_text())
    assert persisted["pending"]["id"] == intent
    assert persisted["pending"]["tickets_before"] == 3
    assert persisted["proof"] is None
    # Simulated process restart: all checks read the durable file, not memory.
    with pytest.raises(AssaultStateError, match="unresolved"):
        begin_entry(config, CONTEXT, TEAM, "run-b", 3, now=NOW)
    with pytest.raises(AssaultStateError, match="unresolved"):
        qualify(config)


@pytest.mark.parametrize("change", [
    {"event_id": "season-2"}, {"boss": "Hod"}, {"difficulty": "extreme"},
    {"day_key": "2026-09-26"},
])
def test_mock_cannot_authorize_different_boss_event_day_or_difficulty(config, change):
    qualify(config)
    with pytest.raises(AssaultStateError, match="fresh"):
        begin_entry(config, replace(CONTEXT, **change), TEAM, "run-a", 3, now=NOW)
    assert read_state(config)["pending"] is None


def test_mock_cannot_authorize_team_change_or_process_restart(config):
    qualify(config)
    modified_team = (replace(TEAM[0], level=81),) + TEAM[1:]
    for team, run_id in ((modified_team, "run-a"), (TEAM, "run-b")):
        with pytest.raises(AssaultStateError, match="fresh"):
            begin_entry(config, CONTEXT, team, run_id, 3, now=NOW)


@pytest.mark.parametrize("offset", [-1, PROOF_LIFETIME_SECONDS, PROOF_LIFETIME_SECONDS + 1])
def test_expired_or_future_mock_does_not_authorize_entry(config, offset):
    qualify(config)
    with pytest.raises(AssaultStateError, match="expired"):
        begin_entry(config, CONTEXT, TEAM, "run-a", 3, now=NOW + timedelta(seconds=offset))


def test_failed_second_mock_erases_previous_success(config):
    qualify(config)
    record_mock(config, CONTEXT, TEAM, replace(MOCK, won=False), "run-a", now=NOW)
    assert read_state(config)["proof"] is None
    with pytest.raises(AssaultStateError, match="fresh"):
        begin_entry(config, CONTEXT, TEAM, "run-a", 3, now=NOW)


def test_verified_clear_allows_remaining_tickets_to_be_swept_once(config):
    clear(config)
    intent = begin_sweep(config, CONTEXT, "run-a", 2, 2, now=NOW)
    assert read_state(config)["pending"]["count"] == 2
    done = complete_sweep(config, intent, tickets_after=0, rewards_verified=True, now=NOW)
    assert done["clear"]["tickets_after"] == 0
    assert done["pending"] is None
    with pytest.raises(AssaultStateError, match="matching"):
        complete_sweep(config, intent, tickets_after=0, rewards_verified=True, now=NOW)
    with pytest.raises(AssaultStateError, match="balance"):
        begin_sweep(config, CONTEXT, "run-a", 2, 2, now=NOW)


def test_partial_sweeps_require_updated_observed_balance(config):
    clear(config)
    first = begin_sweep(config, CONTEXT, "run-a", 2, 1, now=NOW)
    complete_sweep(config, first, tickets_after=1, rewards_verified=True, now=NOW)
    second = begin_sweep(config, CONTEXT, "run-a", 1, 1, now=NOW)
    complete_sweep(config, second, tickets_after=0, rewards_verified=True, now=NOW)


@pytest.mark.parametrize("tickets,won", [(3, True), (1, True), (None, True), (2, None), (True, True)])
def test_ambiguous_real_result_preserves_intent_and_blocks_even_next_day(config, tickets, won):
    intent = enter(config)
    with pytest.raises(AssaultStateError, match="uncertain"):
        complete_entry(config, intent, tickets_after=tickets, won=won, now=NOW)
    assert read_state(config)["pending"]["id"] == intent
    with pytest.raises(AssaultStateError, match="unresolved"):
        record_mock(config, replace(CONTEXT, day_key="2026-09-26"), TEAM, MOCK, "run-b", now=NOW)


def test_verified_real_loss_requires_new_mock_but_allows_a_later_lower_difficulty(config):
    intent = enter(config)
    done = complete_entry(config, intent, tickets_after=2, won=False, now=NOW)
    assert done["pending"] is None
    assert done["clear"] is None
    assert done["blocked_reason"] is None and done["proof"] is None
    assert "lower difficulty or play manually" in done["last_summary"]
    with pytest.raises(AssaultStateError, match="fresh"):
        begin_entry(config, CONTEXT, TEAM, "run-a", 2, now=NOW)
    lower = replace(CONTEXT, difficulty="very_hard")
    with pytest.raises(AssaultStateError, match="fresh"):
        begin_entry(config, lower, TEAM, "run-b", 2, now=NOW)
    record_mock(config, lower, TEAM, MOCK, "run-b", now=NOW)
    replacement = begin_entry(config, lower, TEAM, "run-b", 2, now=NOW)
    assert read_state(config)["pending"]["id"] == replacement


def test_sweep_cannot_use_a_mock_only_or_old_run_or_different_context(config):
    qualify(config)
    with pytest.raises(AssaultStateError, match="real clear"):
        begin_sweep(config, CONTEXT, "run-a", 3, 3, now=NOW)
    clear(config)
    for context, run_id in ((CONTEXT, "run-b"), (replace(CONTEXT, difficulty="extreme"), "run-a")):
        with pytest.raises(AssaultStateError, match="real clear"):
            begin_sweep(config, context, run_id, 2, 2, now=NOW)


@pytest.mark.parametrize("tickets,rewards", [(2, True), (1, True), (None, True), (0, False), (0, 1)])
def test_ambiguous_sweep_preserves_intent(config, tickets, rewards):
    clear(config)
    intent = begin_sweep(config, CONTEXT, "run-a", 2, 2, now=NOW)
    with pytest.raises(AssaultStateError, match="uncertain"):
        complete_sweep(config, intent, tickets_after=tickets, rewards_verified=rewards, now=NOW)
    assert read_state(config)["pending"]["id"] == intent
    with pytest.raises(AssaultStateError, match="unresolved"):
        begin_sweep(config, CONTEXT, "run-a", 2, 2, now=NOW)


def test_verified_sweep_reconciliation_resolves_only_its_matching_uncertain_intent(config):
    clear(config)
    intent = begin_sweep(config, CONTEXT, "run-a", 2, 2, now=NOW)
    with pytest.raises(AssaultStateError, match="uncertain"):
        complete_sweep(config, intent, tickets_after=None, rewards_verified=True, now=NOW)
    held = read_state(config)
    assert held["blocked_reason"] is not None

    tomorrow = NOW + timedelta(days=1)
    next_context = replace(CONTEXT, day_key="2026-09-26")
    with pytest.raises(AssaultStateError, match="unresolved"):
        record_mock(config, next_context, TEAM, MOCK, "run-b", now=tomorrow)
    with pytest.raises(AssaultStateError, match="matching"):
        complete_sweep(config, "not-the-pending-intent", tickets_after=0,
                       rewards_verified=True, now=NOW)
    assert read_state(config) == held

    reconciled = complete_sweep(config, intent, tickets_after=0,
                                rewards_verified=True, now=NOW + timedelta(seconds=1))
    assert reconciled["pending"] is None and reconciled["blocked_reason"] is None
    assert reconciled["clear"]["tickets_after"] == 0
    # Reconciliation records what happened; it does not grant tomorrow a free
    # entry or sweep. Tomorrow's entry still needs a newly verified mock.
    with pytest.raises(AssaultStateError, match="fresh"):
        begin_entry(config, next_context, TEAM, "run-b", 3, now=tomorrow)
    with pytest.raises(AssaultStateError, match="real clear"):
        begin_sweep(config, next_context, "run-b", 3, 3, now=tomorrow)
    record_mock(config, next_context, TEAM, MOCK, "run-b", now=tomorrow)
    next_intent = begin_entry(config, next_context, TEAM, "run-b", 3, now=tomorrow)
    assert read_state(config)["pending"]["id"] == next_intent


@pytest.mark.parametrize("day_offset", [0, 1])
def test_completed_visit_requires_new_mock_and_clear_on_a_later_run(config, day_offset):
    clear(config)
    intent = begin_sweep(config, CONTEXT, "run-a", 2, 2, now=NOW)
    complete_sweep(config, intent, tickets_after=0, rewards_verified=True, now=NOW)
    later = NOW + timedelta(days=day_offset, seconds=1)
    context = replace(CONTEXT, day_key=later.date().isoformat())
    for attempt in (
        lambda: begin_entry(config, context, TEAM, "run-b", 3, now=later),
        lambda: begin_sweep(config, context, "run-b", 3, 3, now=later),
    ):
        with pytest.raises(AssaultStateError):
            attempt()
    record_mock(config, context, TEAM, MOCK, "run-b", now=later)
    next_intent = begin_entry(config, context, TEAM, "run-b", 3, now=later)
    assert read_state(config)["pending"]["id"] == next_intent
    assert read_state(config)["clear"] is None


@pytest.mark.parametrize("tickets", [None, True, -1, 0, 1.5])
def test_unknown_or_invalid_tickets_stop_before_any_intent(config, tickets):
    qualify(config)
    with pytest.raises(ValueError):
        begin_entry(config, CONTEXT, TEAM, "run-a", tickets, now=NOW)
    assert read_state(config)["pending"] is None


def test_manual_hold_preserves_unresolved_action_and_is_instance_isolated(config):
    intent = enter(config)
    state = block(config, "No compatible assistant; lower difficulty or play manually", now=NOW)
    assert state["pending"]["id"] == intent
    other = SimpleNamespace(**{**vars(config), "serial": "127.0.0.1:5675"})
    assert read_state(other)["pending"] is None


@pytest.mark.parametrize("raw", ["{", "[]", '{"version":1}', '{"version":99}'])
def test_corrupt_state_fails_closed(config, raw):
    state_path(config).write_text(raw)
    with pytest.raises(AssaultStateError, match="blocked"):
        read_state(config)


@pytest.mark.parametrize("mutation", [
    lambda state: state.update(clear=None),
    lambda state: state["clear"].update(tickets_after=3),
    lambda state: state["clear"].update(run_id="other-run"),
    lambda state: state["clear"]["context"].update(difficulty="normal"),
    lambda state: state.update(version=True),
])
def test_inconsistent_persisted_sweep_state_cannot_be_completed(config, mutation):
    clear(config)
    intent = begin_sweep(config, CONTEXT, "run-a", 2, 2, now=NOW)
    value = json.loads(state_path(config).read_text())
    mutation(value)
    state_path(config).write_text(json.dumps(value))
    with pytest.raises(AssaultStateError, match="blocked"):
        complete_sweep(config, intent, tickets_after=0, rewards_verified=True, now=NOW)


def test_write_failure_prevents_returning_a_ticket_authorization(config, monkeypatch):
    from ba_automator import assault_state

    qualify(config)
    def fail_write(*args):
        raise OSError("disk full")
    monkeypatch.setattr(assault_state, "write_state", fail_write)
    with pytest.raises(OSError, match="disk full"):
        begin_entry(config, CONTEXT, TEAM, "run-a", 3, now=NOW)
    assert read_state(config)["pending"] is None
