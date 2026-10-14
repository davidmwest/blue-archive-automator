"""The first-visit FAQ overlay is closed only when its panel is recognized."""

import cv2
import numpy as np
import pytest

from ba_automator.config import Config
from ba_automator.shop_runtime import ShopRunner
from ba_automator.shop_vision import ShopScreen, faq_close
from ba_automator.vision import Word


def overlay(*, header=(246, 243, 240)):
    # A dimmed mode screen with the white FAQ panel drawn over it.
    frame = np.full((720, 1280, 3), (70, 60, 50), np.uint8)
    cv2.rectangle(frame, (230, 105), (1050, 615), (238, 236, 234), -1)
    cv2.rectangle(frame, (230, 105), (1050, 160), header, -1)
    return frame


def heading(text='FAQ', box=(610, 118, 670, 148), confidence=0.99):
    return Word(text, confidence, box)


def test_centered_heading_on_white_header_targets_the_close_button():
    assert faq_close(overlay(), [heading()]) == (1017, 133)


@pytest.mark.parametrize('words', [
    [],
    [heading(confidence=0.80)],
    [heading(text='FAQs')],
    [heading(box=(1100, 20, 1160, 50))],          # a mode's own FAQ button
    [heading(), heading(box=(600, 110, 660, 140))],
])
def test_other_headings_do_not_match(words):
    assert faq_close(overlay(), words) is None


def test_heading_without_the_white_panel_does_not_match():
    assert faq_close(overlay(header=(70, 60, 50)), [heading()]) is None


class Reader:
    def __init__(self, words):
        self.words = words

    def read(self, frame):
        return self.words


class Device:
    def __init__(self, frames):
        self.frames, self.taps = list(frames), []

    def foreground_package(self):
        return 'com.nexon.bluearchive'

    def screenshot(self):
        ok, png = cv2.imencode('.png', self.frames.pop(0) if len(self.frames) > 1 else self.frames[0])
        assert ok
        return png.tobytes()

    def tap(self, x, y, *, deadline=None, monotonic=None):
        self.taps.append((x, y))
        return True


class Vision:
    """Report the awaited screen only once the overlay frame is gone."""

    def analyze(self, png, *, billing=False):
        frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        return ShopScreen('menu' if frame[120, 300, 0] < 200 else 'unknown')


def runner(tmp_path, frames, words):
    config = Config(serial='127.0.0.1:5595', package='com.nexon.bluearchive',
                    run_dir=tmp_path / 'runs', lock_dir=tmp_path / 'locks',
                    state_dir=tmp_path / 'state')
    device = Device(frames)
    instance = ShopRunner(config, device, Reader(words), vision=Vision(), sleep=lambda _: None)
    return instance, device


def test_wait_closes_the_overlay_and_then_sees_the_screen(tmp_path):
    clear = np.full((720, 1280, 3), (70, 60, 50), np.uint8)
    instance, device = runner(tmp_path, [overlay(), clear], [heading()])
    try:
        assert instance.wait('menu').screen.kind == 'menu'
    finally:
        instance.journal.close()
    assert device.taps == [(1017, 133)]


def test_wait_does_not_tap_an_unrecognized_screen(tmp_path):
    instance, device = runner(tmp_path, [overlay()], [])
    ticks = iter(range(0, 1000))
    instance.clock = lambda: next(ticks) * 3.0
    try:
        with pytest.raises(Exception, match='did not reach menu'):
            instance.wait('menu', timeout=20)
    finally:
        instance.journal.close()
    assert device.taps == []
