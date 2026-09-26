"""Replay the actual Full List frames that held completed AP/Bounty sweeps."""

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import StartupVision, Word, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def grids():
    vision = StartupVision()
    pairs = {}
    for kind in ("tier", "dismiss"):
        pngs = [(FIXTURES / f"loot-grid-{kind}-pulse-{suffix}.png").read_bytes()
                for suffix in ("before", "after")]
        pairs[kind] = pngs, [lr.page(png, vision) for png in pngs]
    return vision, pairs


def test_tier_outline_pulse_keeps_all_ten_hard_stage_drops_once(grids):
    vision, pairs = grids
    pngs, (before, after) = pairs["tier"]
    assert before.kind == after.kind == "grid"
    assert not before.clipped and not after.clipped
    assert [c.quantity for c in before.cards] == [1, 1, 2, 1, 8, 7, 10, 1, 2, 923]
    assert [c.tier for c in before.cards[:4]] == ["T5", "T5", "T5", "T3"]
    assert not lr.same_icon(before.cards[2].icon, after.cards[2].icon)
    assert lr.ReceiptReader.same(before, after)
    assert all(lr.same_card(a, b) for a, b in zip(before.cards, after.cards))
    assert lr.same_receipt_view(*pngs, before, vision=vision)
    # No OCR evidence means the badge is still part of the strict pixel guard.
    assert not lr.same_receipt_view(*pngs, before)


def test_neutral_dismiss_pulse_does_not_invalidate_bounty_cards(grids):
    vision, pairs = grids
    pngs, (before, after) = pairs["dismiss"]
    assert before.kind == after.kind == "grid"
    assert before.clipped and after.clipped
    assert len(before.cards) == 12
    assert before.cards[0].quantity == 40
    assert lr.ReceiptReader.same(before, after)
    assert lr.same_receipt_view(*pngs, before, vision=vision)


def test_grid_overlap_still_rejects_changed_tier_quantity_and_artwork(grids):
    _, pairs = grids
    _, (before, after) = pairs["tier"]
    a, b = before.cards[2], after.cards[2]
    assert not lr.same_card(a, replace(b, tier="T4"))
    assert not lr.same_card(a, replace(b, quantity=b.quantity + 1))
    assert not lr.same_card(replace(a, tier=None), replace(b, tier=None))
    artwork = cv2.imdecode(np.frombuffer(b.icon, np.uint8), cv2.IMREAD_UNCHANGED)
    artwork[15:30, 30:50, :3] = (0, 0, 0)
    assert not lr.same_card(a, replace(b, icon=lr.encode(artwork)))
    moved = replace(after, cards=(replace(after.cards[0], box=(400, 188, 110, 89)),)
                    + after.cards[1:])
    assert not lr.ReceiptReader.same(before, moved)


@pytest.mark.parametrize("point", [(630, 139), (640, 531), (575, 221), (604, 265), (380, 449)])
def test_grid_guard_keeps_heading_controls_artwork_quantity_and_clipped_edge(grids, point):
    vision, pairs = grids
    (before, after), (expected, _) = pairs["dismiss"]
    changed = decode_frame(after).copy()
    x, y = point
    changed[y-7:y+8, x-7:x+8] = (0, 0, 0)
    assert not lr.same_receipt_view(before, lr.encode(changed), expected, vision=vision)


def test_unreadable_tier_is_not_a_license_to_ignore_a_changed_badge(grids):
    vision, pairs = grids
    (before, after), (expected, _) = pairs["tier"]
    changed = decode_frame(after).copy()
    x, y, w, h = expected.cards[2].box
    changed[y+h-30:y+h, x:x+40] = (0, 0, 0)
    assert not lr.same_receipt_view(before, lr.encode(changed), expected, vision=vision)


@pytest.mark.parametrize("word", [
    Word("T5", .94, (47, 219, 120, 280)),
    Word("x5", 1, (47, 219, 120, 280)),
    Word("T5", 1, (147, 219, 220, 280)),
    Word("T5", 1, (47, 19, 120, 80)),
])
def test_tier_identity_requires_confident_exact_text_inside_badge(word):
    assert lr.grid_tier([word]) is None
    assert lr.grid_tier([Word("T5", 1, (47, 219, 120, 280))]) == "T5"


def test_fixture_pixels_outside_receipt_are_masked(grids):
    _, pairs = grids
    for pngs, _ in pairs.values():
        for png in pngs:
            image = decode_frame(png).copy()
            image[114:604, 314:964] = 0
            assert not np.any(image)


def test_full_list_with_eleph_is_readable_after_opening_animation():
    """The saved stable view after an empty initial parse contains ten cards."""
    png = (FIXTURES / "loot-grid-hard-eleph.png").read_bytes()
    p = lr.page(png, StartupVision())
    assert p.kind == "grid" and not p.clipped
    assert [c.quantity for c in p.cards] == [1, 1, 2, 1, 1, 4, 6, 5, 3, 1439]
    assert [c.tier for c in p.cards[1:5]] == ["T4", "T4", "T4", "T2"]
    image = decode_frame(png).copy()
    image[114:604, 314:964] = 0
    assert not np.any(image)
