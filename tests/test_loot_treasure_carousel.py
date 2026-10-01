"""Preserve ordered overlap when scrolling changes qualifier OCR spacing."""
from dataclasses import replace
from pathlib import Path

import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import StartupVision


@pytest.fixture(scope='module')
def pages():
    vision = StartupVision()
    root = Path(__file__).parent / 'fixtures'
    return [lr.page((root / f'loot-treasure-carousel-qualifier-{suffix}.png').read_bytes(), vision)
            for suffix in ('before', 'after')]


def test_real_treasure_pages_have_one_three_card_overlap(pages):
    before, after = pages
    assert before.kind == after.kind == 'reward'
    assert before.leading_clipped and after.leading_clipped
    expected = [('Damaged Atlantis Medal', 1), ('Intact Atlantis Medal', 1),
                ('Beginner Tactical Training Blu-ray (Hyakkiyako)', 1)]
    assert [(c.name, c.quantity) for c in before.cards[-3:]] == expected
    assert [(c.name, c.quantity) for c in after.cards[:3]] == expected
    matches = [n for n in range(1, min(len(before.cards), len(after.cards)) + 1)
               if all(lr.same_card(a, b) for a, b in zip(before.cards[-n:], after.cards[:n]))]
    assert matches == [3]
    # Only the two new fully visible cards may be appended. The final doll
    # remains in the clipped viewport edge and must be read on a later page.
    assert [(c.name, c.quantity) for c in after.cards[3:]] == [
        ('Advanced Tactical Training Blu-ray (Hyakkiyako)', 1),
        ('Superior Tactical Training Blu-ray (Hyakkiyako)', 1),
    ]
    assert after.trailing_clipped


@pytest.mark.parametrize('label', [
    'Beginner Tactical Training Blu-ray(Hyakkiyako)',
    'Beginner Tactical Training Blu-ray ( Hyakkiyako )',
])
def test_only_qualifier_whitespace_is_ignored(pages, label):
    card = pages[0].cards[-1]
    assert lr.same_card(card, replace(card, name=label))
    for name in ('Advanced Tactical Training Blu-ray (Hyakkiyako)',
                 'Beginner Tactical Training Blu-ray (Gehenna)',
                 'Beginner Tactical Training Blu-ray',
                 'Beginner TacticalTraining Blu-ray (Hyakkiyako)'):
        assert not lr.same_card(card, replace(card, name=name))
    assert not lr.same_card(card, replace(card, name=label, quantity=2))
    assert not lr.same_card(card, replace(card, name=label, box=(100, 255, 149, 225)))
