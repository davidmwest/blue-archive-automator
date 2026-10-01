"""Exact roster restoration only during free mock qualification."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from ba_automator.assault_policy import TeamMember
from ba_automator.joint_firing_drill import (
    DrillRunner, exact_owned_drill_card, owned_drill_blanks, unchanged_drill_portraits,
)
from ba_automator.vision import Word, decode_frame

FIXTURE = Path(__file__).parent / 'fixtures' / 'drill-owned-restoration'


def fixture():
    image = decode_frame(FIXTURE.with_suffix('.png').read_bytes())
    words = [Word(w['text'], w['confidence'], tuple(w['box']))
             for w in json.loads(FIXTURE.with_suffix('.json').read_text())]
    return image, words


def test_saved_quick_roster_finds_only_exact_unselected_owned_student():
    image, words = fixture()
    assert owned_drill_blanks(words) == (0,)
    assert exact_owned_drill_card(image, words, 'Aris (Maid)') == (961, 312)
    assert exact_owned_drill_card(image, words, 'Aris') is None
    for selected in ('Eimi (Armed)', 'Momoi', 'Hoshino'):
        assert exact_owned_drill_card(image, words, selected) is None


def test_wrapped_variant_is_not_mistaken_for_regular_student():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    words = [Word('Hoshino', 1., (585, 350, 655, 370)),
             Word('(Swimsuit)', 1., (580, 372, 665, 392))]
    assert exact_owned_drill_card(image, words, 'Hoshino') is None
    assert exact_owned_drill_card(image, words, 'Hoshino (Swimsuit)') == (628, 305)


def test_ambiguous_duplicate_cards_and_low_confidence_empty_are_held():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    words = [Word('Momoi', 1., (585, 350, 655, 370)),
             Word('Momoi', 1., (696, 350, 766, 370))]
    assert exact_owned_drill_card(image, words, 'Momoi') is None
    assert owned_drill_blanks([Word('EMPTY', .89, (35, 575, 95, 600))]) == ()


def test_existing_portrait_change_is_detected_but_new_slot_is_allowed():
    before = np.zeros((720, 1280, 3), dtype=np.uint8)
    after = before.copy()
    after[584:616, 48:85] = 200
    assert unchanged_drill_portraits(before, after, range(1, 6))
    assert not unchanged_drill_portraits(before, after, range(6))


def team():
    return tuple(TeamMember(f'student-{i}', i, 'striker' if i < 4 else 'special',
                            'explosive', 3, 80, False, None) for i in range(6))


@pytest.mark.parametrize('paid', [True, False])
def test_restoration_requires_free_mock_and_still_verifies_full_team(paid):
    r = object.__new__(DrillRunner)
    expected = team()
    screen = lambda **kw: SimpleNamespace(screen=SimpleNamespace(**kw))
    partial = screen(team=(), quick_target=(1, 2))
    restored = screen(team=expected)
    r.stage = Mock(return_value=screen(remaining_rounds=3, target=(1, 2)))
    r.tap = Mock()
    r.sleep = Mock()
    r.wait = Mock(return_value=partial)
    r.restore_owned_mock = Mock(return_value=restored)
    r.verify_real_owned = Mock(side_effect=lambda frame, members: frame)
    r.open_unit(screen(active=True, mock=not paid), 0, 1, expected, paid=paid)
    if paid:
        r.restore_owned_mock.assert_not_called()
        r.verify_real_owned.assert_called_once_with(partial, expected)
    else:
        r.restore_owned_mock.assert_called_once_with(partial, expected)
        r.verify_real_owned.assert_called_once_with(restored, expected)


def test_borrowed_formation_cannot_enter_owned_restoration():
    r = object.__new__(DrillRunner)
    r.fail = Mock(side_effect=RuntimeError('held'))
    r.tap = Mock()
    expected = (replace(team()[0], assistant=True, assistant_id='lender'), *team()[1:])
    with pytest.raises(RuntimeError, match='held'):
        r.restore_owned_mock(None, expected)
    r.tap.assert_not_called()


def test_restores_exact_empty_slot_then_requires_ownership_and_complete_formation():
    image, words = fixture()
    capture = SimpleNamespace(png=FIXTURE.with_suffix('.png').read_bytes())
    quick = SimpleNamespace(capture=capture, screen=SimpleNamespace(
        words=words, confirm_target=(1180, 670)))
    filled = SimpleNamespace(capture=capture, screen=SimpleNamespace(
        words=[w for w in words if w.normalized != 'empty'], confirm_target=(1180, 670)))
    expected = (replace(team()[0], student_id='Aris (Maid)'), *team()[1:])
    complete = SimpleNamespace(screen=SimpleNamespace(team=expected))
    formation = SimpleNamespace(screen=SimpleNamespace(quick_target=(1180, 120)))
    r = object.__new__(DrillRunner)
    r.tap = Mock()
    r.swipe = Mock()
    r.fail = Mock(side_effect=RuntimeError('held'))
    observations = iter([quick, quick, quick, filled, complete])
    def wait(kind, predicate=lambda screen: True):
        result = next(observations)
        assert predicate(result.screen)
        return result
    r.wait = Mock(side_effect=wait)
    r.verify_owned_quick = Mock(return_value=filled)
    assert r.restore_owned_mock(formation, expected) is complete
    assert r.tap.call_args_list[3].args[1] == (961, 312)
    r.swipe.assert_not_called()
    r.verify_owned_quick.assert_called_once_with(filled)
