from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ba_automator.invitation_roster import (STAR_BOUNDS, profile_identity, profile_rarity,
                                           read_current_stars, roster_cards, roster_screen)
from ba_automator.vision import Word


def word(text, x1, y1, x2, y2, confidence=.99):
    return Word(text, confidence, (x1, y1, x2, y2))


def profile_words(name="Aris (Maid)"):
    return [word("Student", 102, 8, 221, 38), word("Basic Info", 698, 126, 813, 156),
            word("Stats", 684, 188, 741, 217), word(name, 69, 556, 216, 592)]


def profile_frame():
    frame = np.full((720, 1280, 3), 240, np.uint8)
    crop = cv2.imread(str(Path(__file__).parent / "fixtures" / "invitation-profile-rarity.png"))
    x1, y1, x2, y2 = STAR_BOUNDS
    frame[y1:y2, x1:x2] = crop
    return frame


def roster_words(name="Aris (Maid)"):
    return [word("Students", 103, 9, 229, 39), word("Student List", 83, 98, 254, 136),
            word("All", 428, 101, 464, 129), word("STRIKER", 541, 102, 637, 129),
            word("SPECIAL", 689, 101, 780, 128), word("Lv.79", 109, 364, 171, 391),
            word(name, 82, 406, 199, 435)]


def roster_frame():
    frame = np.full((720, 1280, 3), 240, np.uint8)
    frame[98:132, 388:420] = 60
    return frame


def test_live_sanitized_gold_strip_identifies_five_stars_only_for_exact_profile():
    frame, words = profile_frame(), profile_words()
    assert profile_rarity(frame, words, "Aris (Maid)") == 5
    assert profile_rarity(frame, words, "Aris") is None
    assert profile_rarity(frame, roster_words(), "Aris (Maid)") is None
    assert profile_rarity((frame * .5).astype(np.uint8), words, "Aris (Maid)") is None


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_counts_individual_star_peaks_despite_touching_gold_bases(count):
    frame = profile_frame()
    # Preserve actual live stars at their observed 14px spacing, removing stars
    # beyond the requested count to model the same strip with lower rarity.
    frame[565:591, 264 + count * 14 + 2:340] = (60, 40, 20)
    assert profile_rarity(frame, profile_words(), "Aris (Maid)") == count


def test_missing_or_ambiguous_gold_strip_is_unknown():
    words = profile_words()
    frame = profile_frame()
    frame[565:591, 255:340] = 0
    assert profile_rarity(frame, words, "Aris (Maid)") is None
    frame[571:587, 263:335] = (0, 220, 255)
    assert profile_rarity(frame, words, "Aris (Maid)") is None
    frame = profile_frame()
    frame[565:568, 255:258] = (0, 220, 255)
    assert profile_rarity(frame, words, "Aris (Maid)") is None


def test_wrapped_profile_identity_and_low_confidence_names():
    words = profile_words()[:-1] + [word("Aris", 72, 548, 140, 570),
                                   word("(Maid)", 72, 572, 160, 590)]
    assert profile_identity(words) == "aris maid"
    words[-1] = word("(Maid)", 72, 572, 160, 590, .7)
    assert profile_identity(words) is None
    assert profile_rarity(profile_frame(), words, "Aris (Maid)") is None
    assert profile_rarity(profile_frame(), words, "Aris") is None


@pytest.mark.parametrize("weak_index", [0, 1])
def test_roster_rejects_entire_identity_when_any_name_token_is_weak(weak_index):
    parts = [word("Aris", 82, 406, 135, 426), word("(Maid)", 82, 429, 160, 452)]
    weak = parts[weak_index]
    parts[weak_index] = Word(weak.text, .7, weak.box)
    words = roster_words()[:-1] + parts
    assert roster_cards(words) == []


def test_weak_numeric_metadata_does_not_truncate_or_invalidate_student_identity():
    words = profile_words() + [word("26", 72, 548, 90, 562, .7)]
    assert profile_identity(words) == "aris maid"
    words = roster_words() + [word("26", 82, 442, 105, 460, .7)]
    assert roster_cards(words) == [{"identity": "aris maid", "target": (140, 302)}]


