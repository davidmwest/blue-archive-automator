"""Ticket recovery invariants without device input."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from ba_automator import drill_state
from ba_automator.joint_firing_drill import DrillRunner


def runner(tmp_path):
    r = object.__new__(DrillRunner)
    r.config = SimpleNamespace(serial='test', package='game.test', state_dir=tmp_path)
    r.tap = Mock()
    r.important = Mock()
    r.fail = Mock(side_effect=RuntimeError('held'))
    return r


def frame(kind, **fields):
    return SimpleNamespace(screen=SimpleNamespace(kind=kind, **fields))


def test_recovery_never_repeats_a_sent_sweep_confirmation(tmp_path):
    r = runner(tmp_path)
    state = drill_state.begin_spend(r.config, 'day|season', 2, 2, kind='sweep')
    state['pending']['confirmation_sent'] = True
    drill_state.write_state(r.config, state)
    r.wait = Mock(return_value=frame('sweep_confirm', count=2))
    with pytest.raises(RuntimeError, match='held'):
        r.resume_sweep(state)
    r.tap.assert_not_called()


def test_confirm_saves_intent_before_input_and_verifies_receipt_delta(tmp_path):
    r = runner(tmp_path)
    state = drill_state.begin_spend(r.config, 'day|season', 3, 2, kind='sweep')
    r.wait = Mock(side_effect=[frame('sweep_confirm', count=2, target=(770,505)), frame('receipt')])
    def tap(*args):
        assert drill_state.read_state(r.config)['pending']['confirmation_sent']
    r.tap.side_effect = tap
    r.collect_receipt = Mock(return_value=frame('menu', tickets=1))
    assert r.resume_sweep(state).screen.tickets == 1
    assert drill_state.read_state(r.config)['pending'] is None


def test_ticket_delta_alone_does_not_resolve_sweep(tmp_path):
    r = runner(tmp_path)
    state = drill_state.begin_spend(r.config, 'day|season', 2, 2, kind='sweep')
    r.wait = Mock(return_value=frame('menu', tickets=0))
    with pytest.raises(RuntimeError, match='held'):
        r.resume_sweep(state)
    assert drill_state.read_state(r.config)['pending'] is not None
    r.tap.assert_not_called()


def test_receipted_sweep_recovers_from_empty_panel(tmp_path):
    r = runner(tmp_path)
    drill_state.begin_spend(r.config, 'day|season', 2, 2, kind='sweep')
    drill_state.record_receipt(r.config, tmp_path / 'receipt.png')
    r.wait = Mock(return_value=frame('sweep_empty'))
    r.menu = Mock(return_value=frame('menu', tickets=0))
    assert r.resume_sweep(drill_state.read_state(r.config)).screen.tickets == 0
    assert drill_state.read_state(r.config)['pending'] is None
    r.tap.assert_not_called()
