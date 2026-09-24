"""Event data can suggest a recognized entry; it cannot run game actions."""

from datetime import datetime, timezone
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator.events import (
    EventProfileError, inspect_entries, load_event_profile, match_entries, match_route_check,
)
from ba_automator.vision import VisionError, Word


PROFILE_PATH = Path(__file__).parents[1] / "config" / "events" / "lore-pursuit.json"
ACTIVE = datetime(2026, 9, 23, tzinfo=timezone.utc)


def word(text, center=(1193, 207), confidence=0.99):
    x, y = center
    return Word(text, confidence, (x - 50, y - 10, x + 50, y + 10))


@pytest.fixture
def profile():
    return load_event_profile(PROFILE_PATH)


@pytest.fixture
def document():
    return json.loads(PROFILE_PATH.read_text())


def load_document(tmp_path, document):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(document))
    return load_event_profile(path)


def test_researched_profile_loads_with_two_guarded_entrances(profile):
    assert profile.schema_version == 1
    assert profile.id == "lore-pursuit"
    assert profile.server == "global-en"
    assert {entry.screen for entry in profile.entries} == {"home", "campaign"}
    assert profile.phase(ACTIVE) == "playable"


@pytest.mark.parametrize("key,value", [
    ("schema_version", 2), ("schema_version", True), ("id", "../../other"),
    ("server", "jp"), ("title", ""), ("entries", []), ("route_checks", []),
])
def test_invalid_profile_fields_fail(tmp_path, document, key, value):
    document[key] = value
    with pytest.raises(EventProfileError):
        load_document(tmp_path, document)


@pytest.mark.parametrize("key,value", [
    ("screen", "event_recap"), ("screen", []), ("region", [0, 0, 1920, 1080]),
    ("region", [100, 100, 50, 200]), ("region", [0, 0, True, 400]),
    ("tap", [640, 600]), ("tap", [1193.0, 207]), ("ocr_keywords", []),
    ("template_refs", ["../secret.png"]), ("template_refs", ["missing.png"]),
])
def test_invalid_or_unguarded_entry_fails(tmp_path, document, key, value):
    document["entries"][0][key] = value
    with pytest.raises(EventProfileError):
        load_document(tmp_path, document)


def test_profile_is_not_an_arbitrary_action_script(tmp_path, document):
    document["route_checks"][0]["action"] = "tap anything that says confirm"
    with pytest.raises(EventProfileError, match="Unknown"):
        load_document(tmp_path, document)


def test_notice_must_have_context_words(tmp_path, document):
    document["route_checks"][0]["required_ocr"] = []
    with pytest.raises(EventProfileError):
        load_document(tmp_path, document)


def test_destination_cannot_dismiss_itself(tmp_path, document):
    document["route_checks"][-1]["dismiss_control"] = "confirm"
    with pytest.raises(EventProfileError):
        load_document(tmp_path, document)


def test_duplicate_entrance_names_fail(tmp_path, document):
    document["entries"][1]["id"] = document["entries"][0]["id"]
    with pytest.raises(EventProfileError, match="unique"):
        load_document(tmp_path, document)


@pytest.mark.parametrize("availability", [
    {"playable_until": "2026-09-29T01:59:00"},
    {"starts_at": "not a timestamp"},
    {"starts_at": "2026-10-01T00:00:00Z", "playable_until": "2026-09-29T01:59:00Z"},
    {"playable_until": "2026-09-29T01:59:00Z", "rewards_until": "2026-09-28T00:00:00Z"},
])
def test_invalid_availability_fails(tmp_path, document, availability):
    document["availability"] = availability
    with pytest.raises(EventProfileError):
        load_document(tmp_path, document)


def test_rewards_period_does_not_authorize_playable_event_entry(profile):
    after_stages = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert profile.phase(after_stages) == "rewards_only"
    assert match_entries(profile, [word("Lore Pursuit")], screen="home", at=after_stages) == ()
    assert profile.phase(datetime(2026, 10, 7, tzinfo=timezone.utc)) == "closed"


