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


def test_campaign_reentry_accepts_active_practice_menu(tmp_path):
    r = runner(tmp_path)
    campaign = frame('campaign', target=(1029, 458))
    menu = frame('menu', tickets=3, period='season', active=True, mock=True)
    r.wait = Mock(side_effect=[campaign, menu])
    assert r.menu() is menu
    assert 'menu' in r.wait.call_args.args[0]
    r.tap.assert_called_once_with(campaign, campaign.screen.target, 'Open Joint Firing Drill')
    assert drill_state.read_state(r.config)['pending'] is None


def test_menu_waits_for_sliding_balance_panel_before_planning(tmp_path):
    r = runner(tmp_path)
    moving = frame('menu', tickets=None, period=None)
    settled = frame('menu', tickets=0, period='season')
    r.wait = Mock(side_effect=[moving, settled])
    assert r.menu() is settled
    predicate = r.wait.call_args.kwargs['predicate']
    assert not predicate(moving.screen)
    assert predicate(settled.screen)  # Zero is a valid observed balance.
    r.tap.assert_not_called()


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


@pytest.mark.parametrize('remaining', [1, 2, 3])
def test_partial_free_mock_uses_observed_progress_not_old_proof_count(tmp_path, remaining):
    from dataclasses import replace
    from ba_automator.drill_policy import fingerprint
    r = runner(tmp_path)
    for i in (1, 2, 3):
        setattr(r.config, f'drill_stage_{i}', 1)
    r.config.drill_comfort_seconds = 30
    teams = [tuple(replace(m, student_id=f'unit-{unit}-{m.slot}', assistant=False,
                           assistant_id=None) for m in qualified_team()) for unit in range(3)]
    keys = [fingerprint(team, 1, 'empty-five-auto') for team in teams]
    drill_state.save_plan(r.config, 'season', teams, [1]*3, ['empty-five-auto']*3)
    # All three old-room wins remain saved; the current room may be new or
    # partially complete. Only its observed completed prefix may be skipped.
    for key in keys:
        drill_state.begin_practice(r.config, 'day|season', key)
        drill_state.record_practice(r.config, key, True, 72, 30)
    menu = frame('menu', period='season', tickets=3, active=True, mock=True)
    r.open_mock = Mock(return_value=menu)
    r.stage = Mock(return_value=frame('detail', remaining_rounds=remaining))
    def wait(kind, predicate):
        assert kind == 'menu' and predicate(menu.screen)
        assert not predicate(frame('menu', period='season', tickets=2,
                                   active=True, mock=True).screen)
        assert not predicate(frame('menu', period='season', tickets=3,
                                   active=True, mock=False).screen)
        return menu
    r.wait = Mock(side_effect=wait)
    r.open_unit = Mock(return_value=frame('formation'))
    completed = 3 - remaining
    r.practice_round = Mock(side_effect=[(menu, teams[i], keys[i], True)
                                        for i in range(completed, 3)])
    r.journal = Mock()
    _, observed, stages, actual = r.qualify_plan(menu, 'day|season')
    assert observed == teams and actual == keys and stages == [1]*3
    assert [call.args[1] for call in r.open_unit.call_args_list] == list(range(completed, 3))
    assert [call.kwargs['remaining_rounds'] for call in r.open_unit.call_args_list] == list(range(remaining, 0, -1))
    assert r.practice_round.call_count == remaining


@pytest.mark.parametrize('remaining', [None, True, 0, 4, 2, 1])
def test_active_mock_unreadable_or_unproven_progress_never_mobilizes(tmp_path, remaining):
    from dataclasses import replace
    r = runner(tmp_path)
    for i in (1, 2, 3):
        setattr(r.config, f'drill_stage_{i}', 1)
    r.config.drill_comfort_seconds = 30
    teams = [tuple(replace(m, student_id=f'unit-{unit}-{m.slot}', assistant=False,
                           assistant_id=None) for m in qualified_team()) for unit in range(3)]
    drill_state.save_plan(r.config, 'season', teams, [1]*3, ['empty-five-auto']*3)
    drill_state.for_context(r.config, 'day|season')
    menu = frame('menu', period='season', active=True, mock=True)
    r.open_mock = Mock(return_value=menu)
    r.stage = Mock(return_value=frame('detail', remaining_rounds=remaining))
    r.open_unit = Mock()
    r.practice_round = Mock()
    with pytest.raises(RuntimeError, match='held'):
        r.qualify_plan(menu, 'day|season')
    r.open_unit.assert_not_called()
    r.practice_round.assert_not_called()
    r.tap.assert_not_called()


@pytest.mark.parametrize('remaining', [None, 1, 3])
def test_partial_mock_round_mismatch_holds_before_formation_input(tmp_path, remaining):
    r = runner(tmp_path)
    r.stage = Mock(return_value=frame('detail', remaining_rounds=remaining))
    with pytest.raises(RuntimeError, match='held'):
        r.open_unit(frame('menu'), 1, 1, remaining_rounds=2)
    r.tap.assert_not_called()


