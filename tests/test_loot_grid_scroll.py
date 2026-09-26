"""Replay the 13-item Hard sweep whose Full List scroll held AP spending."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.config import Config
from ba_automator.runtime import Capture, Journal
from ba_automator.vision import StartupVision, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def scrolled():
    vision = StartupVision()
    pngs = [(FIXTURES / f"loot-grid-scroll-{side}.png").read_bytes()
            for side in ("before", "after")]
    return vision, pngs, [lr.page(png, vision) for png in pngs]


def test_real_grid_scroll_proves_six_item_overlap_and_one_new_credit_card(scrolled):
    _, _, (before, after) = scrolled
    assert before.kind == after.kind == "grid"
    assert [c.quantity for c in before.cards] == [1, 2, 2, 1, 1, 1, 3, 3, 4, 1, 1, 1]
    assert [c.quantity for c in after.cards] == [3, 3, 4, 1, 1, 1, 1187]
    assert lr.scrolled_grid_overlap(before.cards, after.cards) == 6
    assert len(before.cards + after.cards[6:]) == 13
    assert all(lr.same_scrolled_grid_card(a, b)
               for a, b in zip(before.cards[-6:], after.cards[:6]))


def test_scroll_tolerance_never_weakens_input_identity(scrolled):
    vision, pngs, (before, after) = scrolled
    assert not any(lr.same_card(a, b)
                   for a, b in zip(before.cards[-6:], after.cards[:6]))
    assert not lr.ReceiptReader.same(before, after)
    assert not lr.same_receipt_view(*pngs, before, vision=vision)


def test_scrolled_overlap_rejects_conflicting_complete_names(scrolled):
    _, _, (before, after) = scrolled
    first = replace(before.cards[6], name="General Amulet Blueprint")
    second = replace(after.cards[0], name="General Bag Blueprint")
    assert not lr.same_scrolled_grid_card(first, second)
    assert lr.scrolled_grid_overlap(before.cards[:6] + (first,) + before.cards[7:],
                                    (second,) + after.cards[1:]) == 0


@pytest.mark.parametrize("change", [
    "quantity", "missing_quantity", "tier", "column", "width", "height",
    "artwork", "different_item", "silhouette", "invalid_icon",
])
def test_scrolled_overlap_rejects_changed_or_unreadable_card(scrolled, change):
    _, _, (before, after) = scrolled
    original, changed = before.cards[6], after.cards[0]
    if change == "quantity":
        changed = replace(changed, quantity=4)
    elif change == "missing_quantity":
        changed = replace(changed, quantity=None)
    elif change == "tier":
        changed = replace(changed, tier="T3")
    elif change in {"column", "width", "height"}:
        box = list(changed.box)
        box[{"column": 0, "width": 2, "height": 3}[change]] += 1
        changed = replace(changed, box=tuple(box))
    elif change == "different_item":
        # General Amulet and General Bag share the same quantity and frame.
        changed = replace(changed, icon=after.cards[1].icon)
    elif change == "invalid_icon":
        changed = replace(changed, icon=b"not an image")
    else:
        image = cv2.imdecode(np.frombuffer(changed.icon, np.uint8), cv2.IMREAD_UNCHANGED)
        if change == "artwork":
            image[20:35, 30:50, :3] = 0
        else:
            image[20:35, 30:50, 3] = 0
        changed = replace(changed, icon=lr.encode(image))
    assert not lr.same_scrolled_grid_card(original, changed)
    assert lr.scrolled_grid_overlap(before.cards, (changed,) + after.cards[1:]) == 0


def test_grid_overlap_needs_multiple_cards_in_order_with_common_upward_motion(scrolled):
    _, _, (before, after) = scrolled
    assert lr.scrolled_grid_overlap(before.cards[-1:], after.cards[5:]) == 0
    assert lr.scrolled_grid_overlap(before.cards, after.cards[1:2] + after.cards[:1]
                                    + after.cards[2:]) == 0
    first = after.cards[0]
    skewed = replace(first, box=(first.box[0], first.box[1] + 2, *first.box[2:]))
    assert lr.scrolled_grid_overlap(before.cards, (skewed,) + after.cards[1:]) == 0
    assert lr.scrolled_grid_overlap(after.cards[:6], before.cards[-6:]) == 0


def test_ambiguous_repeated_grid_rows_do_not_establish_overlap(scrolled):
    _, _, (before, _) = scrolled
    original = before.cards[6]
    previous = tuple(replace(original, box=(328, y, 110, 89)) for y in (100, 200, 300))
    current = tuple(replace(original, box=(328, y, 110, 89)) for y in (0, 100, 200))
    # Both two-card and three-card overlaps are possible; do not choose one.
    assert lr.scrolled_grid_overlap(previous, current) == 0


def test_reader_logs_all_thirteen_items_once_after_scrolled_overlap(scrolled, tmp_path):
    _, pngs, pages = scrolled
    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    state_dir=tmp_path / "state", run_dir=tmp_path / "runs")
    directory = config.run_dir / "reader"
    journal = Journal(directory, lambda: 0., 0.)
    evidence = directory / "receipt.png"
    evidence.write_bytes(pngs[0])
    runner = SimpleNamespace(config=config, clock=lambda: 0., journal=journal,
                             task="spend_ap")
    expected_names = [f"Loot {i}" for i in range(12)] + ["Credit Points"]
    identity = {card.icon: expected_names[i] for i, card in enumerate(pages[0].cards)}
    identity.update({card.icon: expected_names[i + 6]
                     for i, card in enumerate(pages[1].cards)})

    class ReplayReader(lr.ReceiptReader):
        """Replay real parsed cards; no device or OCR timing is simulated."""

        def __init__(self):
            super().__init__(runner, None, evidence)
            self.position = 0
            self.inspected = []

        def capture(self):
            png = pngs[self.position] if self.position < 2 else b"returned sweep"
            return Capture(png, 0., config.package)

        def read(self, cap):
            parsed = pages[pngs.index(cap.png)] if cap.png in pngs else lr.Page("sweep")
            self.observed = (cap.png, parsed)
            return parsed

        def revalidate(self, cap, *, force=False):
            return cap

        def pan(self, cap, kind, left):
            self.position = max(0, self.position - 1) if left else min(1, self.position + 1)
            cap = self.capture()
            return cap, self.read(cap)

        def inspect_card(self, cap, card, kind):
            name = identity[card.icon]
            self.inspected.append(name)
            return cap, name

        def input(self, cap, target, *, end=None):
            assert target == (640, 533) and self.position == 1 and end is None
            self.position = 2

    try:
        reader = ReplayReader()
        result = reader.run()
        assert reader.position == 2
        assert result["items_complete"]
        assert reader.inspected == expected_names
        assert [item["name"] for item in result["items"]] == expected_names
        assert [item["quantity"] for item in result["items"]] == [
            1, 2, 2, 1, 1, 1, 3, 3, 4, 1, 1, 1, 1187,
        ]
    finally:
        journal.close()


def test_scroll_fixtures_exclude_account_and_background(scrolled):
    _, pngs, _ = scrolled
    for png in pngs:
        image = decode_frame(png).copy()
        image[114:604, 314:964] = 0
        assert not np.any(image)
