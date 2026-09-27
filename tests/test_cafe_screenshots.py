from pathlib import Path
import json

import pytest

from ba_automator.cafe import earnings_amounts, reward_amounts
from ba_automator.cafe_vision import CafeVision, scene_point
from ba_automator.vision import StartupVision, Word, decode_frame, read_game_words

FIXTURES = Path(__file__).parent / "fixtures"


def frame(name):
    return decode_frame((FIXTURES / f"{name}.png").read_bytes())


@pytest.fixture(scope="module")
def vision():
    return CafeVision(StartupVision())


def test_real_markers_and_hud(vision):
    image = frame("cafe_markers")
    assert vision.is_cafe(image)
    markers = vision.markers(image)
    assert len(markers) == 3
    assert all(scene_point(*target) for _, target in markers)
    assert not vision.is_cafe((image * .6).astype("uint8"))


def test_saved_failed_entry_targets_the_cafe_text_label(vision):
    image = frame("cafe-home-label")
    assert vision.startup.analyze((FIXTURES / "cafe-home-label.png").read_bytes()).state == "home"
    assert vision.home_entry_target(image) == (97, 691)
    # The old (100, 659) point was on the protruding cup illustration.
    assert not vision.is_cafe(image)


@pytest.mark.parametrize("obstruction", ["dimmed", "missing_cafe", "missing_campaign"])
def test_cafe_entry_label_requires_both_bright_home_anchors(vision, obstruction):
    image = frame("cafe-home-label")
    if obstruction == "dimmed":
        image = (image * .6).astype("uint8")
    elif obstruction == "missing_cafe":
        image[:, :200] = 0
    else:
        image[:, 1100:] = 0
    assert vision.home_entry_target(image) is None


def test_real_heart_feedback_is_new(vision):
    before, after = frame("cafe_heart_before"), frame("cafe_heart_after")
    assert vision.relationship_feedback(before, after, (651, 278))
    assert not vision.relationship_feedback(before, before, (651, 278))
    assert not vision.relationship_feedback(after, after, (651, 278))


def test_real_earnings_and_receipt_ocr(vision):
    assert earnings_amounts(vision.words(frame("cafe_earnings"))) == {
        "credits1": 67707, "ap": 81, "credits2": 6250,
    }
    assert reward_amounts(vision.words(frame("cafe_receipt"))) == {"ap": 81, "credits": 73957}


def visitor_words():
    return [Word("Guide", .99, (595, 175, 685, 195)),
            Word("Visiting Student List", .99, (480, 240, 800, 270)),
            Word("Confirm", .99, (560, 435, 720, 480))]


def test_visitor_notice_requires_exact_labels_and_dimmed_cafe_hud(vision):
    room = frame("cafe_markers")
    dimmed = (room * .45).astype("uint8")
    assert vision.visitor_notice(dimmed, visitor_words()) == (640, 457)
    assert vision.visitor_notice(room, visitor_words()) is None
    assert vision.visitor_notice(dimmed, visitor_words()[:2]) is None
    assert vision.visitor_notice(dimmed, visitor_words() + [visitor_words()[-1]]) is None
    assert vision.visitor_notice(dimmed, visitor_words() + [Word("Purchase", .99, (600, 350, 680, 380))]) is None
    # The same text over a different screen or an inconsistent dimming is insufficient.
    dimmed[:45] = room[:45]
    assert vision.visitor_notice(dimmed, visitor_words()) is None


def test_delayed_visitor_notice_accepts_ocr_close_glyph_only_in_its_corner(vision):
    image = frame("cafe-delayed-visitors")
    # Masking the surrounding room changes OCR's detection of the tiny X.
    # Replay the original panel-only words against the unchanged control pixels.
    words = [Word(**value) for value in json.loads(
        (FIXTURES / "cafe-delayed-visitors.json").read_text())]
    assert any(w.normalized == "x" for w in words)
    assert not vision.is_cafe(image)
    assert vision.visitor_notice(image, words) == (640, 458)
    labels = [w for w in words if w.normalized != "x"]
    assert vision.visitor_notice(image, labels) == (640, 458)
    assert vision.visitor_notice(image, labels + [Word("X", .99, (590, 330, 610, 350))]) is None
    assert vision.visitor_notice(image, labels + [Word("Purchase", .99, (590, 330, 680, 350))]) is None


@pytest.fixture(scope="module")
def native_visitors(vision):
    png = (FIXTURES / "cafe-visitors-native.png").read_bytes()
    return decode_frame(png), read_game_words(png, vision.startup)


def test_native_visitor_notice_with_dimmed_comfort_label(vision, native_visitors):
    image, words = native_visitors
    assert not vision.is_cafe(image)
    target = vision.visitor_notice(image, words)
    assert target is not None
    assert 635 <= target[0] <= 645 and 450 <= target[1] <= 465


@pytest.mark.parametrize("obstruction", [
    "title", "edit", "comfort", "different_comfort", "bright_comfort",
    "missing_label", "duplicate_confirm", "purchase",
])
def test_native_visitor_notice_keeps_all_dialog_and_hud_guards(
    vision, native_visitors, obstruction,
):
    original, original_words = native_visitors
    image, words = original.copy(), list(original_words)
    anchors = {"title": (102, 6, 173, 40), "edit": (70, 658, 124, 691),
               "comfort": (964, 626, 1062, 660)}
    if obstruction in anchors:
        x1, y1, x2, y2 = anchors[obstruction]
        image[y1:y2, x1:x2] = 0
    elif obstruction == "different_comfort":
        image[626:660, 964:1062] = image[626:660, 964:1062][:, ::-1]
    elif obstruction == "bright_comfort":
        image[626:660, 964:1062] = (
            image[626:660, 964:1062].astype(float) * 2.4
        ).clip(0, 255).astype("uint8")
    elif obstruction == "missing_label":
        words = [w for w in words if w.normalized != "visiting student list"]
    elif obstruction == "duplicate_confirm":
        words.extend(w for w in original_words if w.normalized == "confirm")
    else:
        words.append(Word("Purchase", .99, (590, 330, 680, 350)))
    assert vision.visitor_notice(image, words) is None