@pytest.mark.parametrize('paid,active,mock,pending,allowed', [
    (False, True, True, False, True), (True, True, False, True, False),
    (False, True, False, False, False), (False, False, False, False, False),
    (False, True, True, True, False),
])
def test_only_unpaid_active_mock_can_request_assistant_replanning(tmp_path, paid, active, mock, pending, allowed):
    r = runner(tmp_path)
    if pending:
        for key in ('a'*64, 'b'*64, 'c'*64):
            drill_state.begin_practice(r.config, 'day|season', key)
            drill_state.record_practice(r.config, key, True, 72, 30)
        drill_state.begin_spend(r.config, 'day|season', 3, 1, kind='entry', fingerprints=['a'*64, 'b'*64, 'c'*64])
    formation = frame('formation')
    r.stage = Mock(return_value=frame('detail', target=(1,2), remaining_rounds=3))
    r.wait = Mock(return_value=formation)
    r.sleep = Mock()
    r.verify_real_assistant = Mock(return_value=formation)
    r.open_unit(frame('menu', active=active, mock=mock), 0, 2, qualified_team(), paid=paid)
    assert r.verify_real_assistant.call_args.kwargs['allow_free_replan'] is allowed


def test_changed_assistant_is_saved_before_fresh_mock_and_cannot_reuse_old_proof(tmp_path):
    from dataclasses import replace
    from ba_automator.drill_policy import fingerprint
    r = runner(tmp_path)
    r.config.drill_comfort_seconds = 30
    for i in (1, 2, 3):
        setattr(r.config, f'drill_stage_{i}', 1)
    teams = [qualified_team()] + [tuple(replace(m, student_id=f'unit-{unit}-{m.slot}',
               assistant=False, assistant_id=None) for m in qualified_team()) for unit in (1,2)]
    old_key = fingerprint(teams[0], 1, 'empty-five-auto')
    drill_state.save_plan(r.config, 'season', teams, [1]*3, ['empty-five-auto']*3)
    drill_state.begin_practice(r.config, 'day|season', old_key)
    drill_state.record_practice(r.config, old_key, True, 72, 30)
    menu = frame('menu', period='season', tickets=3, active=True, mock=True)
    r.open_mock = Mock(return_value=menu)
    r.stage = Mock(return_value=frame('detail', remaining_rounds=3))
    r.wait = Mock(return_value=menu)
    replacement = replace(teams[0][0], assistant_id='fresh-offering')
    def open_unit(*args, **kwargs):
        r.assistant = replacement if args[1] == 0 else None
        return frame('formation')
    r.open_unit = Mock(side_effect=open_unit)
    tested = []
    def practice(formation, stage, context, verified_team):
        key = fingerprint(verified_team, stage, 'empty-five-auto')
        state = drill_state.read_state(r.config)
        if not tested:
            assert key != old_key
            assert state['plan']['teams'][0][0]['assistant_id'] == 'fresh-offering'
            assert key not in state['proofs']
            assert not drill_state.qualified(r.config, state['plan']['fingerprints'], 30)
        assert state['pending'] is None
        drill_state.begin_practice(r.config, context, key)
        drill_state.record_practice(r.config, key, True, 72, 30)
        tested.append(key)
        return menu, verified_team, key, True
    r.practice_round = Mock(side_effect=practice)
    r.journal = Mock()
    _, actual_teams, _, keys = r.qualify_plan(menu, 'day|season')
    assert actual_teams[0][0] == replacement and keys == tested
    assert drill_state.qualified(r.config, keys, 30)
    assert old_key not in keys
    assert drill_state.read_state(r.config)['pending'] is None

@pytest.mark.parametrize('panel', ['quick', 'formation'])
def test_resume_paid_entry_from_interrupted_formation(tmp_path, panel):
    r = runner(tmp_path)
    team = qualified_team()
    state = {'pending': {'fingerprints': ['proof'], 'before': 3},
             'plan': {'fingerprints': ['proof'], 'teams': [[vars(m) for m in team]],
                      'stages': [2], 'period': 'season'}}
    from unittest.mock import patch
    menu = frame('menu', active=True, mock=False, period='season', tickets=2)
    screens = [frame(panel)] + ([frame('formation')] if panel == 'quick' else []) + [menu]
    r.wait = Mock(side_effect=screens)
    settlement = frame('settlement')
    r.paid_rounds = Mock(return_value=settlement)
    r.settle_entry = Mock(return_value='done')
    with patch.object(drill_state, 'read_state', return_value=state):
        assert r.resume_entry(state) == 'done'
    assert r.tap.call_count == (2 if panel == 'quick' else 1)
    r.paid_rounds.assert_called_once()


def test_resume_does_not_replay_mobilized_round_from_formation(tmp_path):
    r = runner(tmp_path)
    state = {'pending': {'fingerprints': ['proof'], 'battle': {'index': 0}},
             'plan': {'fingerprints': ['proof'], 'teams': [], 'stages': [], 'period': 'season'}}
    r.wait = Mock(return_value=frame('formation'))
    with pytest.raises(RuntimeError, match='held'):
        r.resume_entry(state)
    r.tap.assert_not_called()