def test_roster_uses_exact_complete_card_name_and_ignores_unowned_and_clipped_rows():
    words = roster_words() + [word("Shiroko", 300, 406, 400, 435),
                              word("Lv.79", 500, 630, 565, 662),
                              word("Shun", 502, 681, 565, 710)]
    assert roster_screen(roster_frame(), words)
    assert roster_cards(words) == [{"identity": "aris maid", "target": (140, 302)}]
    assert not roster_screen((roster_frame() * .5).astype(np.uint8), words)
    assert not roster_screen(roster_frame(), words[1:])


def test_roster_wrapped_variants_and_scroll_relative_name_anchors():
    words = [word("Lv.79", 304, 265, 369, 296), word("Hoshino", 281, 310, 394, 334),
             word("(Swimsuit)", 275, 337, 403, 363)]
    assert roster_cards(words) == [{"identity": "hoshino swimsuit", "target": (336, 205)}]


class Runner:
    def __init__(self, *, identity="Aris (Maid)", absent=False, stars_valid=True):
        self.elapsed = 0
        self.actions = 0
        self.state = "home"
        self.absent = absent
        self.identity = identity
        self.stars_valid = stars_valid
        self.inputs = []
        self.events = []
        self.config = SimpleNamespace(home_confirmations=2)
        self.startup = SimpleNamespace(matches=lambda frame: {"home_left": (1, 1), "home_right": (2, 2)}
                                       if self.state == "home" else {})
        self.journal = SimpleNamespace(record=lambda event, **data: self.events.append((event, data)),
                                       save_image=lambda *args: None)
        self.device = SimpleNamespace(swipe=self.swipe)

    def clock(self):
        return self.elapsed

    def sleep(self, duration):
        self.elapsed += duration

    def capture(self, *, ocr=False):
        if self.state == "home":
            frame, words = np.full((720, 1280, 3), 240, np.uint8), []
        elif self.state == "roster":
            frame, words = roster_frame(), roster_words("Shun" if self.absent else "Aris (Maid)")
        else:
            frame, words = profile_frame(), profile_words(self.identity)
            if not self.stars_valid:
                frame[565:591, 255:340] = 0
        return self.clock(), b"fixture", frame, words

    def tap(self, cap, target, detail):
        self.inputs.append(target)
        self.actions += 1
        if target == (323, 659):
            self.state = "roster"
        elif target == (140, 302):
            self.state = "profile"
        elif target == (1237, 24):
            self.state = "home"

    def swipe(self, start, end, **kwargs):
        self.inputs.append((start, end))
        return True

    def phase(self, detail):
        pass

    def fail(self, message):
        raise RuntimeError(message)


def test_driver_only_reads_rarity_then_returns_to_verified_home():
    runner = Runner()
    assert read_current_stars(runner, "Aris (Maid)") == 5
    assert runner.state == "home"
    assert runner.inputs == [(323, 659), (445, 115),
                             ((630, 290), (630, 600)), ((630, 290), (630, 600)),
                             (140, 302), (1237, 24)]
    assert any(event == "invitation_rarity_observed" and details["rarity"] == 5
               for event, details in runner.events)


def test_driver_does_not_tap_profile_or_upgrade_when_student_missing():
    runner = Runner(absent=True)
    with pytest.raises(RuntimeError, match="not found"):
        read_current_stars(runner, "Aris (Maid)", max_pages=3)
    assert runner.state == "roster"
    assert all(target in [(323, 659), (445, 115), ((630, 290), (630, 600)),
                          ((630, 595), (630, 335))] for target in runner.inputs)


@pytest.mark.parametrize("arguments", [{"identity": "Aris"}, {"stars_valid": False}])
def test_driver_refuses_wrong_profile_or_unreadable_rarity(arguments):
    runner = Runner(**arguments)
    with pytest.raises(RuntimeError, match="identity|rarity"):
        read_current_stars(runner, "Aris (Maid)")
    assert runner.state == "profile"
    assert runner.inputs[-1] == (140, 302)
