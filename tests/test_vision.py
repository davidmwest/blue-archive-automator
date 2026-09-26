"""Startup decisions must recognize context before authorizing a tap."""

import builtins
import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ba_automator.vision import StartupVision, VisionError, Word, classify, decode_frame, publisher_splash


HOME_MATCHES = {"home_left": (74, 288), "home_right": (1160, 665)}


@pytest.mark.parametrize("initial_flag", [None, "0"])
def test_ocr_disables_native_telemetry_before_import_and_session_creation(monkeypatch, initial_flag):
    if initial_flag is None:
        monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("ORT_DISABLE_TELEMETRY", initial_flag)
    events = []
    original_import = builtins.__import__
    engine = object()

    def create_engine(**_kwargs):
        events.append("session")
        return engine

    def guarded_import(name, *args, **kwargs):
        if name in {"onnxruntime", "rapidocr"}:
            assert os.environ.get("ORT_DISABLE_TELEMETRY") == "1"
            events.append(name)
            if name == "onnxruntime":
                return SimpleNamespace(disable_telemetry_events=lambda: events.append("disable"))
            return SimpleNamespace(RapidOCR=create_engine)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    vision = StartupVision()
    assert vision.ocr is engine
    assert events == ["onnxruntime", "disable", "rapidocr", "session"]


def word(text, *, center=(640, 300), confidence=0.99):
    x, y = center
    return Word(text, confidence, (x - 50, y - 10, x + 50, y + 10))


@pytest.mark.parametrize("message", [
    "Additional data is ready to download. 750 MB",
    "Would you like to download 1.5 GB?",
    "Do you want to download 256 MiB over Wi-Fi?",
    "Data download required: 750MB",
    "You must download 750 MB to continue",
])
def test_download_confirmation_requires_download_context(message):
    observation = classify([
        word(message), word("No", center=(430, 490)), word("Yes", center=(830, 490)),
    ], {})
    assert observation.state == "download_prompt"
    assert observation.target == (830, 490)


@pytest.mark.parametrize("message", ["Yes", "Confirm", "OK", "Download", "750 MB"])
def test_bare_affirmative_or_size_never_authorizes_input(message):
    observation = classify([word(message, center=(830, 490))], {})
    assert observation.state == "unknown"
    assert observation.target is None


def test_purchase_confirmation_is_not_a_startup_popup():
    observation = classify([
        word("Purchase this package for 750 pyroxenes?"),
        word("Yes", center=(830, 490)),
    ], {})
    assert observation.state == "unknown"
    assert observation.target is None


def test_conflicting_purchase_context_does_not_authorize_download_confirmation():
    observation = classify([
        word("Ready to download 750 MB"),
        word("Purchase for 750 pyroxenes?", center=(640, 380)),
        word("Confirm", center=(830, 490)),
    ], HOME_MATCHES)
    assert observation.state == "unknown"
    assert observation.target is None


def test_background_download_text_cannot_authorize_unrelated_confirmation():
    observation = classify([
        word("Ready to download 750 MB", center=(640, 690)),
        word("Confirm", center=(830, 490)),
    ], {})
    assert observation.state == "unknown"
    assert observation.target is None


@pytest.mark.parametrize("message", [
    "The server is under maintenance", "Server maintenance in progress",
    "Please update the app", "Download the latest version of the app",
    "Go to the store to update", "Select login method", "Choose a login method.",
    "Sign in with email and password",
])
def test_external_attention_blocks_before_home_and_confirmation(message):
    observation = classify([
        word(message), word("Confirm", center=(830, 490)),
    ], HOME_MATCHES)
    assert observation.state == "blocked"
    assert observation.target is None


@pytest.mark.parametrize("matches", [{}, {"home_left": (74, 288)}, {"home_right": (1160, 665)}])
def test_partial_home_evidence_is_not_success(matches):
    observation = classify([word("Campaign"), word("Cafe")], matches)
    assert observation.state == "unknown"
    assert observation.target is None


def test_home_requires_both_unobstructed_template_matches():
    observation = classify([word("Campaign"), word("Cafe")], HOME_MATCHES)
    assert observation.state == "home"
    assert observation.target is None


def test_loading_overlay_prevents_home_success():
    observation = classify([word("Now Loading...")], HOME_MATCHES)
    assert observation.state == "loading"


def test_completed_maintenance_notice_can_be_closed():
    observation = classify([word("Server Maintenance Completed")], {"notice_close": (886, 165)})
    assert observation.state == "popup"
    assert observation.target == (886, 165)


def test_news_portal_is_dismissed_even_when_article_mentions_maintenance():
    observation = classify([word("Server is under maintenance")], {
        **HOME_MATCHES, "news_close": (1192, 44), "news_sidebar": (95, 145),
    })
    assert observation.state == "popup"
    assert observation.target == (1192, 44)


