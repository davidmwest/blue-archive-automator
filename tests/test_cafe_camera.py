"""Camera measurements use scene motion, never a particular furniture layout."""

import cv2
import numpy as np
import pytest
from pathlib import Path

from ba_automator.cafe_camera import measure_camera_displacement


def test_live_cafe_edge_drift_is_measured_as_perspective_not_stationary():
    fixtures = Path(__file__).parent / "fixtures"
    before, after = [
        (fixtures / f"cafe-edge-drift-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    diagnostics = {}
    motion = measure_camera_displacement(before, after, diagnostics=diagnostics)
    assert motion is not None and np.allclose(motion, (60, -31), atol=2)
    assert diagnostics["method"] == "affine"


def room(seed):
    random = np.random.default_rng(seed)
    frame = np.full((720, 1280, 3), (207, 221, 234), dtype=np.uint8)
    # Repeated room structure plus distinct, arbitrarily arranged decorations.
    for start in range(-500, 1500, 80):
        cv2.line(frame, (start, 110), (start + 700, 700), (168, 182, 197), 2)
        cv2.line(frame, (start, 700), (start + 700, 110), (186, 194, 207), 1)
    for index in range(95):
        x, y = (int(value) for value in random.integers((110, 115), (1165, 610)))
        width, height = (int(value) for value in random.integers((20, 15), (75, 55)))
        color = tuple(int(value) for value in random.integers(35, 230, size=3))
        cv2.rectangle(frame, (x, y), (x + width, y + height), color, -1)
        cv2.rectangle(frame, (x, y), (x + width, y + height), (25, 40, 60), 2)
        cv2.circle(frame, (x + width // 2, y + height // 2), 5, (235, 230, 210), -1)
        cv2.putText(frame, str(index), (x + 2, y + height - 3), cv2.FONT_HERSHEY_SIMPLEX,
                    .34, (255, 255, 255), 1, cv2.LINE_AA)
    return frame


def hud(frame):
    frame = frame.copy()
    for left, top, right, bottom in ((0, 0, 1280, 130), (0, 585, 1280, 720),
                                    (0, 135, 125, 575), (1165, 135, 1280, 575)):
        panel = np.full((bottom - top, right - left, 3), 245, dtype=np.uint8)
        for y in range(18, bottom - top, 22):
            cv2.putText(panel, "HUD 123 456 MENU " * 12, (3, y),
                        cv2.FONT_HERSHEY_SIMPLEX, .45, (25, 25, 25), 1, cv2.LINE_AA)
        frame[top:bottom, left:right] = panel
    return frame


def translate(frame, dx, dy):
    return cv2.warpAffine(frame, np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32),
                          (1280, 720), borderMode=cv2.BORDER_CONSTANT,
                          borderValue=(207, 221, 234))


def students(frame, seed):
    frame = frame.copy()
    random = np.random.default_rng(seed)
    for index in range(3):
        x, y = (int(value) for value in random.integers((300, 240), (990, 475)))
        color = tuple(int(value) for value in random.integers(30, 220, size=3))
        cv2.circle(frame, (x, y), 28, color, -1)
        cv2.ellipse(frame, (x, y + 35), (21, 33), index * 17, 0, 360, color, -1)
        cv2.circle(frame, (x - 9, y - 2), 4, (255, 255, 255), -1)
        cv2.circle(frame, (x + 9, y - 2), 4, (255, 255, 255), -1)
        cv2.putText(frame, str(seed), (x - 16, y + 42), cv2.FONT_HERSHEY_SIMPLEX,
                    .6, (255, 255, 255), 2, cv2.LINE_AA)
    return frame


@pytest.mark.parametrize("seed", [3, 29, 71])
@pytest.mark.parametrize("movement", [(112, 0), (0, -139), (-84, 58), (2, -3),
                                      (-475, 112), (0, -305)])
def test_scene_translation_across_arbitrary_layouts_ignores_fixed_hud(seed, movement):
    before = room(seed)
    after = translate(before, *movement)
    measured = measure_camera_displacement(hud(before), hud(after))
    assert measured is not None
    assert np.allclose(measured, movement, atol=2.0), measured


def test_pan_is_measured_despite_moving_students_and_new_speech_bubble():
    background = room(84)
    before = students(background, 21)
    after = students(translate(background, 0, -120), 57)
    cv2.rectangle(after, (450, 250), (780, 355), (250, 250, 250), -1)
    cv2.rectangle(after, (450, 250), (780, 355), (55, 55, 55), 2)
    cv2.putText(after, "Animated speech", (470, 310), cv2.FONT_HERSHEY_SIMPLEX,
                .8, (20, 20, 20), 2, cv2.LINE_AA)
    measured = measure_camera_displacement(hud(before), hud(after))
    assert measured is not None
    assert np.allclose(measured, (0, -120), atol=2.0), measured


def test_stationary_room_with_animated_students_is_supported_by_background():
    background = room(17)
    measured = measure_camera_displacement(hud(students(background, 11)),
                                           hud(students(background, 23)))
    assert measured is not None
    assert np.allclose(measured, (0, 0), atol=.75), measured


def test_partial_occlusion_preserves_distributed_scene_measurement():
    before = room(31)
    after = translate(before, -96, 0)
    after[190:530, 755:1130] = 240
    measured = measure_camera_displacement(hud(before), hud(after))
    assert measured is not None
    assert np.allclose(measured, (-96, 0), atol=2.0), measured


def test_blank_interior_with_high_detail_fixed_hud_is_unknown_not_stationary():
    before = np.full((720, 1280, 3), 185, dtype=np.uint8)
    after = np.full((720, 1280, 3), 185, dtype=np.uint8)
    assert measure_camera_displacement(hud(before), hud(after)) is None


@pytest.mark.parametrize("movement", [(0, 0), (85, -25)])
def test_features_concentrated_on_one_patch_do_not_establish_camera_motion(movement):
    random = np.random.default_rng(41)
    before = np.full((720, 1280, 3), 185, dtype=np.uint8)
    before[285:380, 500:635] = random.integers(0, 256, (95, 135, 3), dtype=np.uint8)
    after = translate(before, *movement)
    assert measure_camera_displacement(hud(before), hud(after)) is None


def test_two_competing_distributed_motions_are_ambiguous():
    before = room(28)
    after = before.copy()
    moved = translate(before, 70, 0)
    after[:, 640:] = moved[:, 640:]
    assert measure_camera_displacement(hud(before), hud(after)) is None


def test_static_outside_background_cannot_masquerade_as_stopped_camera():
    before = room(41)
    after = translate(before, 0, -140)
    # Some views expose a fixed outside-room background beside the moving scene.
    # Even if that highly textured strip dominates ORB, it cannot prove no motion.
    background = np.random.default_rng(22).integers(0, 256, (420, 175, 3), dtype=np.uint8)
    before[145:565, 970:1145] = background
    after[145:565, 970:1145] = background
    measured = measure_camera_displacement(hud(before), hud(after))
    assert measured is None or np.linalg.norm(measured) > 20


def test_unrelated_layouts_do_not_establish_stationarity():
    assert measure_camera_displacement(hud(room(5)), hud(room(913))) is None


def test_png_gray_and_bgra_inputs_give_consistent_results():
    before = hud(room(7))
    after = hud(translate(room(7), 48, -31))
    png_before = cv2.imencode(".png", before)[1].tobytes()
    png_after = cv2.imencode(".png", after)[1].tobytes()
    from_png = measure_camera_displacement(png_before, png_after)
    from_gray = measure_camera_displacement(cv2.cvtColor(before, cv2.COLOR_BGR2GRAY),
                                            cv2.cvtColor(after, cv2.COLOR_BGR2GRAY))
    from_bgra = measure_camera_displacement(cv2.cvtColor(before, cv2.COLOR_BGR2BGRA),
                                            cv2.cvtColor(after, cv2.COLOR_BGR2BGRA))
    assert from_png is not None
    assert np.allclose(from_png, (48, -31), atol=2)
    assert from_png == from_gray == from_bgra


@pytest.mark.parametrize("bad", [b"", b"not a PNG", np.zeros((1080, 1920, 3), dtype=np.uint8),
                                 np.zeros((720, 1280, 3), dtype=np.float32),
                                 np.zeros((720, 1280, 2), dtype=np.uint8), None])
def test_invalid_frames_never_become_zero_motion(bad):
    assert measure_camera_displacement(bad, room(4)) is None
    assert measure_camera_displacement(room(4), bad) is None


def test_measurement_is_deterministic_across_repeated_calls():
    before = hud(room(89))
    after = hud(translate(room(89), -70, 95))
    results = [measure_camera_displacement(before, after) for _ in range(3)]
    assert results[0] is not None
    assert results[0] == results[1] == results[2]


@pytest.mark.parametrize("seed", [11, 34, 91])
@pytest.mark.parametrize("matrix", [
    [[.975, .065, 100], [-.0002, .960, -15]],
    [[.96, -.06, -75], [.02, 1.01, 35]],
])
def test_small_perspective_change_during_pan_is_not_unknown(seed, matrix):
    before = room(seed)
    matrix = np.asarray(matrix, dtype=np.float32)
    after = cv2.warpAffine(before, matrix, (1280, 720), borderValue=(207, 221, 234))
    diagnostics = {}
    measured = measure_camera_displacement(hud(before), hud(after), diagnostics=diagnostics)
    assert measured is not None, diagnostics
    # Every point in the scene follows this known transform. The measured median
    # must lie inside its true range, irrespective of where furniture sits.
    corners = np.float32([[140, 135], [1150, 135], [140, 575], [1150, 575]])
    flow = corners @ matrix[:, :2].T + matrix[:, 2] - corners
    assert np.all(np.asarray(measured) >= flow.min(axis=0) - 2), diagnostics
    assert np.all(np.asarray(measured) <= flow.max(axis=0) + 2), diagnostics
    assert diagnostics["reason"] == "accepted"


def test_affine_fallback_is_deterministic_and_reports_why_translation_failed():
    before = room(11)
    matrix = np.float32([[.96, -.06, -75], [.02, 1.01, 35]])
    after = cv2.warpAffine(before, matrix, (1280, 720), borderValue=(207, 221, 234))
    results = []
    for _ in range(3):
        diagnostics = {"stale": "must be replaced"}
        results.append(measure_camera_displacement(hud(before), hud(after), diagnostics=diagnostics))
        assert diagnostics["method"] == "affine"
        assert diagnostics["translation_rejection"] == "translation_consensus_too_weak"
        assert diagnostics["affine_ratio"] > .9
        assert "stale" not in diagnostics
    assert results[0] == results[1] == results[2]


def test_large_scale_change_is_rejected_even_with_strong_affine_consensus():
    before = room(11)
    matrix = np.float32([[.62, 0, 180], [0, .62, 100]])
    after = cv2.warpAffine(before, matrix, (1280, 720), borderValue=(207, 221, 234))
    diagnostics = {}
    assert measure_camera_displacement(hud(before), hud(after), diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "affine_geometry_not_camera_pan"
    assert diagnostics["affine_ratio"] > .9


def test_affine_zoom_about_scene_center_cannot_certify_stationary():
    before = room(19)
    matrix = np.float32([[.97, 0, 640 * .03], [0, .97, 355 * .03]])
    after = cv2.warpAffine(before, matrix, (1280, 720), borderValue=(207, 221, 234))
    diagnostics = {}
    result = measure_camera_displacement(hud(before), hud(after), diagnostics=diagnostics)
    assert result is None or np.linalg.norm(result) > 4, diagnostics


def test_diagnostics_distinguish_missing_features_from_stationary_evidence():
    diagnostics = {}
    blank = np.full((720, 1280, 3), 190, dtype=np.uint8)
    assert measure_camera_displacement(hud(blank), hud(blank), diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "too_few_features"
    stationary = hud(room(31))
    assert measure_camera_displacement(stationary, stationary, diagnostics=diagnostics) == (0, 0)
    assert diagnostics["reason"] == "accepted"
    assert diagnostics["method"] == "translation"
