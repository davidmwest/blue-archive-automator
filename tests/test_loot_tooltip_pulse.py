"""Tooltip identity belongs to its text panel, not its animated pointer."""

from pathlib import Path

import cv2
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import StartupVision, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


def frames():
    return [(FIXTURES / f"loot-tooltip-pointer-pulse-{suffix}.png").read_bytes()
            for suffix in ("before", "after")]


def test_real_selected_card_glow_does_not_change_tooltip_body_identity():
    before, after = frames()
    vision = StartupVision()
    for png in (before, after):
        image = decode_frame(png)
        assert lr.tooltip_boxes(image) == ((515, 303, 339, 133),)
        assert lr.page(png, vision).kind == "tooltip"
        assert lr.read_tooltip(image, vision) == "General Hairpin Blueprint"
        assert not image[:69].any() and not image[135:290].any()
        assert not image[630:].any()
    assert lr.same_receipt_view(before, after, lr.Page("tooltip"), vision=vision)


@pytest.mark.parametrize("box", [(550, 321, 100, 14), (599, 347, 20, 14),
                                (533, 405, 70, 19)])
def test_changed_tooltip_name_owned_amount_or_bottom_description_stays_rejected(box):
    before, after = frames()
    image = decode_frame(after)
    x, y, w, h = box
    image[y:y+h, x:x+w] = (0, 0, 0)
    assert lr.tooltip_boxes(image) == ((515, 303, 339, 133),)
    changed = cv2.imencode(".png", image)[1].tobytes()
    assert not lr.same_receipt_view(before, changed, lr.Page("tooltip"))


def test_pointer_normalization_cannot_accept_a_moved_tooltip():
    before, after = frames()
    image = decode_frame(after)
    image[303:448, 515:859] = image[303:448, 510:854].copy()
    shifted = cv2.imencode(".png", image)[1].tobytes()
    assert not lr.same_receipt_view(before, shifted, lr.Page("tooltip"))
