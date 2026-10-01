import pytest
from ba_automator.treasure_policy import BoardUncertain, Treasure, choose_cell


def test_center_intersects_most_placements():
    choice = choose_cell(1, 5, (Treasure('a', 1, 3),), {})
    assert choice.cell == (0, 2)
    assert choice.placements_covered == 3


def test_miss_changes_next_choice():
    choice = choose_cell(1, 5, (Treasure('a', 1, 2),), {(0, 1): None})
    assert choice.cell == (0, 3)


def test_hit_and_empty_force_completion():
    choice = choose_cell(1, 4, (Treasure('a', 1, 2),),
                         {(0, 0): None, (0, 1): 'a'})
    assert choice.cell == (0, 2)


def test_unwanted_treasure_still_blocks_space():
    choice = choose_cell(1, 4, (Treasure('a', 1, 2),
                               Treasure('b', 1, 2, desired=False)), {(0, 0): 'b'})
    assert choice.cell == (0, 2)
    assert choice.supported_placements == 1


def test_completed_goal_returns_no_click():
    assert choose_cell(1, 3, (Treasure('a', 1, 2),),
                       {(0, 0): 'a', (0, 1): 'a'}) is None


def test_global_impossibility_fails_closed():
    with pytest.raises(BoardUncertain):
        choose_cell(1, 3, (Treasure('a', 1, 2), Treasure('b', 1, 2)), {})


def test_budget_does_not_return_partially_scored_choice():
    with pytest.raises(BoardUncertain, match='budget'):
        choose_cell(5, 9, (Treasure('a', 2, 3),), {}, max_nodes=1)


def test_rotations_require_explicit_permission():
    with pytest.raises(BoardUncertain):
        choose_cell(3, 1, (Treasure('a', 1, 3),), {})
    assert choose_cell(3, 1, (Treasure('a', 1, 3, rotations=True),), {}).cell == (0, 0)


def test_unknown_identity_not_treated_as_empty():
    with pytest.raises(BoardUncertain):
        choose_cell(1, 3, (Treasure('a', 1, 2),), {(0, 0): 'unknown'})


from ba_automator.treasure_policy import TreasureShape, choose_greedy_cell


def test_greedy_live_inventory_is_bounded_and_deterministic():
    shapes = (TreasureShape(2, 3, 2), TreasureShape(1, 3, 5), TreasureShape(1, 2, 2))
    result = choose_greedy_cell(5, 9, shapes)
    assert result == choose_greedy_cell(5, 9, shapes)
    assert result.cell == (2, 2)
    assert result.candidate_count == 190  # 52 rectangles + 62 triples + 76 pairs
    assert result.scores[result.cell].placements_covered == 62
    assert len(result.scores) == 45


def test_greedy_prefers_finishing_known_hit_over_unexplored_coverage():
    result = choose_greedy_cell(1, 7, (TreasureShape(1, 2, 1),), hits={(0, 0)})
    assert result.cell == (0, 1)
    assert result.scores[result.cell].possible_completions == 1
    assert result.scores[(0, 3)].placements_covered == 2


def test_greedy_rotations_and_misses_constrain_hit_completion():
    result = choose_greedy_cell(3, 3, (TreasureShape(1, 3, 1),),
                                hits={(0, 1), (1, 1)}, empty={(0, 0), (0, 2)})
    assert result.cell == (2, 1)
    assert result.scores[result.cell].possible_completions == 1


def test_greedy_completed_shapes_are_never_clicked_or_crossed():
    result = choose_greedy_cell(1, 7, (TreasureShape(1, 3, 1),),
                                completed={(0, 0), (0, 1)}, empty={(0, 2)})
    assert result.cell == (0, 4)
    assert result.candidate_count == 2
    assert set(result.scores) == {(0, 3), (0, 4), (0, 5), (0, 6)}


def test_greedy_adjacent_anonymous_hits_may_be_different_prizes():
    result = choose_greedy_cell(1, 4, (TreasureShape(1, 2, 2),), hits={(0, 1), (0, 2)})
    assert result.cell == (0, 0)
    assert result.scores[(0, 0)].possible_completions == 2
    assert result.scores[(0, 3)].possible_completions == 2


@pytest.mark.parametrize('kwargs', [
    {'empty': {(0, 0)}, 'hits': {(0, 0)}},
    {'completed': {(0, 0)}, 'hits': {(0, 0)}},
    {'empty': {(9, 9)}},
    {'hits': {(0, 0)}, 'empty': {(0, 1), (1, 0)}},
])
def test_greedy_rejects_contradictory_observations(kwargs):
    with pytest.raises(BoardUncertain):
        choose_greedy_cell(2, 2, (TreasureShape(1, 2, 1),), **kwargs)


def test_greedy_inventory_capacity_and_completed_semantics():
    with pytest.raises(BoardUncertain, match='area'):
        choose_greedy_cell(1, 3, (TreasureShape(1, 2, 2),))
    assert choose_greedy_cell(1, 3, ()) is None
    assert choose_greedy_cell(1, 3, (TreasureShape(1, 2, 0),)) is None
    with pytest.raises(BoardUncertain, match='Unfinished hits'):
        choose_greedy_cell(1, 3, (), hits={(0, 1)})


def test_greedy_budget_never_returns_partially_scored_move():
    with pytest.raises(BoardUncertain, match='budget'):
        choose_greedy_cell(5, 9, (TreasureShape(2, 3, 2), TreasureShape(1, 3, 5)),
                           max_placements=50)
