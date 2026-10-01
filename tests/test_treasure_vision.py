"""Sanitized live board regressions; no emulator or OCR engine is required."""
import json
from pathlib import Path
from unittest.mock import Mock

import cv2
import pytest

from ba_automator.treasure_vision import TreasureVision, classify_treasure
from ba_automator.vision import Word, decode_frame

ROOT = Path(__file__).parent / 'fixtures' / 'treasure'


def sample(name):
    return (decode_frame((ROOT / f'{name}.png').read_bytes()),
            [Word(**w) for w in json.loads((ROOT / f'{name}.json').read_text())])


@pytest.fixture
def vision():
    return TreasureVision(Mock(), Mock())


@pytest.mark.parametrize('name,currency,remaining,inventory,hits', [
    ('selected', 7367, 45, (2, 5, 2), set()),
    ('two-revealed', 6967, 43, (2, 5, 2), {(2, 4), (3, 4)}),
    ('three-revealed', 6767, 42, (2, 4, 2), {(2, 4), (3, 4), (4, 4)}),
    ('completed-sunscreen', 5967, 38, (2, 4, 1),
     {(2, 0), (2, 1), (2, 2), (2, 3), (2, 4), (3, 4), (4, 4)}),
])
def test_observed_board(vision, name, currency, remaining, inventory, hits):
    frame, words = sample(name)
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_board'
    assert result.round == 1
    assert result.currency == currency
    assert result.remaining == remaining == len(result.closed)
    assert result.inventory == inventory
    assert result.hits == hits
    assert not result.empty
    assert result.selected_cells == ({(2, 4)} if name == 'selected' else set())
    assert result.selected == len(result.selected_cells)
    assert result.cost == 200 * result.selected


def test_dialogue_obscuring_grid_is_not_a_safe_board(vision):
    for name in ('board', 'revealed-one'):
        frame, words = sample(name)
        assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_open_confirmation(vision):
    frame, words = sample('confirm')
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_confirm'
    assert result.target == (773, 505)


def test_unrecognized_notice_is_not_confirmation(vision):
    frame, words = sample('confirm')
    words = [Word('Buy more currency?', w.confidence, w.box)
             if w.text == 'Open the selected slot(s)?' else w for w in words]
    assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_inconsistent_remaining_or_missing_inventory_fails_closed(vision):
    frame, words = sample('three-revealed')
    wrong = [Word('Remaining Slots: 41/45', w.confidence, w.box)
             if w.text.startswith('Remaining Slots') else w for w in words]
    assert classify_treasure(frame, wrong, vision.templates).kind == 'unknown'
    assert classify_treasure(frame, [w for w in words if w.text != 'x4'], vision.templates).kind == 'unknown'


def test_dimmed_board_and_unknown_open_tile_fail_closed(vision):
    frame, words = sample('three-revealed')
    assert classify_treasure((frame * .6).astype('uint8'), words, vision.templates).kind == 'unknown'
    frame[328:398, 883:953] = (30, 60, 90)
    assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_receipt_precedes_board(monkeypatch, vision):
    words = [Word(**w) for w in json.loads((ROOT / 'receipt.json').read_text())]
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_words', lambda *a: words)
    result = vision.analyze((ROOT.parent / 'loot-treasure-single-credits-native.png').read_bytes())
    assert result.kind == 'receipt'
    vision.fallback.analyze.assert_not_called()


def test_other_screen_and_billing_use_fallback(monkeypatch, vision):
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_words', lambda *a: [])
    png = (ROOT / 'three-revealed.png').read_bytes()
    assert vision.analyze(png) == vision.fallback.analyze.return_value
    vision.fallback.analyze.assert_called_with(png, billing=False)
    vision.analyze(png, billing=True)
    vision.fallback.analyze.assert_called_with(png, billing=True)


