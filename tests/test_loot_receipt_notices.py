"""Task-completion toasts must leave before a reward input is considered."""

from pathlib import Path

import cv2
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import StartupVision, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


def test_real_hard_third_sweep_notice_is_transient_not_receipt_identity():
    vision = StartupVision()
    clear, before, after = [(FIXTURES / f"loot-sweep-task-notice-{suffix}.png").read_bytes()
                            for suffix in ("clear", "before", "after")]
    assert not lr.task_notice_visible(decode_frame(clear))
    assert lr.task_notice_visible(decode_frame(before))
    assert lr.task_notice_visible(decode_frame(after))
    parsed = lr.page(clear, vision)
    assert parsed.kind == "sweep"
    assert [c.quantity for c in parsed.cards] == [3, 1263]
    # Do not relax the heading guard to accept the toast. Capture waits for it.
    assert not lr.same_receipt_view(clear, before, parsed, vision=vision)
    assert not lr.same_receipt_view(before, after, parsed, vision=vision)
    assert lr.same_sweep_below_notice(before, clear)
    assert lr.same_sweep_below_notice(after, clear)
    assert not lr.same_sweep_below_notice(clear, before)
    assert not lr.same_sweep_below_notice(before, after)


@pytest.mark.parametrize("box", [(550, 103, 50, 20), (1040, 80, 40, 30),
                                (500, 470, 25, 20), (870, 505, 30, 15),
                                (590, 593, 70, 25)])
def test_initial_notice_reference_cannot_hide_changed_heading_control_or_rewards(box):
    before = (FIXTURES / "loot-sweep-task-notice-before.png").read_bytes()
    clean = decode_frame((FIXTURES / "loot-sweep-task-notice-clear.png").read_bytes())
    x, y, w, h = box
    clean[y:y+h, x:x+w] = (0, 0, 255)
    assert not lr.same_sweep_below_notice(before, cv2.imencode(".png", clean)[1].tobytes())


@pytest.mark.parametrize("name", ["loot-full-list", "loot-sweep", "task-rewards-receipt-end"])
def test_receipt_content_does_not_look_like_a_task_notice(name):
    assert not lr.task_notice_visible(decode_frame((FIXTURES / f"{name}.png").read_bytes()))
