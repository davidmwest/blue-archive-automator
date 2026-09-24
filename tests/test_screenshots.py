"""Exercise the actual OCR pipeline against sanitized game UI crops."""

from pathlib import Path

import cv2
import pytest

from ba_automator.vision import StartupVision, decode_frame


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def vision():
    return StartupVision()


def test_actual_required_download_dialog(vision):
    observation = vision.analyze((FIXTURES / "download_prompt.png").read_bytes())
    assert observation.state == "download_prompt"
    x, y = observation.target
    assert 655 <= x <= 885
    assert 470 <= y <= 540


@pytest.mark.parametrize("name,state", [
    ("title", "title"),
    ("banner_day", "popup"),
    ("banner_night", "popup"),
    ("home_controls", "home"),
    ("news_overlay", "popup"),
    ("new_products", "popup"),
    ("publisher_splash", "loading"),
])
def test_actual_startup_controls(vision, name, state):
    observation = vision.analyze((FIXTURES / f"{name}.png").read_bytes())
    assert observation.state == state


def test_actual_publisher_splash_waits_without_input(vision):
    observation = vision.analyze((FIXTURES / "publisher_splash.png").read_bytes())
    assert observation.state == "loading"
    assert observation.target is None
    assert observation.detector == "publisher_splash"


def test_dimmed_home_controls_do_not_mean_popups_are_cleared(vision):
    frame = decode_frame((FIXTURES / "home_controls.png").read_bytes())
    dimmed = (frame * 0.55).astype("uint8")
    ok, png = cv2.imencode(".png", dimmed)
    assert ok
    assert vision.analyze(png.tobytes()).state != "home"