def test_selected_count_and_cost_must_match_visible_check(vision):
    frame, words = sample('selected')
    wrong_cost = [Word('400', w.confidence, w.box) if w.text == '200' else w for w in words]
    assert classify_treasure(frame, wrong_cost, vision.templates).kind == 'unknown'
    no_selection = [Word('Open Slot x0', w.confidence, w.box) if w.text == 'Open Slot x1'
                    else Word('0', w.confidence, w.box) if w.text == '200' else w for w in words]
    assert classify_treasure(frame, no_selection, vision.templates).kind == 'unknown'


def test_low_confidence_currency_is_not_spendable(vision):
    frame, words = sample('selected')
    words = [Word(w.text, .8, w.box) if w.text == '7,367' else w for w in words]
    assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_empty_stone_slots_are_not_prize_fragments(vision):
    frame, words = sample('completed-gun')
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_board'
    assert result.currency == 4767
    assert result.remaining == 32
    assert result.inventory == (1, 4, 1)
    assert result.empty == {(0, 1), (4, 1)}
    assert result.hits == {(r, c) for r in (1, 2, 3) for c in (0, 1)} | {
        (2, 2), (2, 3), (2, 4), (3, 4), (4, 4)}


def test_missing_fullframe_counter_uses_bounded_native_crop(monkeypatch, vision):
    _, words = sample('completed-gun')
    words = [w for w in words if w.box[0] != 204]
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_words', lambda *a: words)
    regional = Mock(return_value=[Word('x1', .9661, (14, 9, 43, 31))])
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_region', regional)
    png = (ROOT / 'completed-gun.png').read_bytes()
    result = vision.analyze(png)
    assert result.kind == 'treasure_board'
    assert result.inventory == (1, 4, 1)
    regional.assert_called_once_with(png, vision.startup, (190, 665, 245, 708))


@pytest.mark.parametrize('name', ['selected', 'completed-gun'])
def test_inventory_fallback_cannot_make_dimmed_board_actionable(monkeypatch, vision, name):
    frame, words = sample(name)
    missing = [w for w in words if 195 <= w.box[0] <= 240 and w.box[1] >= 668]
    assert len(missing) == 1
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_words',
                        lambda *a: [w for w in words if w not in missing])
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_region',
                        lambda *a: [Word(missing[0].text, .99, (14, 9, 43, 31))])
    _, encoded = cv2.imencode('.png', (frame * .6).astype('uint8'))
    assert vision.analyze(encoded.tobytes()).kind == 'unknown'


def test_entry_dialogue_cannot_turn_completed_prizes_into_empty_cells(vision):
    frame, words = sample('entry-dialogue')
    # Supply the counter recovered by native regional OCR, so this regression
    # exercises the overlay guard rather than failing on a missing count.
    words.append(Word('x1', .99, (208, 678, 235, 695)))
    assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_finished_phone_with_dim_counter_is_recognized(vision):
    frame, words = sample('completed-phone')
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_board'
    assert result.inventory == (1, 0, 1)
    assert result.currency == 3759
    assert result.remaining == 15
    assert {(0, 5), (0, 6), (0, 7)} <= result.hits
    assert result.empty == {(0, 1), (1, 7), (4, 1), (2, 8)}
    assert classify_treasure((frame * .6).astype('uint8'), words, vision.templates).kind == 'unknown'


@pytest.mark.parametrize('change', ['missing_finish', 'wrong_card', 'missing_zero', 'positive', 'too_faint', 'contradiction'])
def test_finished_inventory_requires_independent_consistent_evidence(vision, change):
    frame, words = sample('completed-phone')
    if change == 'missing_finish':
        words = [w for w in words if w.text != 'Finish']
    elif change == 'wrong_card':
        words = [Word(w.text, w.confidence, (420, 619, 478, 640)) if w.text == 'Finish' else w for w in words]
    elif change == 'missing_zero':
        words = [w for w in words if w.text != 'X0']
    else:
        words = [Word('x1' if change in ('positive', 'contradiction') else w.text,
                      .99 if change == 'contradiction' else .6 if change == 'too_faint' else w.confidence,
                      w.box) if w.text == 'X0' else w for w in words]
    assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_finished_inventory_native_retry(monkeypatch, vision):
    _, words = sample('completed-phone')
    words = [w for w in words if w.text != 'X0']
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_words', lambda *a: words)
    regional = Mock(return_value=[Word('X0', .70128, (0, 0, 65, 43))])
    monkeypatch.setattr('ba_automator.treasure_vision.read_game_region', regional)
    png = (ROOT / 'completed-phone.png').read_bytes()
    assert vision.analyze(png).inventory == (1, 0, 1)
    regional.assert_called_once_with(png, vision.startup, (320, 665, 385, 708))


