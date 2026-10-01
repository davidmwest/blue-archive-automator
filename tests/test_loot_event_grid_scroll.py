"""Replay the event Quest 5 receipt that left a confirmed AP spend pending."""

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import Word


@pytest.fixture
def event_scroll(monkeypatch):
    root = Path(__file__).parent / "fixtures"
    pngs = [(root / f"loot-event-grid-equal-height-{side}.png").read_bytes()
            for side in ("before", "after")]
    quantities = ([15, 13, 6, 4, 6, 3, 1, 3, 1, 3, 1, 1],
                  [1, 3, 1, 3, 1, 1, 1, 514])
    monkeypatch.setattr(lr, "read_native_game_words", lambda *_: [
        Word("Full List", 1., (575, 129, 705, 158)),
        Word("Okay", 1., (600, 520, 680, 550)),
    ])
    pages = []
    for png, amounts in zip(pngs, quantities):
        observed = iter(amounts)
        monkeypatch.setattr(lr, "read_game_crop", lambda *_: [
            Word(f"x{next(observed)}", 1., (240, 210, 320, 250)),
        ])
        pages.append(lr.page(png, object()))
        assert next(observed, None) is None
    return pngs, pages


def test_event_scroll_proves_six_repeated_equipment_cards(event_scroll):
    pngs, (before, after) = event_scroll
    assert before.kind == after.kind == "grid"
    assert len(before.cards) == 12 and len(after.cards) == 8
    assert all(a.box[3] == b.box[3] == 89 and a.box[1] - b.box[1] == 16
               for a, b in zip(before.cards[-6:], after.cards[:6]))
    assert lr.scrolled_grid_overlap(before.cards, after.cards) == 6
    merged = before.cards + after.cards[6:]
    assert len(merged) == 14
    assert [c.quantity for c in merged[-2:]] == [1, 514]
    assert before.trailing_clipped and not after.trailing_clipped
    # Matching scroll overlap does not authorize taps on a changed viewport.
    assert not lr.ReceiptReader.same(before, after)
    assert not lr.same_receipt_view(*pngs, before)


@pytest.mark.parametrize("change", [
    "quantity", "missing_quantity", "tier", "column", "width", "height",
    "different_item", "artwork", "interior_alpha", "silhouette",
    "conflicting_names", "reordered", "translation", "single_card",
])
def test_event_scroll_rejects_changed_or_unproven_cards(event_scroll, change):
    _, (before, after) = event_scroll
    original, changed = before.cards[6], after.cards[0]
    if change == "quantity":
        changed = replace(changed, quantity=2)
    elif change == "missing_quantity":
        changed = replace(changed, quantity=None)
    elif change == "tier":
        changed = replace(changed, tier="T3")
    elif change in {"column", "width", "height", "translation"}:
        box = list(changed.box)
        box[{"column": 0, "width": 2, "height": 3, "translation": 1}[change]] += 2
        changed = replace(changed, box=tuple(box))
    elif change == "different_item":
        # Necklace and bag blueprints share quantity, tier, and card framing.
        changed = replace(changed, icon=after.cards[2].icon)
    elif change == "conflicting_names":
        before = replace(before, cards=before.cards[:6] + (
            replace(original, name="General Necklace Blueprint"),
        ) + before.cards[7:])
        changed = replace(changed, name="General Bag Blueprint")
    elif change == "reordered":
        after = replace(after, cards=after.cards[1:2] + after.cards[:1] + after.cards[2:])
        changed = after.cards[0]
    elif change == "single_card":
        assert lr.scrolled_grid_overlap((original,), (changed,)) == 0
        return
    else:
        image = cv2.imdecode(np.frombuffer(changed.icon, np.uint8), cv2.IMREAD_UNCHANGED)
        if change == "artwork":
            image[20:35, 30:50, :3] = 0
        elif change == "interior_alpha":
            image[40, 50, 3] = 0
        else:
            image[20:35, 30:50, 3] = 0
        changed = replace(changed, icon=lr.encode(image))
    assert lr.scrolled_grid_overlap(before.cards, (changed,) + after.cards[1:]) == 0


def test_event_scroll_fixtures_hide_account_background(event_scroll):
    for png in event_scroll[0]:
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        image[228:1208, 628:1928] = 0
        assert not np.any(image)