def test_optional_availability_and_future_start(tmp_path, document):
    document.pop("availability")
    assert load_document(tmp_path, document).phase(ACTIVE) == "unspecified"
    document["availability"] = {"starts_at": "2026-09-25T00:00:00Z"}
    assert load_document(tmp_path, document).phase(ACTIVE) == "not_started"


def test_home_event_identity_returns_only_its_candidate(profile):
    matches = match_entries(profile, [word("Special Mission: Lore Pursuit")], screen="home", at=ACTIVE)
    assert len(matches) == 1
    assert matches[0].entry_id == "home_banner"
    assert matches[0].target == (1193, 207)
    assert matches[0].evidence == "ocr"


def test_campaign_banner_has_its_own_fixed_region(profile):
    matches = match_entries(profile, [word("Lore Pursuit", center=(100, 162))],
                            screen="campaign", at=ACTIVE)
    assert len(matches) == 1
    assert matches[0].target == (100, 162)


@pytest.mark.parametrize("words,screen", [
    ([word("Lore Pursuit", center=(640, 400))], "home"),
    ([word("Lore Pursuit")], "campaign"),
    ([word("Lore Pursuit")], "unknown"),
    ([word("Lore Pursuit", confidence=0.4)], "home"),
    ([word("Lore Pursuitful")], "home"),
    ([word("Other Event")], "home"),
])
def test_wrong_banner_or_screen_produces_no_target(profile, words, screen):
    assert match_entries(profile, words, screen=screen, at=ACTIVE) == ()


@pytest.mark.parametrize("status", ["Locked", "Starts in 2 days", "Coming soon", "Event has ended", "Rewards only"])
def test_unavailable_banner_does_not_match(profile, status):
    assert match_entries(profile, [word("Lore Pursuit"), word(status, center=(1193, 250))],
                         screen="home", at=ACTIVE) == ()


def test_template_evidence_is_opt_in_and_region_bound(tmp_path, document):
    # Local test asset stands in for a reviewed banner crop; no remote assets used.
    cv2.imwrite(str(tmp_path / "banner.png"), np.full((20, 40, 3), 220, np.uint8))
    document["entries"][0]["template_refs"] = ["banner.png"]
    profile = load_document(tmp_path, document)
    assert match_entries(profile, [], screen="home", at=ACTIVE,
                         template_hits={"unlisted.png": (1193, 207)}) == ()
    assert match_entries(profile, [], screen="home", at=ACTIVE,
                         template_hits={"banner.png": (100, 162)}) == ()
    matches = match_entries(profile, [], screen="home", at=ACTIVE,
                            template_hits={"banner.png": (1193, 207)})
    assert matches[0].evidence == "template"


def test_destination_requires_event_identity_and_event_controls(profile):
    assert not match_route_check(profile, "event_page", [word("Lore Pursuit")])
    assert not match_route_check(profile, "event_page", [word("Quest")])
    assert match_route_check(profile, "event_page", [word("Lore Pursuit"), word("Challenge")])


def test_notice_check_never_accepts_bare_confirm(profile):
    assert not match_route_check(profile, "spoiler_notice", [word("Confirm", center=(640, 490))])
    assert match_route_check(profile, "spoiler_notice", [word("Spoiler Notice", center=(640, 260))])
    with pytest.raises(EventProfileError):
        match_route_check(profile, "unknown_step", [])


def test_saved_frame_uses_injected_local_ocr_only(profile):
    class LocalVision:
        def read(self, frame):
            assert frame.shape == (720, 1280, 3)
            return [word("Lore Pursuit")]

    ok, png = cv2.imencode(".png", np.zeros((720, 1280, 3), dtype=np.uint8))
    assert ok
    assert len(inspect_entries(profile, png.tobytes(), LocalVision(), screen="home", at=ACTIVE)) == 1
    with pytest.raises(VisionError):
        inspect_entries(profile, b"invalid", LocalVision(), screen="home", at=ACTIVE)
