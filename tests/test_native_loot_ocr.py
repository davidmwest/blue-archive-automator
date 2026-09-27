"""Receipt text retains native detail while its layout stays in canonical pixels."""

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import Word, decode_frame, native_game_region


def detailed_frame(width, height):
    image = np.full((height, width, 3), 240, dtype=np.uint8)
    image[height // 2:height // 2 + 31, width // 2:width // 2 + 43:2] = (3, 71, 190)
    return image


def padded_crop(native, bounds, scale):
    left, top, right, bottom = bounds
    large = cv2.resize(native_game_region(native, bounds),
                       ((right - left) * scale, (bottom - top) * scale))
    return cv2.copyMakeBorder(large, 25, 25, 25, 25,
                             cv2.BORDER_CONSTANT, value=(255, 255, 255))


@pytest.mark.parametrize('size', [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)])
@pytest.mark.parametrize('scale', [2, 3, 4])
def test_loot_crop_preserves_native_strokes_and_legacy_padded_coordinates(size, scale):
    native = detailed_frame(*size)
    bounds = (639, 359, 683, 390)
    observed = []
    words = [Word('x12', .99, (25, 25, 80, 60))]

    def read(image):
        observed.append(image)
        return words

    assert lr.read_game_crop(SimpleNamespace(read=read), native, bounds, scale) is words
    np.testing.assert_array_equal(observed[0], padded_crop(native, bounds, scale))
    assert observed[0].shape == (31 * scale + 50, 44 * scale + 50, 3)
    if size != (1280, 720):
        canonical = cv2.resize(native, (1280, 720), interpolation=cv2.INTER_AREA)
        assert not np.array_equal(observed[0], padded_crop(canonical, bounds, scale))


@pytest.mark.parametrize('field', ['name', 'quantity'])
def test_reward_fields_use_native_crop_without_weakening_confidence(field):
    native = detailed_frame(2560, 1440)
    canonical = cv2.resize(native, (1280, 720), interpolation=cv2.INTER_AREA)
    card = (600, 350, 140, 210)
    bounds, scale, text, expected = (
        ((605, 356, 735, 400), 3, 'Heat Pack Blueprint', 'Heat Pack Blueprint')
        if field == 'name' else ((608, 512, 732, 551), 2, 'x12', 12)
    )
    # Put fine strokes inside either field, including the quantity's distant row.
    left, top, _, _ = bounds
    native[top * 2:top * 2 + 18, left * 2:left * 2 + 23:2] = (2, 70, 190)
    seen = []

    def read(image):
        seen.append(image)
        return [Word(text, .99, (25, 25, 100, 55))]

    reader = SimpleNamespace(read=read)
    actual = (lr.reward_name(reader, canonical, card, native=native) if field == 'name'
              else lr.reward_quantity(reader, canonical, card, native=native))
    assert actual == expected
    np.testing.assert_array_equal(seen[0], padded_crop(native, bounds, scale))

    reader.read = lambda _: [Word(text, .64, (25, 25, 100, 55))]
    assert (lr.reward_name(reader, canonical, card, native=native) if field == 'name'
            else lr.reward_quantity(reader, canonical, card, native=native)) is None


def test_tooltip_reads_native_name_then_maps_back_to_canonical_tooltip():
    fixture = Path(__file__).parent / 'fixtures' / 'loot-tooltip-0.png'
    canonical = decode_frame(fixture.read_bytes())
    native = cv2.resize(canonical, (2560, 1440), interpolation=cv2.INTER_NEAREST)
    x, y, w, _ = lr.tooltip_boxes(canonical)[0]
    words = [Word('Heat Pack Blueprint', .99, (x + 30, y + 10, x + w - 12, y + 28)),
             Word('Owned: 14', .99, (x + 30, y + 35, x + 120, y + 52))]
    native_words = [Word(word.text, word.confidence, tuple(v * 2 for v in word.box))
                    for word in words]
    observed = []

    def read(image):
        observed.append(image)
        return native_words

    assert lr.read_tooltip(native, SimpleNamespace(read=read)) == 'Heat Pack Blueprint'
    assert observed[0].shape == native.shape
    decoration = cv2.inRange(cv2.cvtColor(native, cv2.COLOR_BGR2HSV),
                              (75, 100, 180), (105, 255, 255)) != 0
    assert np.all(observed[0][decoration] == 255)
    np.testing.assert_array_equal(observed[0][~decoration], native[~decoration])
