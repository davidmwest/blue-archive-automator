"""The 44-sweep event receipt must not lose rows to subpixel rendering."""
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import Word


@pytest.fixture
def large_scroll(monkeypatch):
    root = Path(__file__).parent / 'fixtures'
    quantities = ([1232, 1056, 176, 132, 176, 88, 61, 73, 68, 66, 81, 75],
                  [61, 73, 68, 66, 81, 75, 64, 65, 63, 10, 29, 6])
    monkeypatch.setattr(lr, 'read_native_game_words', lambda *_: [
        Word('Full List', 1., (575, 129, 705, 158)),
        Word('Okay', 1., (600, 520, 680, 550)),
    ])
    pages = []
    for side, amounts in zip(('before', 'after'), quantities):
        observed = iter(amounts)
        monkeypatch.setattr(lr, 'read_game_crop', lambda *_: [
            Word(f'x{next(observed)}', 1., (240, 210, 320, 250)),
        ])
        png = (root / f'loot-event-grid-large-sweep-{side}.png').read_bytes()
        pages.append(lr.page(png, object()))
        assert next(observed, None) is None
    return pages


def test_large_event_sweep_proves_only_six_overlapping_cards(large_scroll):
    before, after = large_scroll
    assert before.kind == after.kind == 'grid'
    assert len(before.cards) == len(after.cards) == 12
    assert all(a.box[1] - b.box[1] == 32
               for a, b in zip(before.cards[-6:], after.cards[:6]))
    assert lr.scrolled_grid_overlap(before.cards, after.cards) == 6
    combined = before.cards + after.cards[6:]
    assert len(combined) == 18
    assert [c.quantity for c in combined] == [
        1232, 1056, 176, 132, 176, 88, 61, 73, 68, 66, 81, 75,
        64, 65, 63, 10, 29, 6,
    ]
    assert before.trailing_clipped and after.trailing_clipped  # More rows still need inspection.
    assert not lr.ReceiptReader.same(before, after)


@pytest.mark.parametrize('change', ['quantity', 'column', 'tier', 'different_item',
                                   'artwork', 'alpha', 'reordered', 'single_card'])
def test_large_scroll_still_rejects_unproven_overlap(large_scroll, change):
    before, after = large_scroll
    original, changed = before.cards[6], after.cards[0]
    if change == 'quantity':
        changed = replace(changed, quantity=60)
    elif change == 'column':
        changed = replace(changed, box=(changed.box[0] + 1,) + changed.box[1:])
    elif change == 'tier':
        changed = replace(changed, tier='T3')
    elif change == 'different_item':
        changed = replace(changed, icon=after.cards[2].icon)
    elif change == 'reordered':
        assert lr.scrolled_grid_overlap(before.cards, after.cards[1:2] + after.cards[:1] + after.cards[2:]) == 0
        return
    elif change == 'single_card':
        assert lr.scrolled_grid_overlap((original,), (changed,)) == 0
        return
    else:
        image = cv2.imdecode(np.frombuffer(changed.icon, np.uint8), cv2.IMREAD_UNCHANGED)
        if change == 'artwork':
            image[20:35, 30:50, :3] = 0
        else:
            image[40, 50, 3] = 0
        changed = replace(changed, icon=lr.encode(image))
    assert lr.scrolled_grid_overlap(before.cards, (changed,) + after.cards[1:]) == 0
