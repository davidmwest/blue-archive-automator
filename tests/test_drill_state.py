from types import SimpleNamespace
import pytest
from ba_automator import drill_state as state

@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(serial='test', package='test.game', state_dir=tmp_path)

def qualify(config):
    keys = [str(i)*64 for i in range(3)]
    for key in keys:
        state.begin_practice(config, 'day|season', key)
        state.record_practice(config, key, True, 60, 30)
    return keys

def test_failed_practice_survives_restart_and_cannot_be_retried(config):
    key = 'a'*64
    state.begin_practice(config, 'day|season', key)
    state.record_practice(config, key, False, 0, 30)
    assert state.read_state(config)['proofs'] == {}
    with pytest.raises(state.DrillStateError, match='already attempted'):
        state.begin_practice(config, 'day|season', key)

def test_all_three_distinct_proofs_required(config):
    keys = qualify(config)
    with pytest.raises(state.DrillStateError):
        state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=[keys[0]]*3)
    assert state.read_state(config)['pending'] is None

def test_pending_entry_blocks_restart_rollover_and_double_spending(config):
    keys = qualify(config)
    state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=keys)
    for operation in (lambda: state.ensure_restart_safe(config),
                      lambda: state.for_context(config, 'tomorrow|season'),
                      lambda: state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=keys)):
        with pytest.raises(state.DrillStateError): operation()
    assert state.read_state(config)['pending']['before'] == 3

def test_settlement_requires_sequential_rounds_receipt_and_ticket_delta(config):
    keys = qualify(config)
    state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=keys)
    with pytest.raises(state.DrillStateError):
        state.record_round(config, completed_rounds=2, score=100, evidence='result.png')
    with pytest.raises(state.DrillStateError):
        state.finish_spend(config, 2, receipt_verified=True)
    for n in (1,2,3): state.record_round(config, completed_rounds=n, score=100, evidence=f'{n}.png')
    for balance, receipt in ((3,True), (2,False)):
        with pytest.raises(state.DrillStateError): state.finish_spend(config, balance, receipt_verified=receipt)
    state.finish_spend(config, 2, receipt_verified=True)
    assert state.read_state(config)['pending'] is None

def test_new_day_invalidates_mock_proofs(config):
    qualify(config)
    assert state.for_context(config, 'tomorrow|season')['proofs'] == {}


def test_round_checkpoint_survives_restart_and_prevents_duplicate_mobilization(config):
    keys = qualify(config)
    state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=keys)
    state.begin_round(config, 0, 2, keys[0])
    state.observe_auto(config)
    assert state.read_state(config)['pending']['battle']['auto_verified']
    with pytest.raises(state.DrillStateError):
        state.begin_round(config, 0, 2, keys[0])
    state.record_round(config, completed_rounds=1, score=12345, evidence='round.png')
    pending = state.read_state(config)['pending']
    assert 'battle' not in pending and pending['result_unacknowledged']
    state.acknowledge_round(config)
    assert 'result_unacknowledged' not in state.read_state(config)['pending']


def test_corrupt_battle_checkpoint_is_rejected(config):
    keys = qualify(config)
    state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=keys)
    saved = state.read_state(config)
    saved['pending']['battle'] = dict(index=3, stage=2, fingerprint=keys[0], auto_verified=True)
    with pytest.raises(ValueError, match='battle checkpoint'):
        state.write_state(config, saved)


def test_receipt_checkpoint_survives_restart(config):
    state.begin_spend(config, 'day|season', 2, 2, kind='sweep')
    state.record_receipt(config, 'reward.png')
    assert state.read_state(config)['pending']['receipt'] == 'reward.png'
    state.finish_spend(config, 0, receipt_verified=True)
    assert state.read_state(config)['pending'] is None


def test_daily_resumes_pending_drill_before_restart_even_when_disabled(config):
    from ba_automator.config import Config
    from ba_automator.tasks import task_plan
    cfg = Config(serial='127.0.0.1:5695', package='com.nexon.bluearchive', state_dir=config.state_dir)
    state.begin_spend(cfg, 'day|season', 2, 2, kind='sweep')
    plan = task_plan('daily', cfg)
    assert plan[:2] == ('joint_firing_drill', 'restart')
    assert plan.count('joint_firing_drill') == 1


def test_expired_free_room_can_requalify_successful_round(config):
    keys = qualify(config)
    state.begin_practice(config, 'day|season', keys[0])
    saved = state.read_state(config)
    assert saved['attempts'] == keys + [keys[0]]
    assert keys[0] not in saved['proofs']
    assert not state.qualified(config, keys, 30)
    with pytest.raises(state.DrillStateError, match='practice victories'):
        state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=keys)
    state.record_practice(config, keys[0], True, 50, 30)
    assert state.qualified(config, keys, 30)


def test_free_requalification_unknown_outcome_is_not_replayed(config):
    keys = qualify(config)
    state.begin_practice(config, 'day|season', keys[0])
    with pytest.raises(state.DrillStateError, match='already attempted'):
        state.begin_practice(config, 'day|season', keys[0])
    assert len(state.read_state(config)['attempts']) == 4


def test_successful_free_replays_still_obey_daily_battle_limit(config):
    keys = qualify(config)
    for key in keys:
        state.begin_practice(config, 'day|season', key)
        state.record_practice(config, key, True, 60, 30)
    with pytest.raises(state.DrillStateError, match='six'):
        state.begin_practice(config, 'day|season', keys[0])
    assert len(state.read_state(config)['attempts']) == 6
    assert state.qualified(config, keys, 30)


def test_paid_uncertainty_prevents_even_proven_free_requalification(config):
    keys = qualify(config)
    state.begin_spend(config, 'day|season', 3, 1, kind='entry', fingerprints=keys)
    before = state.read_state(config)
    with pytest.raises(state.DrillStateError, match='paid Drill entry'):
        state.begin_practice(config, 'day|season', keys[0])
    assert state.read_state(config) == before
