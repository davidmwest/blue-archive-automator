"""Attention is reserved for a concrete intervention, not every failed attempt."""
from types import SimpleNamespace

import pytest

from ba_automator import failure_notices, crafting_state, ticket_state


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(state_dir=tmp_path, serial='test', package='game.test')


@pytest.mark.parametrize('detail', [
    'spend_ap did not reach event_quick; inspect the local trace',
    'Reward receipt changed or capture expired before inspection input',
    'Cafe visiting-student notice remained after three dismissal attempts',
    'Drill season or ticket balance is unreadable',
])
def test_recognition_failure_without_hold_does_not_require_user_action(config, detail):
    assert failure_notices.required_actions(config, 'daily', detail, {}) == []


def test_actual_pending_spend_is_actionable_even_with_generic_error(config):
    state = ticket_state.empty_state()
    state.update(day='2026-10-01', total=15, quotas=[5, 5, 5])
    state['pending'] = dict(area='Trinity', stage='B', count=5, tickets_before=15,
                            ap_before=427, ap_cost=0, run_dir='saved-run')
    ticket_state.write_state(config, 'scrimmages', state)
    actions = failure_notices.required_actions(config, 'daily', 'unreadable', {})
    assert len(actions) == 1 and actions[0]['task'] == 'scrimmages'
    assert 'receipt' in actions[0]['action']
    assert ticket_state.read_state(config, 'scrimmages')['pending'] == state['pending']


def test_disabled_preset_and_exhausted_retries_have_specific_actions(config):
    state = crafting_state.empty_state()
    state['disabled_reason'] = 'Quick Craft preset requires unsupported materials'
    crafting_state.write_state(config, state)
    actions = failure_notices.required_actions(config, 'daily', '', {'cafe': {'retry_paused': True}})
    assert {a['task'] for a in actions} == {'crafting', 'cafe'}
    assert 'preset' in actions[0]['action']
    assert 'Do everything' in actions[1]['action']


@pytest.mark.parametrize('detail', [
    'Account sign-in needs attention',
    'Google Play needs attention',
    'saved schedule could not be updated',
    'Download requires approval because auto_download is disabled',
    'Configured Blue Archive package is not installed: game.test',
    'Shared ADB server protocol differs; use the same ADB executable as the Azur Lane daemon',
    'Expected a 16:9 landscape game display from 1280×720 to 3840×2160',
])
def test_external_prerequisite_is_actionable(config, detail):
    actions = failure_notices.required_actions(config, 'restart', detail, {})
    assert len(actions) == 1 and actions[0]['reason'] == 'external'


def test_corrupt_transaction_state_is_not_silently_hidden(config):
    ticket_state.state_path(config, 'scrimmages').write_text('{bad json')
    actions = failure_notices.required_actions(config, 'scrimmages', '', {})
    assert len(actions) == 1 and 'repair' in actions[0]['action']
