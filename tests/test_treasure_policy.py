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