def test_completed_round_overlay_is_explicit_not_a_spendable_grid(vision):
    frame, words = sample('round-complete')
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_complete'
    assert (result.round, result.currency, result.remaining, result.inventory) == (1, 2359, 8, (0, 0, 0))
    assert not result.closed and not result.hits
    changed = [Word('Please buy currency.', w.confidence, w.box)
               if w.text.startswith('Please refresh') else w for w in words]
    assert classify_treasure(frame, changed, vision.templates).kind == 'unknown'


def test_observed_free_refresh_confirmation(vision):
    frame, words = sample('refresh-confirm')
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_refresh_confirm'
    assert result.target == (773, 505)
    words = [Word('Refresh for 200 Pyroxenes?', w.confidence, w.box)
             if w.text == 'Refresh for the next round?' else w for w in words]
    assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_fresh_second_round_uses_its_own_inventory(vision):
    frame, words = sample('round-two')
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_board'
    assert (result.round, result.currency, result.inventory) == (2, 2359, (1, 2, 5))
    assert len(result.closed) == result.remaining == 45
    assert not result.hits and not result.empty
    from ba_automator.treasure_state import refresh_result
    refresh_result({'before': {'round': 1, 'currency': 2359}}, result)
    from dataclasses import replace
    with pytest.raises(RuntimeError):
        refresh_result({'before': {'round': 1, 'currency': 2359}}, replace(result, inventory=(2, 5, 2)))


def test_umbrella_art_is_not_a_dialogue_overlay(vision):
    frame, words = sample('round-two-umbrella')
    result = classify_treasure(frame, words, vision.templates)
    assert result.kind == 'treasure_board'
    assert result.round == 2 and result.currency == 1559
    assert result.inventory == (1, 2, 5)
    assert result.hits == {(1, 4), (1, 5), (1, 6)}
    assert result.empty == {(2, 3)}
    assert len(result.closed) == 41
    # Confident text over the grid still blocks spending.
    words.append(Word('Here are your rewards!', .99, (650, 270, 950, 295)))
    assert classify_treasure(frame, words, vision.templates).kind == 'unknown'


def test_small_surfboard_tip_is_a_hit_and_leaves_a_feasible_board(vision):
    from ba_automator.treasure_policy import TreasureShape, choose_greedy_cell
    from ba_automator.treasure_rounds import round_shapes

    frame, words = sample('round-two-surfboard-tip')
    board = classify_treasure(frame, words, vision.templates)
    assert board.kind == 'treasure_board'
    assert (board.round, board.currency, board.remaining, board.inventory) == (2, 1035, 32, (1, 1, 4))
    assert (1, 1) in board.hits  # Only a small yellow triangle is exposed.
    assert board.empty == {(2, 3)}
    completed = {(1, c) for c in range(4, 8)} | {(3, c) for c in range(3, 6)}
    choice = choose_greedy_cell(5, 9, tuple(
        TreasureShape(shape.height, shape.width, count)
        for shape, count in zip(round_shapes(board.round), board.inventory)),
        empty=board.empty, hits=board.hits - completed, completed=completed)
    assert choice.cell in board.closed
    assert (1, 1) not in choice.scores
    assert classify_treasure((frame * .6).astype('uint8'), words, vision.templates).kind == 'unknown'
