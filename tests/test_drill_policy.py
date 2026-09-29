from dataclasses import replace

import pytest

from ba_automator.assault_policy import TeamMember
from ba_automator.drill_policy import fingerprint, validate_plan


def teams():
    return [
        [TeamMember(f'student-{round_}-{slot}', slot,
                    'striker' if slot < 4 else 'special', 'explosive', 3, 80)
         for slot in range(6)]
        for round_ in range(3)
    ]


@pytest.mark.parametrize('slot', [0, 4])
def test_one_assistant_can_be_striker_or_special(slot):
    plan = teams()
    plan[1][slot] = replace(plan[1][slot], assistant=True, assistant_id='offering-a')
    assert validate_plan(plan, [2, 2, 2])


def test_owned_and_borrowed_copies_can_be_in_different_rounds():
    plan = teams()
    plan[1][0] = replace(plan[0][0], assistant=True, assistant_id='offering-a')
    assert validate_plan(plan, [2, 2, 2])
    plan[1][0] = replace(plan[1][0], assistant=False, assistant_id=None)
    with pytest.raises(ValueError, match='more than one'):
        validate_plan(plan, [2, 2, 2])


def test_same_variant_cannot_be_owned_and_borrowed_in_one_team():
    plan = teams()
    plan[0][1] = replace(plan[0][0], slot=1, assistant=True, assistant_id='offering-a')
    with pytest.raises(ValueError, match='more than one'):
        validate_plan(plan, [2, 2, 2])


def test_borrow_allowance_is_shared_across_all_rounds():
    plan = teams()
    for index in (0, 2):
        plan[index][0] = replace(plan[index][0], assistant=True, assistant_id=f'offering-{index}')
    with pytest.raises(ValueError, match='Only one assistant'):
        validate_plan(plan, [2, 2, 2])


def test_changing_lender_invalidates_practice_fingerprint():
    team = teams()[0]
    team[0] = replace(team[0], assistant=True, assistant_id='offering-a')
    proven = fingerprint(team, 2, 'observed-skills')
    team[0] = replace(team[0], assistant_id='offering-b')
    assert fingerprint(team, 2, 'observed-skills') != proven
