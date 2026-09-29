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


def qualified_team():
    from ba_automator.assault_policy import TeamMember
    return tuple(TeamMember(f"student-{i}", i, "striker" if i < 4 else "special",
                            "explosive", 3, 80, i == 0, "lender" if i == 0 else None)
                 for i in range(6))


@pytest.mark.parametrize('from_campaign', [False, True])
def test_expired_free_mock_is_dismissed_before_new_visit(tmp_path, from_campaign):
    r = runner(tmp_path)
    settlement = frame('mock_settlement', target=(640, 524))
    settlement.capture = SimpleNamespace(png=b'saved-screen')
    lobby = frame('lobby', target=(170, 440))
    menu = frame('menu', tickets=3, period='season')
    screens = [settlement, lobby, menu]
    if from_campaign:
        screens.insert(0, frame('campaign', target=(1029, 458)))
    r.wait = Mock(side_effect=screens)
    r.journal = Mock()
    assert r.menu() is menu
    assert r.tap.call_count == (3 if from_campaign else 2)
    assert drill_state.read_state(r.config)['pending'] is None
    r.journal.save_image.assert_called_once_with('expired-mock-settlement.png', b'saved-screen')


def test_expired_mock_never_discards_unresolved_paid_entry(tmp_path):
    r = runner(tmp_path)
    drill_state.begin_spend(r.config, 'day|season', 3, 2, kind='sweep')
    r.wait = Mock(return_value=frame('mock_settlement', target=(640, 524)))
    with pytest.raises(RuntimeError, match='held'):
        r.menu()
    r.tap.assert_not_called()
    assert drill_state.read_state(r.config)['pending'] is not None


def test_verified_assistant_identity_survives_mock_and_matches_paid_proof(tmp_path):
    from dataclasses import replace
    from ba_automator.drill_policy import fingerprint
    r = runner(tmp_path)
    r.config.drill_comfort_seconds = 30
    team = qualified_team()
    formation = frame('formation', team=tuple(replace(m, assistant=False, assistant_id=None) for m in team))
    r.observe_team = Mock(side_effect=AssertionError('already verified by open_unit'))
    r.starting_skills = Mock(return_value=(formation, 'empty-five-auto'))
    r.journal = Mock()
    result = frame('result', won=True, elapsed_seconds=90)
    r.battle = Mock(return_value=result)
    r.dismiss_result = Mock(return_value=frame('menu'))
    _, observed, key, ok = r.practice_round(formation, 2, 'day|season', verified_team=team)
    assert observed == team and ok
    assert key == fingerprint(team, 2, 'empty-five-auto')
    assert drill_state.read_state(r.config)['proofs'][key] == {'won': True, 'remaining': 90}


@pytest.mark.parametrize('changed_after_skills', [False, True])
def test_verified_team_change_stops_before_practice_or_spending(tmp_path, changed_after_skills):
    from dataclasses import replace
    r = runner(tmp_path)
    team = qualified_team()
    visible = tuple(replace(m, assistant=False, assistant_id=None) for m in team)
    original = frame('formation', team=visible)
    changed = frame('formation', team=(replace(visible[0], level=79), *visible[1:]))
    r.starting_skills = Mock(return_value=(changed, 'empty-five-auto'))
    r.battle = Mock()
    with pytest.raises(RuntimeError, match='held'):
        r.practice_round(original if changed_after_skills else changed, 2, 'day|season', verified_team=team)
    r.battle.assert_not_called()
    assert not drill_state.read_state(r.config)['attempts']
