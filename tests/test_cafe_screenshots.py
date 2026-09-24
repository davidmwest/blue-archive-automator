from pathlib import Path

import pytest

from ba_automator.cafe import earnings_amounts, reward_amounts
from ba_automator.cafe_vision import CafeVision, scene_point
from ba_automator.vision import StartupVision, decode_frame

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
