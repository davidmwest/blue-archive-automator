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


def test_native_partial_cyan_task_progress_waits_for_clean_receipt():
    before, clear = [
        (FIXTURES / f"loot-sweep-task-progress-native-refresh-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    assert lr.task_notice_visible(decode_frame(before))
    assert not lr.task_notice_visible(decode_frame(clear))
    assert lr.same_sweep_below_notice(before, clear)
    assert not lr.same_sweep_below_notice(clear, before)
    # The notice itself still cannot authorize an input using a clean heading.
    assert not lr.same_receipt_view(before, clear, lr.Page("sweep"))
    assert not lr.same_receipt_view(clear, before, lr.Page("sweep"))


def test_native_departing_banner_is_not_yet_the_original_receipt():
    clear, visible, fading = [
        (FIXTURES / f"loot-sweep-task-fading-native-{name}.png").read_bytes()
        for name in ("clear", "visible", "fading")
    ]
    assert lr.task_notice_visible(decode_frame(visible))
    assert not lr.task_notice_visible(decode_frame(fading))
    assert not lr.task_notice_visible(decode_frame(clear))
    assert lr.task_notice_heading(decode_frame(fading)) != lr.task_notice_heading(decode_frame(clear))
    # The strict identity guard still rejects a fading banner. Capture must
    # wait for stable heading pixels; neither color disappearance nor waiting
    # by itself authorizes input into the incomplete view.
    assert not lr.same_receipt_view(clear, fading, lr.Page("sweep"))
    assert lr.same_receipt_view(clear, clear, lr.Page("sweep"))


@pytest.mark.parametrize("box", [(550, 103, 50, 20), (1040, 80, 40, 30),
                                (400, 470, 25, 20), (520, 505, 30, 10),
                                (590, 593, 70, 25)])
def test_native_progress_notice_preserves_clean_receipt_identity(box):
    before = (FIXTURES / "loot-sweep-task-progress-native-refresh-before.png").read_bytes()
    clear = decode_frame(
        (FIXTURES / "loot-sweep-task-progress-native-refresh-after.png").read_bytes()
    )
    x, y, w, h = box
    clear[y:y+h, x:x+w] = (0, 0, 255)
    assert not lr.same_sweep_below_notice(before, cv2.imencode(".png", clear)[1].tobytes())


@pytest.mark.parametrize("mutation", ["no_bar", "not_left_anchored", "bright_panel"])
def test_cyan_task_notice_requires_panel_and_progress_bar(mutation):
    image = decode_frame(
        (FIXTURES / "loot-sweep-task-progress-native-refresh-before.png").read_bytes()
    )
    if mutation == "no_bar":
        image[54:76, 487:794] = 0
    elif mutation == "not_left_anchored":
        image[54:76, 487:500] = 0
    else:
        image[5:49, 420:930] = 255
    assert not lr.task_notice_visible(image)


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
