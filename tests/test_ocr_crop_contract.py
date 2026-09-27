"""OCR crops keep their own pixel coordinates, independent of game resolution."""

from types import SimpleNamespace

import numpy as np
import pytest

from ba_automator.vision import StartupVision, Word


@pytest.mark.parametrize("height,width", [(47, 213), (960, 1600), (1440, 2560)])
def test_read_preserves_arbitrary_crop_pixels_and_coordinates(height, width):
    # Callers crop and sometimes enlarge text before OCR. Even a crop whose size
    # resembles a supported game display must not be normalized a second time.
    crop = np.zeros((height, width, 3), dtype=np.uint8)
    crop[-20:, -40:] = (20, 80, 230)
    left, top, right, bottom = width - 39, height - 19, width - 3, height - 3

    def ocr(frame, *, use_cls):
        assert frame is crop
        assert use_cls is False
        return SimpleNamespace(
            txts=["Rank 24"],
            boxes=[np.array([[left, top], [right, top], [right, bottom], [left, bottom]])],
            scores=[0.98],
        )

    vision = object.__new__(StartupVision)
    vision.ocr = ocr

    assert vision.read(crop) == [Word("Rank 24", 0.98, (left, top, right, bottom))]
