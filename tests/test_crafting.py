"""Craft spending, receipt reconciliation, and durable timer guards without a device."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from ba_automator.config import Config, ConfigError
from ba_automator.crafting import CraftFrame, CraftingRunner
from ba_automator.crafting_state import CraftStateError, empty_state, read_state, scheduled_jobs, state_path, write_state
from ba_automator.crafting_vision import CraftScreen, CraftSlot
from ba_automator.runtime import Capture, TaskError
from ba_automator.locking import InstanceLock


class Clock:
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds
    def wall(self):
        return datetime(2026, 9, 24, 15, tzinfo=timezone.utc) + timedelta(seconds=self.now)


class Device:
    def __init__(self, package):
        self.foreground = package
        self.taps = []
        self.size = (1280, 720)
        self.accept = True
    def connect(self): pass
    def verify_package(self): pass
    def display_size(self): return self.size
    def screenshot(self): return b'offline screenshot'
    def foreground_package(self): return self.foreground
    def tap(self, x, y, *, deadline, monotonic):
        if not self.accept or monotonic() > deadline:
            return False
        self.taps.append((x, y))
        return True


def listing(*kinds):
    return CraftScreen('list', slots=tuple(CraftSlot(n, k, 60 * n if k == 'running' else None)
                                          for n, k in enumerate(kinds, 1)),
                       target=(890, 618), collect_target=(1120, 618))


def quick(quantity=1, owned=3, unit=1):
    return CraftScreen('quick', quantity=quantity, owned=owned, required=quantity * unit, credits=quantity * 2000)


@pytest.fixture
def harness(tmp_path):
    config = Config(serial='127.0.0.1:5695', package='com.nexon.bluearchive',
                    run_dir=tmp_path/'runs', state_dir=tmp_path/'state', lock_dir=tmp_path/'locks')
    clock, screens = Clock(), [CraftScreen('unknown')]
    device = Device(config.package)
    def analyze(png):
        return screens.pop(0) if len(screens) > 1 else screens[0]
    runner = CraftingRunner(config, device, None, crafting_vision=SimpleNamespace(analyze=analyze),
                            monotonic=clock.monotonic, sleep=clock.sleep, wall_clock=clock.wall)
    runner.state = empty_state()
    def frame(screen):
        return CraftFrame(Capture(device.screenshot(), clock.now, device.foreground), screen, clock.wall())
    yield SimpleNamespace(config=config, clock=clock, screens=screens, device=device, runner=runner, frame=frame)
    if not runner.journal.stream.closed:
        runner.journal.close()


def actions(config):
    path = config.state_dir/'important-actions.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_maximum_batch_is_checked_before_spending_and_timers_are_saved(harness):
    h = harness
    h.screens[:] = [quick(), quick(2), quick(3), CraftScreen('confirm', quantity=3, target=(768, 504)), listing('running','running','running')]
    assert h.runner.fill(h.frame(listing('empty','empty','empty'))) == 'success'
    assert h.device.taps == [(890,618),(1117,512),(1117,512),(1114,589),(768,504)]
    state = read_state(h.config)
    assert len(state['slots']) == 3 and state['pending_action'] is None
    assert [a['action'] for a in actions(h.config)] == ['craft_start_requested','crafts_started']
    assert all(job['task'] == 'crafting' for job in scheduled_jobs(state))


def test_only_vacant_slots_are_filled_and_low_inventory_limits_batch(harness):
    h = harness
    h.screens[:] = [quick(owned=1), CraftScreen('confirm', quantity=1, target=(768,504)), listing('running','running','empty')]
    h.runner.fill(h.frame(listing('running','empty','empty')))
    assert actions(h.config)[-1]['count'] == 1


@pytest.mark.parametrize('initial_quantity', [1, 3])
def test_two_keys_with_three_vacant_slots_selects_only_two_crafts(harness, initial_quantity):
    h = harness
    h.screens[:] = [quick(initial_quantity, owned=2)]
    if initial_quantity != 1:
        h.screens.append(quick(owned=2))
    h.screens.extend([quick(2, owned=2), CraftScreen('confirm', quantity=2, target=(768,504)),
                      listing('running','running','empty')])
    assert h.runner.fill(h.frame(listing('empty','empty','empty'))) == 'success'
    expected = [(890,618)]
    if initial_quantity != 1:
        expected.append((836,512))
    expected.extend([(1117,512),(1114,589),(768,504)])
    assert h.device.taps == expected
    assert actions(h.config)[-1]['count'] == 2
    assert [slot['slot'] for slot in read_state(h.config)['slots']] == [1, 2]
    assert read_state(h.config)['pending_action'] is None


def test_unaffordable_jump_to_three_never_opens_confirmation(harness):
    h = harness
    h.screens[:] = [quick(owned=2), quick(3, owned=2)]
    with pytest.raises(TaskError, match='unexpected batch quantity'):
        h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert h.device.taps == [(890,618),(1117,512)]
    assert not actions(h.config)
    assert h.runner.state['pending_action'] is None


def test_unacknowledged_minimum_never_advances_or_spends(harness):
    h = harness
    h.screens[:] = [quick(3, owned=2)]
    with pytest.raises(TaskError, match='expected quick'):
        h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert h.device.taps == [(890,618),(836,512)]
    assert not actions(h.config)


def test_multi_keystone_preset_uses_affordable_whole_crafts(harness):
    h = harness
    h.screens[:] = [quick(owned=5, unit=2), quick(2, owned=5, unit=2),
                    CraftScreen('confirm', quantity=2, target=(768,504)),
                    listing('running','running','empty')]
    h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert actions(h.config)[-1]['count'] == 2
    assert h.device.taps == [(890,618),(1117,512),(1114,589),(768,504)]


def test_zero_keys_sends_no_craft_or_confirmation_input(harness):
    h = harness
    h.screens[:] = [quick(owned=0), listing('empty','empty','empty')]
    h.runner.observe_slots(h.frame(listing('empty','empty','empty')))
    h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert h.device.taps == [(890,618),(1206,102)]
    assert not actions(h.config)
    assert read_state(h.config)['next_check_at'] is not None


def test_full_slots_need_no_quick_craft_input(harness):
    h = harness
    assert h.runner.fill(h.frame(listing('running','running','running'))) == 'success'
    assert not h.device.taps


@pytest.mark.parametrize('changed', [replace(quick(2), owned=2), replace(quick(2), required=6), replace(quick(2), credits=9999)])
def test_cost_changes_stop_before_craft_confirmation(harness, changed):
    h = harness
    h.screens[:] = [quick(), changed]
    with pytest.raises(TaskError, match='cost changed'):
        h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert (1114,589) not in h.device.taps and (768,504) not in h.device.taps
    assert not actions(h.config)


@pytest.mark.parametrize('step', ['minimum', 'second_increment'])
def test_cost_and_inventory_are_rechecked_at_every_quantity_step(harness, step):
    h = harness
    if step == 'minimum':
        h.screens[:] = [quick(3), replace(quick(), owned=2)]
        expected = [(890,618),(836,512)]
    else:
        h.screens[:] = [quick(), quick(2), replace(quick(3), credits=9999)]
        expected = [(890,618),(1117,512),(1117,512)]
    with pytest.raises(TaskError, match='cost changed'):
        h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert h.device.taps == expected
    assert not actions(h.config)


def test_wrong_confirmation_count_never_spends(harness):
    h = harness
    h.screens[:] = [quick(), quick(2), quick(3), CraftScreen('confirm', quantity=2, target=(768,504))]
    with pytest.raises(TaskError, match='expected confirm'):
        h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert (768,504) not in h.device.taps


def test_uncertain_spend_is_retained_and_not_replayed(harness):
    h = harness
    h.screens[:] = [quick(),quick(2),quick(3),CraftScreen('confirm',quantity=3,target=(768,504)),listing('empty','empty','empty')]
    with pytest.raises(TaskError):
        h.runner.fill(h.frame(listing('empty','empty','empty')))
    assert read_state(h.config)['pending_action']['kind'] == 'start'
    before = list(h.device.taps)
    with pytest.raises(TaskError, match='uncertain'):
        h.runner.reconcile(h.frame(listing('empty','empty','empty')))
    assert h.device.taps == before
    h.runner.reconcile(h.frame(listing('running','running','running')))
    assert read_state(h.config)['pending_action'] is None
    assert actions(h.config)[-1]['status'] == 'reconciled'


def test_receipt_and_new_empty_slots_are_required_for_collection(harness):
    h = harness
    h.screens[:] = [CraftScreen('receipt',target=(1110,660)),listing('empty','running','empty')]
    h.runner.collect(h.frame(listing('ready','running','ready')))
    assert h.device.taps == [(1120,618),(1110,660)]
    assert actions(h.config)[-1]['action'] == 'crafts_collected'
    assert actions(h.config)[-1]['count'] == 2
    assert [slot['slot'] for slot in read_state(h.config)['slots']] == [2]


def test_collection_budget_includes_tooltip_inspection_and_slot_verification(harness, monkeypatch):
    from ba_automator import crafting

    h = harness
    h.clock.sleep(200)
    h.screens[:] = [CraftScreen('receipt', target=(1110, 660)), listing('empty', 'empty', 'empty')]

    def inspect(runner, receipt, evidence):
        assert runner.task == 'crafting'
        assert read_state(h.config)['pending_action']['slots'] == [1, 2, 3]
        h.clock.sleep(240)
        return h.frame(receipt.screen)

    monkeypatch.setattr(crafting, 'inspect_receipt', inspect)
    h.runner.collect(h.frame(listing('ready', 'ready', 'ready')))
    assert h.device.taps == [(1120, 618), (1110, 660)]
    assert read_state(h.config)['pending_action'] is None
    assert actions(h.config)[-1]['action'] == 'crafts_collected'
    assert actions(h.config)[-1]['count'] == 3


@pytest.mark.parametrize('limit', ['time', 'inputs'])
def test_extended_collection_budget_remains_bounded(harness, limit):
    from ba_automator import crafting

    h = harness
    if limit == 'time':
        h.clock.sleep(crafting.TIMEOUT)
    else:
        h.runner.actions = 200
    with pytest.raises(TaskError, match='time or input limit'):
        h.runner.capture()
    assert not h.device.taps


def test_missing_receipt_stops_without_claiming_collection_or_starting_more(harness):
    h = harness
    h.screens[:] = [listing('empty','running','running')]
    with pytest.raises(TaskError, match='expected receipt'):
        h.runner.collect(h.frame(listing('ready','running','running')))
    assert [a['action'] for a in actions(h.config)] == ['craft_collection_requested']
    assert read_state(h.config)['pending_action']['kind'] == 'collect'
    with pytest.raises(TaskError, match='uncertain receipt'):
        h.runner.reconcile(h.frame(listing('empty','running','running')))


def test_disabled_quick_craft_persists_reason_and_never_spends(harness):
    h = harness
    h.screens[:] = [CraftScreen('disabled',target=(1206,102),reason='Node 1 is not configured'),listing('empty','empty','empty'),CraftScreen('home')]
    assert h.runner.fill(h.frame(listing('empty','empty','empty'))) == 'disabled'
    state = read_state(h.config)
    assert state['disabled_reason'] == 'Node 1 is not configured'
    assert scheduled_jobs(state) == []
    assert (1114,589) not in h.device.taps
    assert actions(h.config)[-1]['action'] == 'crafting_disabled'


@pytest.mark.parametrize('fault', ['stale','foreground','preflight'])
def test_input_requires_fresh_frame_and_selected_foreground(harness, fault):
    h = harness
    frame = h.frame(CraftScreen('home'))
    if fault == 'stale': h.clock.sleep(6)
    elif fault == 'foreground': h.device.foreground = 'com.android.settings'
    else: h.device.accept = False
    with pytest.raises(TaskError):
        h.runner.tap(frame,(660,658),'test')
    assert not h.device.taps


def test_navigation_retries_only_a_verified_source(harness):
    h = harness
    h.screens[:] = [CraftScreen('home'),CraftScreen('home'),listing('empty','empty','empty')]
    assert h.runner.navigate('home','list',(660,658)).screen.kind == 'list'
    assert h.device.taps == [(660,658),(660,658)]


def test_lock_and_wrong_resolution_block_input(harness):
    h = harness
    with InstanceLock(h.config), pytest.raises(RuntimeError, match='lock'):
        h.runner.run()
    assert not h.device.taps


def test_state_is_scoped_to_instance_and_survives_reloading(harness):
    h = harness
    h.runner.observe_slots(h.frame(listing('running','empty','running')))
    state = read_state(h.config)
    assert state == read_state(h.config)
    assert len(state['slots']) == 2
    other = replace(h.config,serial='127.0.0.1:5675')
    assert state_path(h.config) != state_path(other)
    assert read_state(other) == empty_state()


@pytest.mark.parametrize('contents', ['garbage','{}','{"version":1,"slots":[{"slot":1,"due_at":"tomorrow"}]}'])
def test_corrupt_state_is_not_treated_as_an_empty_crafting_queue(harness, contents):
    h = harness
    h.config.state_dir.mkdir()
    state_path(h.config).write_text(contents)
    with pytest.raises(CraftStateError): read_state(h.config)
    assert not h.device.taps


@pytest.mark.parametrize('bad',[1,'true',None])
def test_crafting_setting_requires_a_boolean(bad):
    with pytest.raises(ConfigError):
        Config(serial='127.0.0.1:5695',package='com.nexon.bluearchive',crafting_schedule_enabled=bad)
