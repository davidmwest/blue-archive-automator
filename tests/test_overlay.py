"""A generic close needs independent home shading, modal, and X evidence."""

from importlib.resources import files
import json

import cv2
import numpy as np
import pytest

from ba_automator.vision import StartupVision, Word, shaded_overlay_close


@pytest.fixture
def anchors():
    root = files("ba_automator").joinpath("assets")
    specs = json.loads(root.joinpath("startup.json").read_text())
    return [(spec, cv2.imdecode(np.frombuffer(root.joinpath(spec["file"]).read_bytes(),
                                            dtype=np.uint8), cv2.IMREAD_COLOR))
            for spec in specs if spec["name"] in {"home_left", "home_right"}]


def label(text, center=(640, 310)):
    x, y = center
    return Word(text, 0.99, (x - 110, y - 12, x + 110, y + 12))


def cross(image, center=(1027, 130), color=(82, 55, 36), size=10):
    x, y = center
    cv2.line(image, (x - size, y - size), (x + size, y + size), color, 3, cv2.LINE_AA)
    cv2.line(image, (x - size, y + size), (x + size, y - size), color, 3, cv2.LINE_AA)


def scene(anchors, *, dim=0.58, with_panel=True, with_x=True):
    # A textured home background with the real, sanitized menu anchors. The modal
    # is synthesized independently, so tests do not need another account screenshot.
    yy, xx = np.indices((720, 1280))
    image = np.stack((80 + xx % 95, 95 + yy % 70, 110 + (xx + yy) % 80), axis=2).astype(np.uint8)
    for spec, template in anchors:
        x1, y1, _, _ = spec["region"]
        image[y1:y1 + template.shape[0], x1:x1 + template.shape[1]] = template
    image = np.rint(image.astype(np.float32) * dim).astype(np.uint8)
    if with_panel:
        cv2.rectangle(image, (320, 100), (1060, 590), (239, 240, 243), -1)
        cv2.putText(image, "ANNOUNCEMENT", (485, 155), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (94, 72, 48), 2, cv2.LINE_AA)
        cv2.line(image, (345, 190), (1035, 190), (194, 196, 202), 2)
        cv2.putText(image, "Welcome back, Sensei!", (435, 325), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (84, 73, 61), 2, cv2.LINE_AA)
    if with_x:
        cross(image)
    return image


@pytest.mark.parametrize("dim", [0.35, 0.58, 0.77])
def test_dimmed_home_and_unique_modal_x_provide_close_target(anchors, dim):
    target = shaded_overlay_close(scene(anchors, dim=dim), [label("Welcome back, Sensei!")], anchors)
    assert target is not None
    assert abs(target[0] - 1027) <= 1
    assert abs(target[1] - 130) <= 1


def test_white_x_on_dark_round_close_button_is_supported(anchors):
    frame = scene(anchors, with_x=False)
    cv2.circle(frame, (1027, 130), 22, (80, 58, 40), -1, cv2.LINE_AA)
    cross(frame, color=(255, 255, 255))
    target = shaded_overlay_close(frame, [], anchors)
    assert target is not None
    assert abs(target[0] - 1027) <= 1
    assert abs(target[1] - 130) <= 1


@pytest.mark.parametrize("options", [
    {"dim": 1.0}, {"dim": 0.9}, {"with_x": False}, {"with_panel": False},
])
def test_shading_x_or_panel_alone_does_not_authorize_close(anchors, options):
    assert shaded_overlay_close(scene(anchors, **options), [], anchors) is None


def test_unknown_background_does_not_authorize_close(anchors):
    frame = scene(anchors)
    frame[660:720, :] = (70, 70, 70)
    assert shaded_overlay_close(frame, [], anchors) is None


def test_two_home_anchors_must_have_consistent_shading(anchors):
    frame = scene(anchors)
    spec, template = anchors[0]
    x1, y1, _, _ = spec["region"]
    frame[y1:y1 + template.shape[0], x1:x1 + template.shape[1]] = np.rint(template * 0.30)
    assert shaded_overlay_close(frame, [], anchors) is None


def test_two_corner_x_candidates_are_ambiguous(anchors):
    frame = scene(anchors)
    cross(frame, center=(978, 150))
    assert shaded_overlay_close(frame, [], anchors) is None


@pytest.mark.parametrize("message", [
    "Purchase this package", "750 pyroxenes", "Credits 100,000", "USD 18.99", "$9.99",
    "Link account", "Password", "Account verification", "Yes", "No", "Confirm", "Cancel",
    "Close", "Continue", "Recruit", "Exchange",
    "Ready to download", "Server under maintenance",
])
def test_sensitive_or_choice_dialogs_are_not_generic_popups(anchors, message):
    assert shaded_overlay_close(scene(anchors), [label(message)], anchors) is None


@pytest.mark.parametrize("symbol", ["plus", "chevron", "diamond", "letter_k"])
def test_other_corner_shapes_are_not_close_controls(anchors, symbol):
    frame = scene(anchors, with_x=False)
    color = (82, 55, 36)
    paths = {
        "plus": [[(1017, 130), (1037, 130)], [(1027, 120), (1027, 140)]],
        "chevron": [[(1017, 120), (1037, 130), (1017, 140)]],
        "diamond": [[(1027, 119), (1038, 130), (1027, 141), (1016, 130), (1027, 119)]],
        "letter_k": [[(1017, 119), (1017, 141)], [(1036, 119), (1017, 130), (1036, 141)]],
    }
    for path in paths[symbol]:
        cv2.polylines(frame, [np.array(path)], False, color, 3, cv2.LINE_AA)
    assert shaded_overlay_close(frame, [], anchors) is None


def test_x_in_ocr_phrase_is_not_a_close_control(anchors):
    assert shaded_overlay_close(scene(anchors), [label("EXTRA", center=(1027, 130))], anchors) is None


def test_generic_fallback_identifies_its_detector(anchors):
    vision = StartupVision.__new__(StartupVision)
    vision.assets = anchors
    vision.read = lambda frame: [label("Welcome back, Sensei!")]
    vision.matches = lambda frame: {}
    ok, png = cv2.imencode(".png", scene(anchors))
    assert ok
    observation = vision.analyze(png.tobytes())
    assert observation.state == "popup"
    assert observation.detector == "shaded_overlay"
    assert observation.target == (1027, 130)


def test_known_handlers_keep_priority_over_generic_fallback(anchors):
    vision = StartupVision.__new__(StartupVision)
    vision.assets = anchors
    vision.read = lambda frame: []
    vision.matches = lambda frame: {"notice_close": (900, 175)}
    ok, png = cv2.imencode(".png", scene(anchors))
    assert ok
    observation = vision.analyze(png.tobytes())
    assert observation.target == (900, 175)
    assert observation.detector == "known"
