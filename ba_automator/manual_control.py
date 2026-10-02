"""Conservative, local frame comparison for human taps on the dashboard preview."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .vision import decode_native_frame


@dataclass(frozen=True)
class FrameMatch:
    matches: bool
    reason: str


def compare_tap_frames(displayed: bytes, current: bytes, x: int, y: int) -> FrameMatch:
    """Require similar overall layout and a much closer match at the tap target.

    This is a visual guard, not semantic recognition. Small animated decorations
    can move; a moving target, modal, transition, or changed display must refresh
    first. Both screenshots use canonical coordinates only for comparison.
    """
    before, after = decode_native_frame(displayed), decode_native_frame(current)
    if before.shape != after.shape:
        return FrameMatch(False, "display size changed")
    before, after = [cv2.GaussianBlur(cv2.resize(frame, (1280, 720),
                       interpolation=cv2.INTER_AREA), (5, 5), 0) for frame in (before, after)]
    difference = cv2.absdiff(before, after).max(axis=2)
    target = difference[max(0, y - 36):min(720, y + 37), max(0, x - 36):min(1280, x + 37)]
    # Live2D hair/halos can change a small part of the lobby quite strongly.
    # Limit both the changed area and overall difference; the target below
    # stays stricter than the background.
    if float(np.mean(difference > 18)) > .10 or float(difference.mean()) > 6:
        return FrameMatch(False, "screen changed")
    if float(np.mean(target > 18)) > .04 or float(target.mean()) > 2.5:
        return FrameMatch(False, "tap target changed")
    return FrameMatch(True, "screen matches")
