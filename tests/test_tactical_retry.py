"""Recoverable scouting failures get a bounded, durable retry allowance."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from ba_automator import tactical_retry as retry, tactical_state as history


NOW = datetime(2026, 9, 26, 21, tzinfo=timezone.utc).timestamp()
DAY = "2026-09-26"
BACKOFF = 900


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(
        state_dir=tmp_path, serial="127.0.0.1:5695", package="com.nexon.bluearchive",
        tactical_battles_enabled_in_daily=True, tactical_battles_preserve_tickets=1,
    )


def observe(config, *, now=NOW, day=DAY, tickets=4):
    return history.observe_ladder(
        config, day, tickets=tickets, rank=571,
        now=datetime.fromtimestamp(now, timezone.utc),
    )


def test_missing_history_does_not_create_a_retry(config):
    assert not retry.retry_due(config, now=NOW)
    assert not retry.claim_retry(config, now=NOW)
    assert not retry.schedule_retry(config, now=NOW)
    retry.clear_retry(config)
    assert list(config.state_dir.iterdir()) == []


def test_retry_waits_fifteen_minutes_and_can_be_claimed_only_once(config):
    observe(config)
    before = history.state_path(config).read_bytes()
    assert retry.schedule_retry(config, now=NOW)
    assert not retry.retry_due(config, now=NOW + BACKOFF - 1)
    assert not retry.claim_retry(config, now=NOW + BACKOFF - 1)
    assert retry.retry_due(config, now=NOW + BACKOFF)
    assert retry.claim_retry(config, now=NOW + BACKOFF)
    assert not retry.retry_due(config, now=NOW + BACKOFF)
    assert not retry.claim_retry(config, now=NOW + BACKOFF)
    assert history.state_path(config).read_bytes() == before


def test_scheduling_an_existing_retry_does_not_delay_or_consume_another_attempt(config):
    observe(config)
    assert retry.schedule_retry(config, now=NOW)
    retry.schedule_retry(config, now=NOW + BACKOFF - 1)
    assert retry.claim_retry(config, now=NOW + BACKOFF)
    for number in range(1, 3):
        scheduled_at = NOW + BACKOFF * number
        assert retry.schedule_retry(config, now=scheduled_at)
        assert retry.claim_retry(config, now=scheduled_at + BACKOFF)
    assert not retry.schedule_retry(config, now=NOW + BACKOFF * 3)


def test_three_retry_cap_survives_a_new_config_instance(config):
    observe(config)
    assert retry.MAX_DAILY_RETRIES == 3
    for number in range(3):
        scheduled_at = NOW + BACKOFF * number
        assert retry.schedule_retry(config, now=scheduled_at)
        config = SimpleNamespace(**vars(config))
        assert retry.claim_retry(config, now=scheduled_at + BACKOFF)
    assert not retry.schedule_retry(config, now=NOW + BACKOFF * 3)
    assert not retry.retry_due(config, now=NOW + BACKOFF * 4)


def test_clearing_a_scheduled_retry_does_not_restore_its_allowance(config):
    observe(config)
    for number in range(3):
        assert retry.schedule_retry(config, now=NOW + number)
        retry.clear_retry(config)
        assert not retry.retry_due(config, now=NOW + BACKOFF)
    assert not retry.schedule_retry(config, now=NOW + 3)


@pytest.mark.parametrize("guard", ["disabled", "reserve", "below_reserve", "unknown", "pending", "blocked", "stale"])
def test_guards_prevent_scheduling_and_claiming(config, guard):
    observe(config)
    assert retry.schedule_retry(config, now=NOW)
    if guard == "disabled":
        config.tactical_battles_enabled_in_daily = False
    elif guard in {"reserve", "below_reserve"}:
        observe(config, tickets=1 if guard == "reserve" else 0)
    elif guard == "pending":
        history.begin_battle(config, DAY, "opponent-a", 4, 571,
                             now=datetime.fromtimestamp(NOW, timezone.utc))
    else:
        state = history.read_state(config)
        if guard == "unknown":
            state["last_tickets"] = None
        elif guard == "blocked":
            state["blocked_reason"] = "A result needs inspection"
        elif guard == "stale":
            state["day_key"] = "2026-09-25"
        history.write_state(config, state)
    before = history.state_path(config).read_bytes()
    assert not retry.retry_due(config, now=NOW + BACKOFF)
    assert not retry.claim_retry(config, now=NOW + BACKOFF)
    assert not retry.schedule_retry(config, now=NOW + BACKOFF)
    assert history.state_path(config).read_bytes() == before


def test_proven_but_unreconciled_battle_also_blocks_retry(config):
    observe(config)
    intent = history.begin_battle(config, DAY, "opponent-a", 4, 571,
                                  now=datetime.fromtimestamp(NOW, timezone.utc))
    history.record_outcome(config, intent, won=False, evidence="loss.png",
                           now=datetime.fromtimestamp(NOW, timezone.utc))
    assert not retry.schedule_retry(config, now=NOW)


def test_new_day_needs_fresh_history_before_it_gets_a_new_allowance(config):
    observe(config)
    for number in range(3):
        assert retry.schedule_retry(config, now=NOW + number)
        retry.clear_retry(config)
    tomorrow = NOW + 86400
    assert not retry.schedule_retry(config, now=tomorrow)
    assert not retry.retry_due(config, now=tomorrow)
    observe(config, day="2026-09-27", now=tomorrow, tickets=5)
    assert retry.schedule_retry(config, now=tomorrow)
    assert retry.claim_retry(config, now=tomorrow + BACKOFF)


def test_old_due_retry_is_never_claimed_after_reset(config):
    observe(config)
    assert retry.schedule_retry(config, now=NOW)
    tomorrow = NOW + 86400
    observe(config, day="2026-09-27", now=tomorrow, tickets=5)
    assert not retry.retry_due(config, now=tomorrow)
    assert not retry.claim_retry(config, now=tomorrow)
    assert retry.schedule_retry(config, now=tomorrow)
    assert not retry.retry_due(config, now=tomorrow + BACKOFF - 1)


@pytest.mark.parametrize("raw", ["not json", "[]", "null", "{}", '{"version": 99}'])
def test_invalid_retry_file_cannot_reset_the_budget(config, raw):
    observe(config)
    path = retry.retry_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw)
    assert not retry.retry_due(config, now=NOW)
    assert not retry.claim_retry(config, now=NOW)
    assert not retry.schedule_retry(config, now=NOW)
    retry.clear_retry(config)
    assert path.read_text() == raw


def test_corrupt_battle_history_does_not_allow_retry(config):
    history.state_path(config).write_text("not json")
    assert not retry.schedule_retry(config, now=NOW)
    assert not retry.retry_due(config, now=NOW + BACKOFF)
    assert not retry.claim_retry(config, now=NOW + BACKOFF)


def test_retry_state_is_separate_for_each_instance_and_game(config):
    observe(config)
    assert retry.schedule_retry(config, now=NOW)
    for changed in ({"serial": "127.0.0.1:5555"}, {"package": "another.game"}):
        other = SimpleNamespace(**(vars(config) | changed))
        assert retry.retry_path(other) != retry.retry_path(config)
        observe(other)
        assert not retry.retry_due(other, now=NOW + BACKOFF)
        assert retry.schedule_retry(other, now=NOW + BACKOFF)
        assert not retry.retry_due(other, now=NOW + BACKOFF)
    assert retry.retry_path(config) != history.state_path(config)
    assert retry.retry_due(config, now=NOW + BACKOFF)


@pytest.mark.parametrize("change", [
    {"attempts": 0}, {"attempts": 4}, {"attempts": True}, {"version": True},
    {"due_at": NOW}, {"due_at": float("nan")}, {"updated_at": float("inf")},
    {"day_key": "2026-09-25"},
])
def test_malformed_budget_fields_fail_closed(config, change):
    observe(config)
    retry.schedule_retry(config, now=NOW)
    path = retry.retry_path(config)
    value = json.loads(path.read_text())
    path.write_text(json.dumps(value | change))
    before = path.read_bytes()
    assert not retry.schedule_retry(config, now=NOW + BACKOFF)
    assert not retry.retry_due(config, now=NOW + BACKOFF)
    assert not retry.claim_retry(config, now=NOW + BACKOFF)
    assert path.read_bytes() == before


def test_clock_rollback_cannot_reset_budget_or_claim_future_work(config):
    observe(config)
    assert retry.schedule_retry(config, now=NOW)
    assert not retry.schedule_retry(config, now=NOW - 1)
    assert not retry.retry_due(config, now=NOW - 1)
    assert not retry.claim_retry(config, now=NOW - 1)
    assert retry.claim_retry(config, now=NOW + BACKOFF)


@pytest.mark.parametrize("kind", ["symlink", "directory", "oversized"])
def test_budget_storage_rejects_unsafe_or_unbounded_files(config, kind):
    observe(config)
    path = retry.retry_path(config)
    if kind == "symlink":
        target = config.state_dir / "unrelated.json"
        target.write_text("keep this")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b" " * 4097)
    assert not retry.schedule_retry(config, now=NOW)
    assert not retry.retry_due(config, now=NOW + BACKOFF)
    assert not retry.claim_retry(config, now=NOW + BACKOFF)
    retry.clear_retry(config)
    if kind == "symlink":
        assert target.read_text() == "keep this"
