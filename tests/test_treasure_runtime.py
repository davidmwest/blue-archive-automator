"""Treasure transaction boundaries, using real durable state without a device."""
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ba_automator import event_treasure, treasure_state
from ba_automator.treasure_policy import BoardUncertain
from ba_automator.runtime import Capture


CELLS = frozenset((r, c) for r in range(5) for c in range(9))
CELL = (2, 4)
PROFILE = {'id': 'test-event', 'target_round': 3, 'board': {'cell_cost': 200}}


def board(**values):
    fields = dict(kind='treasure_board', round=1, currency=200, inventory=(2, 5, 2),
                  selected=0, cost=0, remaining=45, closed=CELLS,
                  hits=frozenset(), empty=frozenset(), selected_cells=frozenset())
    fields.update(values)
    return SimpleNamespace(screen=SimpleNamespace(**fields), capture=SimpleNamespace(png=b'board'))


@dataclass
class Score:
    placements_covered: int = 1


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    r = object.__new__(event_treasure.TreasureRunner)
    r.config = SimpleNamespace(serial='test', package='game.test', state_dir=tmp_path / 'state',
                               ap_event_treasure_enabled=True)
    r.run_dir = tmp_path / 'run'
    r.run_dir.mkdir()
    r.startup = Mock()
    r.wall_clock = Mock(return_value=123.)
    r.clock = Mock(return_value=0.)
    r.started = 0.
    r.actions = 0
    r.phase = Mock()
    r.finish = Mock(side_effect=lambda status='completed': status)
    r.important = Mock()
    r.journal = Mock()
    r.tap = Mock()
    r.home = Mock()
    r.sleep = Mock()
    r.fail = Mock(side_effect=lambda detail: (_ for _ in ()).throw(RuntimeError(detail)))
    r.capture = Mock(return_value=board())
    r.stable_board = Mock(return_value=board())
    r.wait = Mock()
    monkeypatch.setattr(event_treasure, 'available_event', Mock(return_value=PROFILE))
    monkeypatch.setattr(event_treasure, 'enter_event', Mock(side_effect=AssertionError('unexpected navigation')))
    monkeypatch.setattr(event_treasure, 'inspect_receipt', Mock(side_effect=lambda runner, receipt, path: receipt))
    monkeypatch.setattr(event_treasure, 'choose_greedy_cell', Mock(return_value=SimpleNamespace(
        cell=CELL, scores={CELL: Score()}, candidate_count=1)))
    return r


