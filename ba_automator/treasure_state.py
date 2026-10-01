"""Atomic treasure transaction intent; an interrupted reveal is never replayed."""
import json
import os
from pathlib import Path
from uuid import uuid4
from .ap_state import state_path as ap_path
from .treasure_rounds import round_shapes, round_counts


def state_path(config):
    path = ap_path(config)
    return path.with_name('treasure-' + path.name[3:])


def read_state(config):
    try:
        state = json.loads(state_path(config).read_text())
    except FileNotFoundError:
        return dict(version=1, event_id=None, round=None, completed=[], pending=None)
    if (not isinstance(state, dict) or state.get('version') != 1
            or not isinstance(state.get('completed'), list)
            or 'pending' not in state):
        raise RuntimeError('Invalid treasure state; inspect before spending event currency')
    cells = state['completed']
    if any(not isinstance(c, list) or len(c) != 2 or any(type(n) is not int for n in c)
           or not 0 <= c[0] < 5 or not 0 <= c[1] < 9 for c in cells):
        raise RuntimeError('Invalid completed treasure cells')
    pending = state['pending']
    if pending is not None:
        if not isinstance(pending, dict) or not pending:
            raise RuntimeError('Invalid pending treasure intent')
        if pending.get('version') == 2:
            validate_intent(pending)
    return state


def write_state(config, state):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temp.open('w') as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def completed_rectangle(before, after, hits, last_cell, completed, round_no=1):
    """A single inventory decrement must identify exactly one revealed prize."""
    from .treasure_policy import BoardUncertain
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    if not changed:
        return frozenset()
    if len(changed) != 1 or before[changed[0]] - after[changed[0]] != 1:
        raise BoardUncertain('Treasure inventory changed unexpectedly')
    shape = round_shapes(round_no)[changed[0]]
    h, w = shape.height, shape.width
    available = set(hits) - set(completed)
    rectangles = set()
    for height, width in {(h, w), (w, h)}:
        for r in range(5 - height + 1):
            for c in range(9 - width + 1):
                cells = frozenset((y, x) for y in range(r, r + height)
                                  for x in range(c, c + width))
                if last_cell in cells and cells <= available:
                    rectangles.add(cells)
    if len(rectangles) != 1:
        raise BoardUncertain('Completed treasure footprint is ambiguous; inspect its receipt and board')
    return rectangles.pop()


def logged_reveal_can_navigate(pending):
    """Only a durable, already logged receipt permits leaving its old screen.

    This does not resolve the transaction: the original board delta must still
    match exactly after navigation, before any further currency can be spent.
    """
    if not pending or pending.get('version') != 2:
        return False
    validate_intent(pending)
    if pending['kind'] != 'reveal' or not pending['receipt_logged']:
        return False
    reference = (pending.get('receipt_ready') or pending.get('receipt_checkpoint')
                 or pending.get('receipt_seen'))
    return bool(reference and Path(reference).is_file() and Path(reference).stat().st_size)


def ensure_safe(config, *, recover_logged_receipt=False):
    pending = read_state(config)['pending']
    if pending and not (recover_logged_receipt and logged_reveal_can_navigate(pending)):
        raise RuntimeError('Treasure reveal has an unresolved receipt; inspect its trace before restarting')


def board_snapshot(board):
    """Persist only transaction facts, independent of OCR implementation classes."""
    return {**{key: getattr(board, key) for key in
               ('round', 'currency', 'remaining', 'selected', 'cost')},
            'inventory': list(board.inventory),
            **{key: [list(c) for c in sorted(getattr(board, key))]
               for key in ('closed', 'hits', 'empty', 'selected_cells')}}


def reveal_result(pending, board, completed):
    """Validate the complete resource/board delta before clearing an intent."""
    before = pending['before']
    cell = tuple(pending['cell'])
    old_closed = {tuple(c) for c in before['closed']}
    old_hits = {tuple(c) for c in before['hits']}
    old_empty = {tuple(c) for c in before['empty']}
    if (board.round != before['round'] or board.currency != before['currency'] - pending['cost']
            or board.remaining != before['remaining'] - 1 or board.closed != old_closed - {cell}
            or board.selected != 0 or board.selected_cells or board.cost != 0
            or not old_hits <= board.hits or not old_empty <= board.empty
            or board.hits | board.empty != old_hits | old_empty | {cell}
            or board.hits & board.empty):
        raise RuntimeError('Treasure currency or revealed cells did not match the saved intent')
    return set(completed) | set(completed_rectangle(
        before['inventory'], board.inventory, board.hits, cell, completed, board.round))