def test_new_products_notice_dismisses_confirm_and_never_shop_shortcut():
    observation = classify([
        word("New products added."), word("Confirm", center=(525, 550)),
        word("Shortcut", center=(766, 550)), word("USD 18.99"),
    ], {})
    assert observation.state == "popup"
    assert observation.target == (525, 550)


@pytest.mark.parametrize("message", [
    "An unexpected issue occurred", "Purchase this package for 750 pyroxenes?",
])
def test_unknown_confirmation_in_front_of_home_prevents_success(message):
    observation = classify([
        word(message), word("Confirm", center=(830, 490)),
    ], HOME_MATCHES)
    assert observation.state == "unknown"
    assert observation.target is None


@pytest.mark.parametrize("text", ["TOUCH TO START", "TOUCHTOSTART", "Tap to start", "Touch to begin"])
def test_title_uses_recognized_start_target(text):
    observation = classify([word(text, center=(640, 600))], {})
    assert observation.state == "title"
    assert observation.target == (640, 600)


def test_required_download_button_must_be_in_confirm_region():
    observation = classify([
        word("Additional data download required: 750 MB"), word("Yes", center=(140, 100)),
    ], {})
    assert observation.state == "unknown"
    assert observation.target is None


def test_recognized_close_is_prioritized_over_home_background():
    observation = classify([], {**HOME_MATCHES, "notice_close": (1160, 70)})
    assert observation.state == "popup"
    assert observation.target == (1160, 70)


@pytest.mark.parametrize("message", ["Downloading 50%", "Extracting data", "Applying patch"])
def test_download_progress_does_not_tap_affirmative_text(message):
    observation = classify([word(message)], {})
    assert observation.state == "downloading"
    assert observation.target is None


def png(width, height):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


@pytest.mark.parametrize("image", [b"", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_malformed_screenshot_raises_vision_error(image):
    with pytest.raises(VisionError):
        decode_frame(image)


@pytest.mark.parametrize("dimensions", [(1920, 1080), (720, 1280), (1280, 719)])
def test_wrong_resolution_screenshot_is_rejected(dimensions):
    with pytest.raises(VisionError, match="1280×720"):
        decode_frame(png(*dimensions))


def splash_words():
    return [word("NEXON", center=(250, 340)), word("NEXON", center=(545, 340)),
            word("GAMES", center=(580, 375)), word("IODIVISION", center=(820, 350)),
            word("mX", center=(1070, 350), confidence=.75)]


def test_publisher_splash_accepts_split_or_merged_middle_logo():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert publisher_splash(frame, splash_words())
    merged = splash_words()
    merged[1:3] = [word("NEXON GAMES", center=(545, 350))]
    assert publisher_splash(frame, merged)


@pytest.mark.parametrize("words", [
    [], [word("NEXON", center=(250, 340))], splash_words()[:3],
    [word("NEXON", center=(250, 340)), word("NEXON", center=(250, 340)),
     word("GAMES", center=(580, 375)), word("IODIVISION", center=(820, 350))],
    splash_words() + [word("Confirm", center=(640, 550))],
    splash_words() + [word("Nexon announcement", center=(820, 350))],
    [word(w.text, center=(w.center[0], 150), confidence=w.confidence) for w in splash_words()],
    [word(w.text, center=w.center, confidence=.7) for w in splash_words()],
])
def test_publisher_mentions_without_all_positioned_logos_are_not_a_splash(words):
    assert not publisher_splash(np.zeros((720, 1280, 3), dtype=np.uint8), words)


def test_publisher_splash_requires_black_background_outside_the_logo_strip():
    bright = np.full((720, 1280, 3), 240, dtype=np.uint8)
    assert not publisher_splash(bright, splash_words())
    dark_notice = np.zeros((720, 1280, 3), dtype=np.uint8)
    dark_notice[470:560, 500:780] = 100
    assert not publisher_splash(dark_notice, splash_words())


def test_publisher_splash_keeps_blocked_and_unknown_dialog_precedence():
    vision = StartupVision.__new__(StartupVision)
    vision.assets = []
    for extra, state in (("Server is under maintenance", "blocked"), ("Confirm", "unknown")):
        vision.read = lambda frame, extra=extra: splash_words() + [word(extra, center=(640, 550))]
        observation = vision.analyze(png(1280, 720))
        assert observation.state == state
        assert observation.target is None


def test_native_screenshot_preserves_coordinate_grid():
    frame = decode_frame(png(1280, 720))
    assert frame.shape == (720, 1280, 3)
    assert frame.dtype == np.uint8