def prepare_reveal(r, after=None):
    before = board()
    selected = board(selected=1, cost=200, selected_cells=frozenset({CELL}))
    confirm = SimpleNamespace(screen=SimpleNamespace(kind='treasure_confirm', target=(800, 500)))
    receipt = SimpleNamespace(screen=SimpleNamespace(kind='receipt'), capture=SimpleNamespace(png=b'receipt'))
    if after is None:
        after = board(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    r.stable_board.side_effect = [before, after]
    replies = iter([selected, confirm, receipt])
    def wait(kinds, predicate=lambda screen: True, **kwargs):
        reply = next(replies)
        assert reply.screen.kind in ({kinds} if isinstance(kinds, str) else kinds)
        assert predicate(reply.screen)
        return reply
    r.wait.side_effect = wait
    return before, selected, confirm, receipt


def test_disabled_does_not_observe_or_touch_game(runtime):
    runtime.config.ap_event_treasure_enabled = False
    assert runtime.run() == 'completed'
    runtime.capture.assert_not_called()
    runtime.tap.assert_not_called()


def test_unsupported_event_does_not_observe_or_touch_game(runtime):
    event_treasure.available_event.return_value = None
    assert runtime.run() == 'completed'
    runtime.capture.assert_not_called()
    runtime.tap.assert_not_called()


def test_no_currency_returns_home_without_selecting_or_spending(runtime):
    runtime.stable_board.return_value = board(currency=199)
    assert runtime.run() == 'completed'
    assert runtime.tap.call_count == 1
    assert 'Return home' in runtime.tap.call_args.args[2]
    event_treasure.choose_greedy_cell.assert_not_called()
    assert treasure_state.read_state(runtime.config)['pending'] is None


@pytest.mark.parametrize('elapsed,actions', [(2400, 0), (0, 420)])
def test_long_visit_yields_before_starting_another_transaction(runtime, elapsed, actions):
    runtime.clock.return_value = elapsed
    runtime.actions = actions
    assert runtime.run() == 'completed'
    event_treasure.choose_greedy_cell.assert_not_called()
    assert runtime.tap.call_count == 1
    assert 'Return home' in runtime.tap.call_args.args[2]
    assert runtime.important.call_args.args[0] == 'treasure_deferred'
    assert treasure_state.read_state(runtime.config)['pending'] is None


def test_preselected_board_is_held_before_any_input(runtime):
    runtime.stable_board.return_value = board(selected=1, cost=200, selected_cells=frozenset({CELL}))
    with pytest.raises(RuntimeError, match='preselected'):
        runtime.run()
    runtime.tap.assert_not_called()


def test_preselected_completed_board_cannot_create_refresh_intent(runtime):
    runtime.stable_board.return_value = board(inventory=(0, 0, 0), selected=1, cost=200,
                                             selected_cells=frozenset({CELL}))
    with pytest.raises(RuntimeError, match='preselected'):
        runtime.run()
    runtime.tap.assert_not_called()
    assert treasure_state.read_state(runtime.config)['pending'] is None


@pytest.mark.parametrize('matching_state', [False, True])
def test_completed_prize_without_saved_footprint_blocks_spending(runtime, matching_state):
    footprint = frozenset({(2, 4), (3, 4), (4, 4)})
    runtime.stable_board.return_value = board(inventory=(2, 4, 2), hits=footprint,
                                             closed=CELLS-footprint, remaining=42)
    if matching_state:
        saved = treasure_state.read_state(runtime.config)
        saved.update(event_id=PROFILE['id'], round=1)
        treasure_state.write_state(runtime.config, saved)
    with pytest.raises(RuntimeError, match='completed prize footprints'):
        runtime.run()
    runtime.tap.assert_not_called()
    event_treasure.choose_greedy_cell.assert_not_called()


def test_saved_completed_footprint_matches_inventory(runtime):
    footprint = frozenset({(2, 4), (3, 4), (4, 4)})
    runtime.stable_board.return_value = board(currency=199, inventory=(2, 4, 2), hits=footprint,
                                             closed=CELLS-footprint, remaining=42)
    saved = treasure_state.read_state(runtime.config)
    saved.update(event_id=PROFILE['id'], round=1, completed=[list(c) for c in sorted(footprint)])
    treasure_state.write_state(runtime.config, saved)
    assert runtime.run() == 'completed'
    assert runtime.tap.call_count == 1
    assert 'Return home' in runtime.tap.call_args.args[2]


def test_uncertain_planner_does_not_spend(runtime):
    event_treasure.choose_greedy_cell.side_effect = BoardUncertain('ambiguous board')
    with pytest.raises(BoardUncertain):
        runtime.run()
    runtime.tap.assert_not_called()
    assert treasure_state.read_state(runtime.config)['pending'] is None


def test_existing_unresolved_intent_blocks_even_navigation(runtime):
    saved = treasure_state.read_state(runtime.config)
    saved['pending'] = {'currency': 200, 'cell': [2, 4]}
    treasure_state.write_state(runtime.config, saved)
    with pytest.raises(RuntimeError, match='unresolved'):
        runtime.run()
    runtime.capture.assert_not_called()
    runtime.tap.assert_not_called()


def test_intent_is_durable_before_confirm_and_remains_through_receipt(runtime):
    _, _, confirmation, receipt = prepare_reveal(runtime)
    seen = []
    def tap(frame, target, detail):
        pending = treasure_state.read_state(runtime.config)['pending']
        if frame is confirmation or frame is receipt:
            assert pending['cell'] == list(CELL)
            assert pending['currency'] == pending['cost'] == 200
            seen.append(frame.screen.kind)
        if 'Return home' in detail:
            assert pending is None
    runtime.tap.side_effect = tap
    assert runtime.run() == 'completed'
    assert seen == ['treasure_confirm', 'receipt']
    assert treasure_state.read_state(runtime.config)['pending'] is None
    assert runtime.important.call_args.args[0] == 'treasure_revealed'


@pytest.mark.parametrize('change', [dict(currency=1), dict(remaining=45), dict(closed=CELLS),
                                   dict(round=2)])
def test_any_unverified_result_delta_keeps_pending(runtime, change):
    fields = dict(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    fields.update(change)
    prepare_reveal(runtime, board(**fields))
    with pytest.raises(RuntimeError, match='did not match'):
        runtime.run()
    assert treasure_state.read_state(runtime.config)['pending']['cell'] == list(CELL)
    runtime.home.assert_not_called()


def test_receipt_failure_keeps_pending_and_never_confirms_twice(runtime):
    _, _, confirm, _ = prepare_reveal(runtime)
    event_treasure.inspect_receipt.side_effect = RuntimeError('unreadable reward')
    with pytest.raises(RuntimeError, match='unreadable reward'):
        runtime.run()
    assert sum(call.args[0] is confirm for call in runtime.tap.call_args_list) == 1
    assert treasure_state.read_state(runtime.config)['pending'] is not None


@pytest.mark.parametrize('change', [dict(currency=199), dict(round=2), dict(cost=400),
                                   dict(remaining=44), dict(closed=CELLS-{CELL}),
                                   dict(hits=frozenset({CELL})), dict(empty=frozenset({CELL})),
                                   dict(inventory=(1, 5, 2))])
def test_changed_selection_terms_never_confirm_spending(runtime, change):
    selected = board(selected=1, cost=200, selected_cells=frozenset({CELL}))
    for key, value in change.items():
        setattr(selected.screen, key, value)
    runtime.wait.return_value = selected
    with pytest.raises(RuntimeError, match='selection cost or balance changed'):
        runtime.run()
    assert runtime.tap.call_count == 1
    assert treasure_state.read_state(runtime.config)['pending'] is None


def test_after_goal_round_no_tile_is_selected(runtime):
    runtime.stable_board.return_value = board(round=4)
    runtime.run()
    event_treasure.choose_greedy_cell.assert_not_called()
    assert runtime.tap.call_count == 1


def test_completed_goal_refreshes_once_without_spending_next_round(runtime):
    complete = board(round=3, inventory=(0, 0, 0))
    next_round = board(round=4)
    confirm = SimpleNamespace(screen=SimpleNamespace(kind='treasure_refresh_confirm', target=(800, 500)))
    runtime.stable_board.side_effect = [complete, next_round]
    runtime.wait.return_value = confirm
    runtime.run()
    event_treasure.choose_greedy_cell.assert_not_called()
    assert [call.args[2] for call in runtime.tap.call_args_list] == [
        'Refresh fully completed treasure board', 'Confirm free refresh of completed board',
        'Return home after Treasure Hunt']
    assert treasure_state.read_state(runtime.config)['round'] == 4
    assert treasure_state.read_state(runtime.config)['pending'] is None


def test_refresh_wait_rejects_old_board_until_next_round(runtime):
    complete = board(round=3, inventory=(0, 0, 0))
    next_round = board(round=4)
    runtime.stable_board.side_effect = [complete, next_round]
    def wait(kinds, predicate, **kwargs):
        assert not predicate(complete.screen)
        assert predicate(next_round.screen)
        return next_round
    runtime.wait.side_effect = wait
    assert runtime.run() == 'completed'
    assert treasure_state.read_state(runtime.config)['pending'] is None


def test_ambiguous_completed_prize_footprint_keeps_intent(runtime):
    after = board(currency=0, remaining=44, closed=CELLS-{CELL},
                  inventory=(2, 4, 2), hits=frozenset({CELL}))
    prepare_reveal(runtime, after)
    with pytest.raises(BoardUncertain, match='ambiguous'):
        runtime.run()
    assert treasure_state.read_state(runtime.config)['pending'] is not None


def test_refresh_intent_is_durable_before_both_inputs(runtime):
    runtime.stable_board.side_effect = [board(round=3, inventory=(0, 0, 0)), board(round=4)]
    runtime.wait.return_value = SimpleNamespace(screen=SimpleNamespace(
        kind='treasure_refresh_confirm', target=(800, 500)))
    def tap(frame, target, detail):
        saved = treasure_state.read_state(runtime.config)
        if 'refresh' in detail.lower():
            assert saved['pending']['kind'] == 'refresh'
            assert saved['pending']['round'] == 3
            assert saved['pending']['currency'] == 200
        else:
            assert saved['pending'] is None
    runtime.tap.side_effect = tap
    runtime.run()


@pytest.mark.parametrize('change', [dict(round=3), dict(currency=199), dict(remaining=44)])
def test_refresh_delta_mismatch_preserves_intent(runtime, change):
    fields = dict(round=4)
    fields.update(change)
    runtime.stable_board.side_effect = [board(round=3, inventory=(0, 0, 0)), board(**fields)]
    runtime.wait.return_value = board(round=4)
    with pytest.raises(RuntimeError, match='did not advance'):
        runtime.run()
    assert treasure_state.read_state(runtime.config)['pending']['kind'] == 'refresh'
    runtime.home.assert_not_called()


def recovery_intent(r, *, logged=True):
    before = board().screen
    evidence = r.run_dir / 'original-reward.png'
    evidence.write_bytes(b'reward')
    pending = dict(version=2, kind='reveal', before=treasure_state.board_snapshot(before),
                   cell=list(CELL), cost=200, completed=[], evidence=str(evidence),
                   run_dir=str(r.run_dir), receipt_logged=logged, receipt_seen=str(evidence))
    saved = treasure_state.read_state(r.config)
    saved.update(event_id=PROFILE['id'], round=1, completed=[], pending=pending)
    treasure_state.write_state(r.config, saved)
    return pending


def test_logged_pending_board_is_reconciled_without_repeating_currency_input(runtime):
    recovery_intent(runtime)
    after = board(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    runtime.capture.return_value = after
    runtime.stable_board.return_value = after
    runtime.config.ap_event_treasure_enabled = False
    runtime.run()
    assert treasure_state.read_state(runtime.config)['pending'] is None
    assert runtime.tap.call_count == 1
    assert 'Return home' in runtime.tap.call_args.args[2]
    event_treasure.inspect_receipt.assert_not_called()
    event_treasure.choose_greedy_cell.assert_not_called()


def test_unlogged_pending_board_cannot_discard_receipt_uncertainty(runtime):
    recovery_intent(runtime, logged=False)
    runtime.capture.return_value = board(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    with pytest.raises(RuntimeError, match='lacks a logged receipt'):
        runtime.run()
    assert treasure_state.read_state(runtime.config)['pending'] is not None
    runtime.tap.assert_not_called()


@pytest.mark.parametrize('change', [dict(currency=1), dict(inventory=(2, 4, 2)),
                                   dict(hits=frozenset({(1, 1)})), dict(selected=1)])
def test_logged_pending_requires_exact_post_transaction_board(runtime, change):
    recovery_intent(runtime)
    after = dict(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    after.update(change)
    runtime.capture.return_value = board(**after)
    runtime.stable_board.return_value = board(**after)
    with pytest.raises((RuntimeError, BoardUncertain)):
        runtime.run()
    assert treasure_state.read_state(runtime.config)['pending'] is not None
    runtime.tap.assert_not_called()


def test_recovery_reinspects_only_matching_saved_receipt(runtime, monkeypatch):
    import ba_automator.loot_receipts as receipts
    pending = recovery_intent(runtime, logged=False)
    runtime.vision = SimpleNamespace(startup=Mock())
    monkeypatch.setattr(receipts, 'page', Mock())
    matches = Mock(return_value=True)
    monkeypatch.setattr(receipts, 'same_receipt_view', matches)
    receipt = SimpleNamespace(screen=SimpleNamespace(kind='receipt'), capture=Capture(b'reward', 0., 'game.test'))
    runtime.capture.return_value = receipt
    runtime.stable_board.return_value = board(currency=0, remaining=44,
                                             closed=CELLS-{CELL}, empty=frozenset({CELL}))
    def tap(frame, target, detail):
        if frame is receipt:
            assert treasure_state.read_state(runtime.config)['pending']['receipt_logged'] is True
    runtime.tap.side_effect = tap
    runtime.run()
    matches.assert_called_once()
    event_treasure.inspect_receipt.assert_called_once_with(runtime, receipt, event_treasure.Path(pending['evidence']))
    assert len(runtime.tap.call_args_list) == 2
    assert treasure_state.read_state(runtime.config)['pending'] is None


def test_different_receipt_cannot_be_dismissed_during_recovery(runtime, monkeypatch):
    import ba_automator.loot_receipts as receipts
    recovery_intent(runtime)
    runtime.vision = SimpleNamespace(startup=Mock())
    monkeypatch.setattr(receipts, 'page', Mock())
    monkeypatch.setattr(receipts, 'same_receipt_view', Mock(return_value=False))
    runtime.capture.return_value = SimpleNamespace(screen=SimpleNamespace(kind='receipt'),
                                                  capture=Capture(b'other-reward', 0., 'game.test'))
    with pytest.raises(RuntimeError, match='differs'):
        runtime.run()
    runtime.tap.assert_not_called()


def test_interrupted_refresh_recovers_only_complete_new_board(runtime):
    saved = treasure_state.read_state(runtime.config)
    saved.update(event_id=PROFILE['id'], round=3, pending=dict(version=2, kind='refresh',
        before=treasure_state.board_snapshot(board(round=3, inventory=(0, 0, 0)).screen)))
    treasure_state.write_state(runtime.config, saved)
    runtime.capture.return_value = board(round=4)
    runtime.stable_board.return_value = board(round=4)
    runtime.run()
    assert treasure_state.read_state(runtime.config)['pending'] is None
    assert treasure_state.read_state(runtime.config)['round'] == 4
    assert runtime.tap.call_count == 1


@pytest.mark.parametrize('kind', ['treasure_refresh_confirm', 'unknown', 'treasure_confirm'])
def test_pending_refresh_only_confirms_recognized_free_refresh(runtime, kind):
    saved = treasure_state.read_state(runtime.config)
    saved.update(event_id=PROFILE['id'], round=3, pending=dict(version=2, kind='refresh',
        before=treasure_state.board_snapshot(board(round=3, inventory=(0, 0, 0)).screen)))
    treasure_state.write_state(runtime.config, saved)
    runtime.capture.return_value = SimpleNamespace(screen=SimpleNamespace(kind=kind, target=(773, 505)))
    runtime.wait.return_value = board(round=4)
    runtime.stable_board.return_value = board(round=4)
    if kind != 'treasure_refresh_confirm':
        with pytest.raises(RuntimeError, match='refresh is unresolved'):
            runtime.run()
        runtime.tap.assert_not_called()
        assert treasure_state.read_state(runtime.config)['pending'] is not None
        return
    runtime.run()
    assert treasure_state.read_state(runtime.config)['pending'] is None
    assert treasure_state.read_state(runtime.config)['round'] == 4
    assert [call.args[2] for call in runtime.tap.call_args_list] == [
        'Confirm pending free treasure refresh', 'Return home after treasure recovery']
    event_treasure.choose_greedy_cell.assert_not_called()


def test_pending_treasure_runs_before_restart_even_if_disabled(runtime):
    from ba_automator.config import Config
    from ba_automator.tasks import task_plan
    runtime.config.serial = '127.0.0.1:5695'
    runtime.config.package = 'com.nexon.bluearchive'
    recovery_intent(runtime)
    cfg = Config(serial=runtime.config.serial, package=runtime.config.package, state_dir=runtime.config.state_dir,
                 ap_event_treasure_enabled=False)
    for command in ('daily', 'restart', 'event_treasure', 'spend_ap'):
        plan = task_plan(command, cfg)
        assert plan[:2] == ('event_treasure', 'restart')
        assert plan.count('event_treasure') == 1


def test_receipt_checkpoint_is_immutable_and_keeps_original_identity(runtime):
    pending = recovery_intent(runtime, logged=False)
    runtime.saved = treasure_state.read_state(runtime.config)
    evidence = event_treasure.Path(pending['evidence'])
    runtime.receipt_checkpoint(SimpleNamespace(png=b'page-one'), evidence)
    first = treasure_state.read_state(runtime.config)['pending']['receipt_checkpoint']
    runtime.receipt_checkpoint(SimpleNamespace(png=b'page-two'), evidence)
    second = treasure_state.read_state(runtime.config)['pending']['receipt_checkpoint']
    assert first != second
    assert event_treasure.Path(first).read_bytes() == b'page-one'
    assert event_treasure.Path(second).read_bytes() == b'page-two'
    assert treasure_state.read_state(runtime.config)['pending']['evidence'] == str(evidence)
    assert not treasure_state.read_state(runtime.config)['pending']['receipt_logged']


def test_matching_mid_carousel_checkpoint_can_recover(runtime, monkeypatch):
    import ba_automator.loot_receipts as receipts
    pending = recovery_intent(runtime, logged=False)
    checkpoint = runtime.run_dir / 'mid-carousel.png'
    checkpoint.write_bytes(b'middle-page')
    saved = treasure_state.read_state(runtime.config)
    saved['pending']['receipt_checkpoint'] = str(checkpoint)
    treasure_state.write_state(runtime.config, saved)
    runtime.vision = SimpleNamespace(startup=Mock())
    monkeypatch.setattr(receipts, 'page', Mock())
    matches = Mock(return_value=True)
    monkeypatch.setattr(receipts, 'same_receipt_view', matches)
    runtime.capture.return_value = SimpleNamespace(screen=SimpleNamespace(kind='receipt'),
                                                  capture=Capture(b'middle-page', 0., 'game.test'))
    runtime.stable_board.return_value = board(currency=0, remaining=44, closed=CELLS-{CELL},
                                              empty=frozenset({CELL}))
    runtime.run()
    assert matches.call_args.args[0] == b'middle-page'
    assert treasure_state.read_state(runtime.config)['pending'] is None
    event_treasure.inspect_receipt.assert_called_once()


@pytest.mark.parametrize('damage', [
    lambda p: p['before'].update(currency=True),
    lambda p: p['before'].update(remaining=44),
    lambda p: p['before'].update(inventory=[2, 5, 3]),
    lambda p: p['before']['closed'].append([0, 0]),
    lambda p: p.update(receipt_logged='yes'),
    lambda p: p.update(receipt_checkpoint='/outside/checkpoint.png'),
    lambda p: p.update(completed=[[0, 0]]),
    lambda p: p.update(cell=[5, 0]),
])
def test_malformed_recovery_intent_cannot_send_inputs(runtime, damage):
    recovery_intent(runtime)
    saved = treasure_state.read_state(runtime.config)
    damage(saved['pending'])
    treasure_state.write_state(runtime.config, saved)
    with pytest.raises(RuntimeError, match='Invalid pending treasure recovery facts'):
        runtime.run()
    runtime.capture.assert_not_called()
    runtime.tap.assert_not_called()


def test_pending_recovery_preserves_enabled_post_farming_visit(runtime):
    from ba_automator.config import Config
    from ba_automator.tasks import task_plan
    runtime.config.serial = '127.0.0.1:5695'
    runtime.config.package = 'com.nexon.bluearchive'
    recovery_intent(runtime)
    cfg = Config(serial=runtime.config.serial, package=runtime.config.package,
                 state_dir=runtime.config.state_dir, ap_event_treasure_enabled=True)
    assert task_plan('spend_ap', cfg) == (
        'event_treasure', 'restart', 'spend_ap', 'event_treasure', 'red_dots')


@pytest.mark.parametrize('enabled,active', [(False, True), (True, False)])
def test_treasure_noop_keeps_daily_running(runtime, enabled, active):
    from ba_automator.task_execution import run_daily_plan
    runtime.config.ap_event_treasure_enabled = enabled
    event_treasure.available_event.return_value = PROFILE if active else None
    runtime.finish.side_effect = lambda status='success': SimpleNamespace(status=status, run_dir=runtime.run_dir)
    calls, events = [], []
    def run(task):
        calls.append(task)
        return runtime.run() if task == 'event_treasure' else SimpleNamespace(status='success')
    result = run_daily_plan(('restart', 'event_treasure', 'spend_ap'), run, events.append)
    assert result['status'] == 'success'
    assert result['failed_tasks'] == []
    assert calls == ['restart', 'event_treasure', 'spend_ap']
    runtime.capture.assert_not_called()
    runtime.tap.assert_not_called()


def recovered_receipt(r, png, timestamp):
    return SimpleNamespace(screen=SimpleNamespace(kind='receipt'), capture=Capture(png, timestamp, 'game.test'))


def test_logged_pending_parses_saved_identity_before_fresh_capture(runtime, monkeypatch):
    import ba_automator.loot_receipts as receipts
    recovery_intent(runtime)
    runtime.vision = SimpleNamespace(startup=Mock())
    now = [0.]
    runtime.clock.side_effect = lambda: now[0]
    def parse(png, vision):
        now[0] = 20.  # Native OCR of old evidence expires the initial frame.
        return 'saved-identity'
    parsed = Mock(side_effect=parse)
    monkeypatch.setattr(receipts, 'page', parsed)
    matches = Mock(return_value=True)
    monkeypatch.setattr(receipts, 'same_receipt_view', matches)
    runtime.capture.side_effect = lambda: recovered_receipt(runtime, b'reward', now[0])
    runtime.stable_board.return_value = board(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    def tap(frame, target, detail):
        if 'receipt' in detail:
            assert frame.capture.captured_at == 20.
            assert frame.capture.is_fresh(runtime.clock())
    runtime.tap.side_effect = tap
    runtime.run()
    parsed.assert_called_once()
    matches.assert_called_once_with(b'reward', b'reward', 'saved-identity', vision=runtime.vision.startup)
    event_treasure.inspect_receipt.assert_not_called()
    assert treasure_state.read_state(runtime.config)['pending'] is None


def test_logged_pending_revalidates_after_identity_ocr_expires_capture(runtime, monkeypatch):
    import ba_automator.loot_receipts as receipts
    recovery_intent(runtime)
    runtime.vision = SimpleNamespace(startup=Mock())
    monkeypatch.setattr(receipts, 'page', Mock(return_value='saved-identity'))
    now, seen = [0.], []
    runtime.clock.side_effect = lambda: now[0]
    def matches(before, after, expected, **kwargs):
        seen.append((before, after, expected))
        if len(seen) == 1:
            now[0] += 6.
        return True
    monkeypatch.setattr(receipts, 'same_receipt_view', matches)
    runtime.capture.side_effect = lambda: recovered_receipt(runtime, b'live-reward', now[0])
    runtime.stable_board.return_value = board(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    def tap(frame, target, detail):
        if 'receipt' in detail:
            assert frame.capture.captured_at == 6.
            assert frame.capture.is_fresh(now[0])
    runtime.tap.side_effect = tap
    runtime.run()
    assert seen == [(b'reward', b'live-reward', 'saved-identity')] * 2
    event_treasure.inspect_receipt.assert_not_called()
    assert treasure_state.read_state(runtime.config)['pending'] is None


@pytest.mark.parametrize('failure', ['expired', 'different', 'not_receipt'])
def test_pending_receipt_revalidation_is_bounded_and_preserves_intent(runtime, monkeypatch, failure):
    import ba_automator.loot_receipts as receipts
    pending = recovery_intent(runtime)
    runtime.vision = SimpleNamespace(startup=Mock())
    monkeypatch.setattr(receipts, 'page', Mock(return_value='saved-identity'))
    now = [0.]
    runtime.clock.side_effect = lambda: now[0]
    def matches(*args, **kwargs):
        now[0] += 6. if failure == 'expired' else 0.
        return failure != 'different'
    matcher = Mock(side_effect=matches)
    monkeypatch.setattr(receipts, 'same_receipt_view', matcher)
    def capture():
        if runtime.capture.call_count > 1 and failure == 'not_receipt':
            return board()
        return recovered_receipt(runtime, b'live', now[0])
    runtime.capture.side_effect = capture
    with pytest.raises(RuntimeError, match='differs.*expired'):
        runtime.run()
    assert runtime.capture.call_count == 5  # Initial classification plus four readonly attempts.
    assert runtime.sleep.call_count == 3
    runtime.tap.assert_not_called()
    runtime.stable_board.assert_not_called()
    event_treasure.inspect_receipt.assert_not_called()
    assert treasure_state.read_state(runtime.config)['pending'] == pending
    for call in matcher.call_args_list:
        assert call.args[0] == b'reward'  # Never promote an unverified screenshot.


def test_stable_completed_board_uses_journaled_final_reveal(runtime):
    import json
    from pathlib import Path
    from ba_automator.shop_runtime import ShopFrame
    from ba_automator.treasure_vision import TreasureScreen
    runtime.saved = json.loads((Path(__file__).parent / 'fixtures/treasure/final-reveal.json').read_text())
    observed = TreasureScreen('treasure_complete', round=1, currency=2359, remaining=8,
                              selected=0, cost=0, inventory=(0, 0, 0))
    runtime.wait.return_value = ShopFrame(SimpleNamespace(png=b'complete'), observed)
    frame = event_treasure.TreasureRunner.stable_board(runtime)
    assert frame.screen.kind == 'treasure_board'
    assert len(frame.screen.hits) == 31
    runtime.tap.assert_not_called()


@pytest.mark.parametrize('kind', ['home', 'event_page'])
def test_logged_pending_can_reopen_board_without_replaying_tile(runtime, kind):
    recovery_intent(runtime)
    after = board(currency=0, remaining=44, closed=CELLS-{CELL}, empty=frozenset({CELL}))
    runtime.capture.return_value = board(kind=kind)
    runtime.wait.return_value = after
    runtime.stable_board.return_value = after
    event_treasure.enter_event.side_effect = None
    event_treasure.enter_event.return_value = board(kind='event_page')
    runtime.run()
    assert treasure_state.read_state(runtime.config)['pending'] is None
    assert event_treasure.enter_event.call_count == (1 if kind == 'home' else 0)
    assert [c.args[1] for c in runtime.tap.call_args_list] == [(515, 663), (1237, 24)]
    event_treasure.inspect_receipt.assert_not_called()
    event_treasure.choose_greedy_cell.assert_not_called()


@pytest.mark.parametrize('damage', ['unlogged', 'missing_receipt', 'legacy'])
def test_pending_home_requires_durable_logged_receipt_before_navigation(runtime, damage):
    pending = recovery_intent(runtime, logged=damage != 'unlogged')
    if damage == 'missing_receipt':
        from pathlib import Path
        Path(pending['receipt_seen']).unlink()
    elif damage == 'legacy':
        saved = treasure_state.read_state(runtime.config)
        saved['pending'] = {'currency': 200, 'cell': [2, 4]}
        treasure_state.write_state(runtime.config, saved)
    runtime.capture.return_value = board(kind='home')
    with pytest.raises(RuntimeError):
        runtime.run()
    event_treasure.enter_event.assert_not_called()
    runtime.tap.assert_not_called()
    assert treasure_state.read_state(runtime.config)['pending'] is not None


def test_restart_exception_is_specific_to_logged_treasure_recovery(runtime):
    pending = recovery_intent(runtime)
    with pytest.raises(RuntimeError, match='unresolved'):
        treasure_state.ensure_safe(runtime.config)
    treasure_state.ensure_safe(runtime.config, recover_logged_receipt=True)
    saved = treasure_state.read_state(runtime.config)
    saved['pending']['receipt_logged'] = False
    treasure_state.write_state(runtime.config, saved)
    with pytest.raises(RuntimeError, match='unresolved'):
        treasure_state.ensure_safe(runtime.config, recover_logged_receipt=True)


@pytest.mark.parametrize('foreground,logged,relaunch', [
    ('com.uncube.launcher3', True, True),
    ('com.android.launcher3', True, True),
    ('game.test', True, False),
    ('other.app', True, False),
    ('com.uncube.launcher3', False, False),
])
def test_runner_only_relaunches_logged_recovery_from_known_launcher(
        runtime, monkeypatch, foreground, logged, relaunch):
    from ba_automator import restart
    from ba_automator.locking import InstanceLock, LockError
    pending = recovery_intent(runtime, logged=logged)
    runtime.config.lock_dir = runtime.run_dir / 'locks'
    device = Mock()
    device.foreground_package.return_value = foreground

    def boot(config, selected_device, vision, **kwargs):
        assert selected_device is device
        assert kwargs['recover_logged_treasure'] is True
        # Restart must be able to take its own lock and must preserve intent.
        with InstanceLock(config):
            treasure_state.ensure_safe(config, recover_logged_receipt=True)
            assert treasure_state.read_state(config)['pending'] == pending

    boot_mock = Mock(side_effect=boot)
    monkeypatch.setattr(restart, 'run_restart', boot_mock)
    runner = Mock()
    def run():
        with pytest.raises(LockError):
            with InstanceLock(runtime.config):
                pass
        assert treasure_state.read_state(runtime.config)['pending'] == pending
        return 'completed'
    runner.run.side_effect = run
    monkeypatch.setattr(event_treasure, 'TreasureRunner', Mock(return_value=runner))
    assert event_treasure.run_event_treasure(runtime.config, device, runtime.startup) == 'completed'
    assert boot_mock.call_count == int(relaunch)
    runner.journal.close.assert_called_once()