def refresh_result(pending, board):
    before = pending['before']
    all_cells = frozenset((r, c) for r in range(5) for c in range(9))
    if (board.round != before['round'] + 1 or board.currency != before['currency']
            or board.remaining != 45 or board.closed != all_cells
            or board.hits or board.empty or board.selected or board.selected_cells
            or board.cost or board.inventory != round_counts(board.round)):
        raise RuntimeError('Treasure refresh did not advance exactly one round')


def validate_intent(pending):
    """Old intents remain held; only complete typed v2 facts enable recovery."""
    def cells(value):
        if (not isinstance(value, list) or any(not isinstance(c, list) or len(c) != 2
                or any(type(n) is not int for n in c)
                or not 0 <= c[0] < 5 or not 0 <= c[1] < 9 for c in value)):
            raise ValueError('invalid cells')
        result = {tuple(c) for c in value}
        if len(result) != len(value):
            raise ValueError('duplicate cells')
        return result
    try:
        before = pending['before']
        if (pending['kind'] not in ('reveal', 'refresh')
                or any(type(before[key]) is not int or before[key] < 0
                       for key in ('round', 'currency', 'remaining', 'selected', 'cost'))
                or before['round'] < 1 or before['selected'] or before['cost']):
            raise ValueError('invalid board facts')
        closed, hits, empty = (cells(before[key]) for key in ('closed', 'hits', 'empty'))
        if (len(closed) != before['remaining'] or before['selected_cells']
                or closed & hits or closed & empty or hits & empty
                or len(closed | hits | empty) != 45):
            raise ValueError('invalid board partition')
        inventory = before['inventory']
        if (not isinstance(inventory, list) or len(inventory) != 3
                or any(type(n) is not int or not 0 <= n <= maximum
                       for n, maximum in zip(inventory, round_counts(before['round'])))):
            raise ValueError('invalid inventory')
        if pending['kind'] == 'refresh':
            if any(inventory):
                raise ValueError('unfinished refresh')
        else:
            cell = cells([pending['cell']])
            if (not cell <= closed or type(pending['cost']) is not int or pending['cost'] != 200
                    or before['currency'] < pending['cost']
                    or type(pending['receipt_logged']) is not bool
                    or not cells(pending['completed']) <= hits):
                raise ValueError('invalid reveal facts')
            directory = Path(pending['run_dir'])
            for key in ('evidence', 'receipt_seen', 'receipt_ready', 'receipt_checkpoint'):
                if key in pending and (not isinstance(pending[key], str)
                        or Path(pending[key]).parent != directory
                        or Path(pending[key]).suffix != '.png'):
                    raise ValueError('invalid receipt path')
            if 'evidence' not in pending:
                raise ValueError('missing evidence')
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('Invalid pending treasure recovery facts; inspect its saved receipt') from exc


def completed_board(saved, observed):
    """Reconstruct a shaded completed board only from verified durable facts."""
    from .treasure_vision import TreasureScreen
    if observed.kind != 'treasure_complete' or observed.inventory != (0, 0, 0):
        raise RuntimeError('Expected a fully completed treasure board')
    pending = saved.get('pending')
    if pending and pending.get('kind') == 'reveal':
        validate_intent(pending)
        if not pending['receipt_logged'] or sum(pending['before']['inventory']) != 1:
            raise RuntimeError('Final treasure reveal lacks a verified receipt or last prize')
        facts = dict(pending['before'])
        cell = tuple(pending['cell'])
        facts.update(currency=facts['currency'] - pending['cost'], remaining=facts['remaining'] - 1,
                     inventory=[0, 0, 0],
                     closed=[c for c in facts['closed'] if tuple(c) != cell],
                     hits=[*facts['hits'], list(cell)])
    else:
        facts = saved.get('finished_board')
        if not facts or saved['event_id'] is None or saved['round'] != observed.round:
            raise RuntimeError('Completed treasure board has no saved verified footprint')
    if any(getattr(observed, k) != facts[k] for k in ('round', 'currency', 'remaining', 'selected', 'cost')):
        raise RuntimeError('Completed treasure board differs from the saved transaction')
    board = TreasureScreen('treasure_board', **{k: facts[k] for k in
                           ('round', 'currency', 'remaining', 'selected', 'cost')},
                           inventory=tuple(facts['inventory']),
                           **{k: frozenset(tuple(c) for c in facts[k]) for k in
                              ('closed', 'hits', 'empty', 'selected_cells')})
    completed = {tuple(c) for c in saved['completed']}
    if pending and pending.get('kind') == 'reveal':
        completed = reveal_result(pending, board, completed)
    if board.inventory != (0, 0, 0) or len(completed) != sum(s.height * s.width * s.count for s in round_shapes(board.round)) or completed != board.hits:
        raise RuntimeError('Completed treasure prize footprints are inconsistent')
    return board
