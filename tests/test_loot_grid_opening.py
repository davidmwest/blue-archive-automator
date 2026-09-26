"""Readable Full List cards must stop moving before inspection can begin.

The September 26 Hard 7-1 opening pair retains only the Full List modal.
Account name, balances, experience, and all other game background are masked.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.config import Config
from ba_automator.runtime import Journal, TaskError
from ba_automator.vision import StartupVision, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def opening_frames():
    vision = StartupVision()
    pngs = [(FIXTURES / f"loot-grid-opening-shift-{suffix}.png").read_bytes()
            for suffix in ("before", "after")]
    pages = [lr.page(png, vision) for png in pngs]
    return vision, pngs, pages


def test_readable_opening_cards_still_move_and_fail_the_normal_input_guard(opening_frames):
    vision, (before, after), (first, settled) = opening_frames
    assert first.kind == settled.kind == "grid"
    assert len(first.cards) == len(settled.cards) == 12
    assert [c.quantity for c in first.cards] == [c.quantity for c in settled.cards]
    assert first.cards[0].box[1] - settled.cards[0].box[1] == 2
    assert not lr.same_receipt_view(before, after, first, vision=vision)
    assert lr.same_receipt_view(after, after, settled, vision=vision)
    for png in (before, after):
        masked = decode_frame(png).copy()
        masked[114:604, 314:964] = 0
        assert not np.any(masked)


@pytest.mark.parametrize("outcome", ["settled", "moving", "stale", "foreign"])
def test_opening_waits_through_ocr_for_fresh_stationary_cards_without_input(
    opening_frames, tmp_path, monkeypatch, outcome
):
    vision, (before, after), pages = opening_frames
    state = SimpleNamespace(now=0.0, captures=0, reads=0, inputs=[],
                            foreground="com.nexon.bluearchive")
    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    state_dir=tmp_path / "state", run_dir=tmp_path / "runs")
    directory = config.run_dir / "opening"
    journal = Journal(directory, lambda: state.now, state.now)

    def sleep(seconds):
        state.now += seconds

    def screenshot():
        state.captures += 1
        if outcome == "stale":
            # A stationary screenshot that took too long cannot be accepted.
            state.now += 6
        if outcome == "moving":
            return before if state.captures % 2 else after
        return before if state.captures == 1 else after

    def page(png, unused_vision):
        state.reads += 1
        # Replay independently OCR-read real pages while moving fake time
        # through the expensive read: the next capture is its freshness proof.
        state.now += 1
        if outcome == "foreign":
            state.foreground = "com.android.settings"
        return pages[0] if png == before else pages[1]

    def unexpected_input(*args, **kwargs):
        state.inputs.append(args)
        pytest.fail("Waiting for Full List to settle must not send device input")

    def fail(message):
        raise TaskError(message, directory)

    runner = SimpleNamespace(
        config=config, clock=lambda: state.now, sleep=sleep, journal=journal,
        fail=fail, actions=0,
        device=SimpleNamespace(foreground_package=lambda: state.foreground,
                               screenshot=screenshot, tap=unexpected_input,
                               swipe=unexpected_input),
    )
    monkeypatch.setattr(lr, "page", page)
    reader = lr.ReceiptReader(runner, vision, directory / "receipt.png")
    try:
        initial = reader.capture()
        started = state.now
        if outcome == "settled":
            cap, parsed = reader.opened_grid(initial)
            assert cap.png == after and parsed is pages[1]
            assert cap.is_fresh(state.now)
            assert reader.observed == (after, pages[1])
            assert state.captures == 4 and state.reads == 2
        else:
            message = "Foreground changed" if outcome == "foreign" else "no readable cards"
            with pytest.raises(TaskError, match=message):
                reader.opened_grid(initial)
            if outcome != "foreign":
                assert (directory / "receipt-full-list-unreadable.png").exists()
                assert state.now - started < lr.GRID_ENTRY_TIMEOUT + 15
        assert not state.inputs and runner.actions == 0
    finally:
        journal.close()
