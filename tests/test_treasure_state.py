import pytest
from ba_automator.treasure_state import completed_rectangle
from ba_automator.treasure_policy import BoardUncertain


def test_only_newly_completed_phone_is_removed():
    cells = {(2, 4), (3, 4), (4, 4)}
    assert completed_rectangle((2, 5, 2), (2, 4, 2), cells, (4, 4), ()) == cells
    assert not completed_rectangle((2, 5, 2), (2, 5, 2), cells, (4, 4), ())


def test_ambiguous_neighbor_prizes_stop():
    with pytest.raises(BoardUncertain):
        completed_rectangle((2, 5, 2), (2, 4, 2), {(0, 0),(0, 1),(0, 2),(0, 3)}, (0, 1), ())
    with pytest.raises(BoardUncertain):
        completed_rectangle((2, 5, 2), (2, 3, 2), {(0, 0),(0, 1),(0, 2)}, (0, 1), ())


def test_completed_prizes_excluded_from_later_rectangle():
    assert completed_rectangle((2, 4, 2), (2, 4, 1), {(0, 0),(0, 1),(0, 2),(0, 3),(0, 4)},
                               (0, 4), {(0, 0),(0, 1),(0, 2)}) == {(0, 3),(0, 4)}


def test_live_final_prize_reconciles_shaded_board_and_survives_next_visit():
    import json
    from pathlib import Path
    from ba_automator.treasure_vision import TreasureScreen
    from ba_automator.treasure_state import completed_board, board_snapshot
    saved = json.loads((Path(__file__).parent / 'fixtures/treasure/final-reveal.json').read_text())
    observed = TreasureScreen('treasure_complete', round=1, currency=2359, remaining=8,
                              selected=0, cost=0, inventory=(0, 0, 0))
    result = completed_board(saved, observed)
    assert len(result.hits) == 31 and len(result.closed) == 8 and len(result.empty) == 6
    saved.update(pending=None, completed=[list(c) for c in result.hits], finished_board=board_snapshot(result))
    assert completed_board(saved, observed) == result


@pytest.mark.parametrize('change', ['balance', 'unlogged', 'inventory', 'cell', 'footprint'])
def test_completed_overlay_cannot_bypass_reveal_checks(change):
    import json
    from pathlib import Path
    from ba_automator.treasure_vision import TreasureScreen
    from ba_automator.treasure_state import completed_board
    saved = json.loads((Path(__file__).parent / 'fixtures/treasure/final-reveal.json').read_text())
    observed = TreasureScreen('treasure_complete', round=1, currency=2359, remaining=8,
                              selected=0, cost=0, inventory=(0, 0, 0))
    if change == 'balance': saved['pending']['before']['currency'] += 200
    if change == 'unlogged': saved['pending']['receipt_logged'] = False
    if change == 'inventory': saved['pending']['before']['inventory'] = [0, 0, 2]
    if change == 'cell': saved['pending']['cell'] = [0, 8]
    if change == 'footprint': saved['completed'] = []
    with pytest.raises((RuntimeError, ValueError)):
        completed_board(saved, observed)


@pytest.mark.parametrize('round_no,index,size', [(2, 0, (2, 4)), (2, 1, (4, 1)), (2, 2, (1, 3)),
                                               (3, 0, (3, 3)), (3, 1, (2, 2)), (3, 2, (1, 2))])
def test_round_specific_prize_completion(round_no, index, size):
    from ba_automator.treasure_rounds import round_counts
    before = round_counts(round_no)
    after = list(before); after[index] -= 1
    h, w = size
    hits = {(r, c) for r in range(h) for c in range(w)}
    assert completed_rectangle(before, after, hits, (h-1, w-1), (), round_no) == hits


def test_reviewed_round_counts_and_area():
    from ba_automator.treasure_rounds import round_shapes, round_counts
    assert [round_counts(n) for n in range(1, 8)] == [
        (2,5,2), (1,2,5), (1,4,3), (2,5,2), (1,2,5), (1,4,3), (2,3,6)]
    assert all(sum(s.height*s.width*s.count for s in round_shapes(n)) <= 45 for n in range(1, 8))
