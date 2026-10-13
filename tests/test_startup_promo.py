"""A startup promotion's tinted corner X needs its label and its outline."""

from importlib.resources import files
import json

import cv2
import numpy as np
import pytest

from ba_automator.vision import StartupVision, Word, startup_promo_close


OFFSET = (23, 15)  # where the live banners draw the X inside the asset's search region


@pytest.fixture
def anchors():
    root = files("ba_automator").joinpath("assets")
    specs = json.loads(root.joinpath("startup.json").read_text(encoding="utf-8"))
    return [(spec, cv2.imdecode(np.frombuffer(root.joinpath(spec["file"]).read_bytes(),
                                            dtype=np.uint8), cv2.IMREAD_COLOR))
            for spec in specs if spec["name"] == "startup_close"]


def label(text="Don't show again today", box=(65, 667, 246, 689), confidence=0.99):
    return Word(text, confidence, box)


def expected(anchors):
    spec, template = anchors[0]
    return (spec["region"][0] + OFFSET[0] + template.shape[1] // 2,
            spec["region"][1] + OFFSET[1] + template.shape[0] // 2)


def banner(anchors, *, background, stroke, with_x=True):
    # Synthetic artwork with the sanitized asset's own X strokes recolored, so the
    # test needs no promotional screenshot.
    spec, template = anchors[0]
    yy, xx = np.indices((720, 1280))
    image = np.stack([np.clip(base + (xx // 160 + yy // 120) % 3 * 4, 0, 255) for base in background],
                     axis=2).astype(np.uint8)
    if with_x:
        strokes = np.min(template, axis=2) >= 160
        left, top = spec["region"][0] + OFFSET[0], spec["region"][1] + OFFSET[1]
        image[top:top + template.shape[0], left:left + template.shape[1]][strokes] = stroke
    return image


BRIGHT = {"background": (243, 240, 238), "stroke": (150, 96, 104)}
DARK = {"background": (96, 60, 34), "stroke": (255, 255, 255)}


@pytest.mark.parametrize("style", [BRIGHT, DARK])
def test_label_and_tinted_x_provide_close_target(anchors, style):
    target = startup_promo_close(banner(anchors, **style), [label()], anchors)
    assert target is not None
    assert abs(target[0] - expected(anchors)[0]) <= 2
    assert abs(target[1] - expected(anchors)[1]) <= 2


def test_label_without_x_does_not_authorize_close(anchors):
    assert startup_promo_close(banner(anchors, with_x=False, **BRIGHT), [label()], anchors) is None


def test_x_without_label_does_not_authorize_close(anchors):
    assert startup_promo_close(banner(anchors, **BRIGHT), [], anchors) is None


@pytest.mark.parametrize("word", [
    label(box=(550, 340, 730, 362)),      # a dialog checkbox, not the banner's corner label
    label(confidence=0.70),
    label(text="Show again today"),
])
def test_other_labels_do_not_authorize_close(anchors, word):
    assert startup_promo_close(banner(anchors, **BRIGHT), [word], anchors) is None


def test_promotion_fallback_identifies_its_detector(anchors):
    vision = StartupVision.__new__(StartupVision)
    vision.assets = anchors
    vision.read = lambda frame: [label()]
    vision.matches = lambda frame: {}
    ok, png = cv2.imencode(".png", banner(anchors, **BRIGHT))
    assert ok
    observation = vision.analyze(png.tobytes())
    assert observation.state == "popup"
    assert observation.detector == "startup_promo"
    assert abs(observation.target[0] - expected(anchors)[0]) <= 2
