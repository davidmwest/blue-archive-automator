"""The display mask must preserve full cards and receipt comparison pixels."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator.loot_icon_mask import isolate_compact_card
from ba_automator.loot_receipts import encode, same_icon


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["fedora", "helmet"])
def test_observed_grid_card_hides_neighbors_and_preserves_full_art(name):
    original = cv2.imread(str(FIXTURES / f"loot-compact-{name}.png"))
    masked = isolate_compact_card(original)
    assert masked.shape == (89, 110, 4)
    assert np.array_equal(masked[:, :, :3], original)
    # Adjacent card fragments are at opposite corners of the slanted rectangle.
    assert masked[15, 1, 3] == 0
    assert masked[78, 108, 3] == 0
    # Keep the whole item's frame, center artwork, lower tier badge and quantity.
    assert all(masked[y, x, 3] == 255 for x, y in [
        (100, 8), (50, 40), (8, 78), (85, 80),
    ])
    assert same_icon(encode(original), encode(masked))
    assert np.array_equal(masked, isolate_compact_card(original))


def test_broken_observed_outline_is_not_used_to_cut_artwork():
    original = cv2.imread(str(FIXTURES / "loot-compact-broken-outline.png"))
    assert isolate_compact_card(original) is original


def test_absent_or_ambiguous_white_frame_keeps_source_pixels():
    for color in (0, 160, 255):
        original = np.full((64, 75, 3), color, dtype=np.uint8)
        assert isolate_compact_card(original) is original


def test_transparent_corners_do_not_weaken_receipt_change_detection():
    original = cv2.imread(str(FIXTURES / "loot-compact-fedora.png"))
    masked = isolate_compact_card(original)
    changed = original.copy()
    changed[5:30, :5] = (0, 0, 0)
    assert not same_icon(encode(masked), encode(isolate_compact_card(changed)))
