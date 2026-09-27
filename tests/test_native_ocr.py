"""Native game OCR retains original detail while actions use canonical boxes."""

import math
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ba_automator.vision import (
    StartupVision, VisionError, Word, decode_frame, decode_native_frame,
    native_game_region, read_game_region, read_game_words,
)


def png(image):
    encoded, data = cv2.imencode('.png', image)
    assert encoded
    return data.tobytes()


def detailed_frame(width, height):
    image = np.full((height, width, 3), 240, dtype=np.uint8)
    # These one-pixel strokes do not survive a normalize-then-enlarge detour.
    image[height // 2:height // 2 + 31, width // 2:width // 2 + 43:2] = (3, 71, 190)
    return image


@pytest.mark.parametrize('width,height', [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)])
def test_game_ocr_sees_original_pixels_but_returns_canonical_boxes(width, height):
    original = detailed_frame(width, height)
    scale = width / 1280
    box = (round(100 * scale) + 1, round(80 * scale) + 1,
           round(200 * scale) - 1, round(115 * scale) - 1)

    def read(frame):
        np.testing.assert_array_equal(frame, original)
        return [Word('Lv.88', .99, box)]

    words = read_game_words(png(original), SimpleNamespace(read=read))
    expected = (math.floor(box[0] / scale), math.floor(box[1] / scale),
                math.ceil(box[2] / scale), math.ceil(box[3] / scale))
    assert words == [Word('Lv.88', .99, expected)]
    assert words[0].center == ((expected[0] + expected[2]) // 2,
                               (expected[1] + expected[3]) // 2)


@pytest.mark.parametrize('width,height', [(1920, 1080), (2560, 1440)])
def test_native_region_uses_native_pixels_and_returns_region_local_words(width, height):
    original = detailed_frame(width, height)
    bounds = (639, 359, 683, 390)
    scale = width / 1280
    left, top, right, bottom = (round(value * scale) for value in bounds)
    crop = native_game_region(original, bounds)
    np.testing.assert_array_equal(crop, original[top:bottom, left:right])
    assert np.shares_memory(crop, original)
    read_box = (2, 3, right - left - 2, bottom - top - 3)

    def read(frame):
        np.testing.assert_array_equal(frame, crop)
        return [Word('88', .97, read_box)]

    actual = read_game_region(png(original), SimpleNamespace(read=read), bounds)
    expected = (
        math.floor((read_box[0] + left) / scale - bounds[0]),
        math.floor((read_box[1] + top) / scale - bounds[1]),
        math.ceil((read_box[2] + left) / scale - bounds[0]),
        math.ceil((read_box[3] + top) / scale - bounds[1]),
    )
    assert actual == [Word('88', .97, expected)]


@pytest.mark.parametrize('bounds', [(-1, 0, 50, 50), (0, 0, 1281, 50), (4, 4, 4, 8),
                                   (0, 700, 10, 721), (False, 0, 50, 50)])
def test_native_region_rejects_unreviewed_out_of_frame_bounds(bounds):
    with pytest.raises(VisionError):
        native_game_region(np.zeros((1440, 2560, 3), np.uint8), bounds)


def test_game_ocr_rejects_arbitrary_crop_before_reading_it():
    reader = SimpleNamespace(read=lambda _: pytest.fail('unsupported game image reached OCR'))
    with pytest.raises(VisionError):
        read_game_words(png(np.zeros((1080, 1440, 3), np.uint8)), reader)


def test_startup_keeps_templates_canonical_and_text_native(monkeypatch):
    original = detailed_frame(2560, 1440)
    encoded = png(original)
    vision = object.__new__(StartupVision)
    vision.assets = []
    seen = []

    def read(frame):
        np.testing.assert_array_equal(frame, original)
        seen.append('native')
        return [Word('Cafe', .99, (80, 1130, 200, 1200))]

    def matches(frame):
        np.testing.assert_array_equal(frame, decode_frame(encoded))
        seen.append('canonical')
        return {'home_left': (74, 288), 'home_right': (1160, 665)}

    vision.read = read
    vision.matches = matches
    result = vision.analyze(encoded)
    assert seen == ['native', 'canonical']
    assert result.state == 'home'
    np.testing.assert_array_equal(decode_native_frame(encoded), original)


@pytest.mark.parametrize('width,height', [(1920, 1080), (2560, 1440)])
def test_tactical_level_variants_enlarge_native_strokes_without_downsample_detour(width, height):
    from ba_automator.tactical_vision import _level_crops

    native = detailed_frame(width, height)
    canonical = decode_frame(png(native))
    x, y = 639, 359
    actual = _level_crops(canonical, x, y, native_frame=native)
    expected = cv2.resize(native_game_region(native, (x, y, x + 34, y + 14)),
                          (34 * 4, 14 * 4), interpolation=cv2.INTER_CUBIC)
    np.testing.assert_array_equal(actual[0], expected)
    assert len(actual) == 16
    assert not np.array_equal(actual[0], _level_crops(canonical, x, y)[0])


@pytest.mark.parametrize('width,height', [(1920, 1080), (2560, 1440)])
@pytest.mark.parametrize('kind', ['roster', 'assistant', 'stars'])
def test_formation_digit_batches_enlarge_original_native_pixels(width, height, kind):
    from ba_automator.assault_assistant_vision import read_assistant_metadata
    from ba_automator.assault_vision import STAR_DIGITS, formation_stars
    from ba_automator.tactical_formation import COLUMNS, read_roster_levels

    if kind == 'roster':
        bounds = (COLUMNS[0]+37, 212, COLUMNS[0]+66, 231)
        output_size, strip_bounds = (116, 76), (70, 45, 186, 121)
        invoke = read_roster_levels
    elif kind == 'assistant':
        bounds = (574+38, 240+12, 574+57, 240+32)
        output_size, strip_bounds = (95, 100), (70, 45, 165, 145)

        def invoke(frame, reader, **kwargs):
            return read_assistant_metadata(frame, reader, [(574, 240, 681, 411)], **kwargs)
    else:
        bounds = STAR_DIGITS[0]
        output_size, strip_bounds = (56, 56), (50, 35, 106, 91)
        invoke = formation_stars

    native = np.full((height, width, 3), 240, np.uint8)
    # Fine grayscale strokes avoid changing star-color classification while
    # demonstrating that the digit batch kept sub-canonical image detail.
    region = native_game_region(native, bounds)
    region[:, ::2] = 10
    canonical = decode_frame(png(native))
    batches = []
    reader = SimpleNamespace(read=lambda image: batches.append(image.copy()) or [])
    invoke(canonical, reader, native_frame=native)
    x1, y1, x2, y2 = strip_bounds
    actual_batch = batches.pop()
    if kind == 'roster':
        # The roster uses a fixed bounded composite canvas. Compare the complete
        # batch so its final resize cannot hide an earlier 720p downsample.
        from ba_automator.tactical_formation import COLUMNS
        expected = np.full((2160, 300, 3), 255, np.uint8)
        for index, x in enumerate(COLUMNS):
            for part, (a, b, c, d, scale) in enumerate(((x+37, 212, x+66, 231, 4),
                                                      (x+38, 213, x+67, 232, 5))):
                crop = cv2.resize(native_game_region(native, (a, b, c, d)),
                                  ((c-a)*scale, (d-b)*scale))
                y = (index*2+part)*180+45
                expected[y:y+crop.shape[0], 70:70+crop.shape[1]] = crop
        np.testing.assert_array_equal(actual_batch, cv2.resize(expected, (288, 1984)))
    else:
        np.testing.assert_array_equal(actual_batch[y1:y2, x1:x2], cv2.resize(region, output_size))
    invoke(canonical, reader)
    assert not np.array_equal(actual_batch, batches.pop())
