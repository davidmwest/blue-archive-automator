"""Replay observed Lessons controls without loading an OCR model or an account."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator.lesson_vision import LessonVision
from ba_automator.vision import Word


FIXTURES = Path(__file__).parent / "fixtures"


class RecordedOCR:
    def __init__(self, frame, metadata):
        self.words = [Word(item["text"], item["confidence"], tuple(item["box"]))
                      for item in metadata["words"]]
        self.crops = {}
        for item in metadata["bond_crops"] + metadata.get("header_crops", []):
            x1, y1, x2, y2 = item["box"]
            scale = item.get("scale", 5)
            image = cv2.resize(frame[y1:y2, x1:x2], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            self.crops[image.tobytes()] = item["text"]

    def read(self, frame):
        if frame.shape[:2] == (720, 1280):
            return self.words
        text = self.crops.get(frame.tobytes())
        return [Word(text, .99, (0, 0, frame.shape[1], frame.shape[0]))] if text else []


def replay(name):
    path = FIXTURES / f"lesson-{name}.png"
    frame = cv2.imread(str(path))
    metadata = json.loads(path.with_suffix(".json").read_text())
    ocr = RecordedOCR(frame, metadata)
    return frame, ocr, LessonVision(ocr)


def analyze(vision, frame):
    return vision.analyze(cv2.imencode(".png", frame)[1].tobytes())


def test_overview_reads_ticket_counter_and_each_visible_location_progress():
    frame, _, vision = replay("exp-on")
    screen = analyze(vision, frame)
    assert screen.kind == "overview"
    assert (screen.tickets, screen.ticket_capacity, screen.total_rank) == (7, 7, 80)
    assert [(row.name, row.rank, row.xp, row.xp_to_next) for row in screen.location_rows] == [
        ("Schale Office", 7, 300, 2100),
        ("Schale Residence Hall", 7, 100, 2100),
        ("Gehenna Hub", 7, 300, 2100),
        ("Abydos Main Building", 7, 600, 2100),
        ("Millennium Study Center", 7, 300, 2100),
    ]


def test_exp_off_does_not_invent_zero_xp_or_maximum_rank():
    frame, _, vision = replay("locations-ready")
    screen = analyze(vision, frame)
    assert len(screen.location_rows) == 5
    assert all(row.xp is None and row.capped is None for row in screen.location_rows)


def test_scrolled_overview_accepts_rank_bracket_and_excludes_clipped_rows():
    frame, _, vision = replay("overview-middle")
    screen = analyze(vision, frame)
    assert [row.name for row in screen.location_rows] == [
        "Red Winter Federal Academy", "Hyakkiyako Central Area",
        "D.U. Shiratori City", "Shanhaijing Main Special Zone",
    ]
    assert all(row.xp is not None for row in screen.location_rows)


def test_map_limits_location_rank_to_header_not_locked_room_rank():
    frame, ocr, vision = replay("schale-ready")
    ocr.words.append(Word("Rank 9 required", 1, (435, 160, 600, 187)))
    screen = analyze(vision, frame)
    assert screen.kind == "map"
    assert screen.location_name == "Schale Office"
    assert (screen.location_rank, screen.location_xp, screen.location_xp_to_next) == (7, 300, 2100)


def test_room_grid_counts_owned_students_and_reads_tiny_bond_labels():
    frame, _, vision = replay("schale-list")
    screen = analyze(vision, frame)
    assert screen.kind == "rooms" and screen.inspection_complete
    assert (screen.tickets, screen.ticket_capacity) == (7, 7)
    assert len(screen.room_cards) == 7
    available = [card for card in screen.room_cards if card.available]
    assert [len(card.students) for card in available] == [3, 3, 1, 3, 2]
    assert [sum(student.owned for student in card.students) for card in available] == [2, 1, 1, 1, 1]
    assert [[student.bond for student in card.students if student.owned] for card in available] == [
        [21, 4], [7], [7], [21], [7],
    ]
    assert [card.index for card in screen.room_cards if card.available is False] == [5, 6]


def test_wrapped_names_and_unowned_pink_hair_are_not_misclassified():
    frame, _, vision = replay("wildhunt-list")
    screen = analyze(vision, frame)
    assert screen.room_cards[0].name == "Wildhunt Central Office"
    assert screen.room_cards[3].name == "Arts Information Center"
    assert all(student.owned is False for student in screen.room_cards[4].students)
    assert [[student.bond for student in card.students if student.owned]
            for card in screen.room_cards[:6]] == [[11], [8], [6], [8], [], []]


def test_red_hair_inside_blue_portrait_is_not_a_relationship_heart():
    frame, _, vision = replay("hyakkiyako-rooms")
    screen = analyze(vision, frame)
    # Red hair occupies the lower right corner and top edge of this portrait;
    # the real ownership heart would extend outside the portrait frame.
    assert screen.room_cards[0].students[2].owned is False
    assert [(student.owned, student.bond) for student in screen.room_cards[0].students] == [
        (False, None), (True, 9), (False, None),
    ]
    assert screen.room_cards[0].name == "Hyakkiyako Yin-Yang Club Main Building"


def test_narrow_heart_crop_recovers_single_digits_without_adjacent_artwork():
    frame, _, vision = replay("gehenna-rooms")
    screen = analyze(vision, frame)
    assert screen.room_cards[3].students[0].bond == 4
    assert screen.room_cards[5].students[1].bond == 3


def test_same_line_ocr_fragments_keep_reading_order_despite_baseline_jitter():
    frame, ocr, vision = replay("gehenna-rooms")
    ocr.words = [word for word in ocr.words
                 if not (550 <= word.center[0] <= 810 and 190 <= word.center[1] <= 245)]
    ocr.words.extend([Word("Gehenna", .99, (559, 202, 648, 230)),
                      Word("Clubhouse", .99, (651, 201, 780, 229))])
    assert analyze(vision, frame).room_cards[1].name == "Gehenna Clubhouse"


def test_opening_animation_does_not_use_translated_portraits_as_fixed_geometry():
    frame, _, vision = replay("opening")
    screen = analyze(vision, frame)
    assert screen.kind == "unknown" and not screen.inspection_complete
    assert not screen.room_cards


def test_confirmation_waits_for_its_final_position_before_parsing_students():
    frame, _, vision = replay("theater")
    shifted = np.zeros_like(frame)
    shifted[4:] = frame[:-4]
    screen = analyze(vision, shifted)
    assert screen.kind == "unknown" and not screen.inspection_complete


def test_relationship_rank_up_is_an_intermediate_screen_with_a_known_dismissal():
    frame, _, vision = replay("relationship-rank-up")
    screen = analyze(vision, frame)
    assert screen.kind == "relationship_rank_up"
    assert screen.dismiss_target == (1170, 650)
    assert screen.kind != "receipt"


def test_receipt_requires_loaded_report_scoped_room_and_confirm_control():
    frame, ocr, vision = replay("receipt")
    ocr.words.extend([Word("Wrong Room", 1, (120, 210, 280, 239)),
                      Word("Confirm", 1, (1090, 645, 1240, 680))])
    screen = analyze(vision, frame)
    assert screen.kind == "receipt"
    assert screen.room_name == "Black Tortoise Promenade"
    assert screen.dismiss_target == (640, 554)
    assert screen.tickets is None  # The covered counter is verified after dismissal.


def test_report_heading_alone_does_not_prove_a_completed_receipt():
    frame, _, vision = replay("receipt-loading")
    screen = analyze(vision, frame)
    assert screen.kind == "unknown"
    assert screen.dismiss_target is None


def test_receipt_without_unique_confirmation_remains_unknown():
    frame, ocr, vision = replay("receipt")
    ocr.words.append(Word("Confirm", 1, (580, 580, 700, 600)))
    assert analyze(vision, frame).kind == "unknown"


def test_completed_room_has_dimmed_portrait_borders_despite_white_heading():
    before, _, before_vision = replay("before-completion")
    after, _, after_vision = replay("completed")
    first = analyze(before_vision, before)
    second = analyze(after_vision, after)
    assert first.room_cards[3].available is True
    assert second.room_cards[3].available is False
    assert second.room_cards[3].students == ()
    assert all(card.available is True for card in second.room_cards[:3])
    assert all(card.available is True for card in second.room_cards[4:6])
    assert second.inspection_complete


def test_one_dimmed_portrait_does_not_mark_an_entire_active_room_completed():
    frame, _, vision = replay("before-completion")
    frame[410:465, 149:215] = (frame[410:465, 149:215].astype(float) * .5).astype(np.uint8)
    screen = analyze(vision, frame)
    assert screen.room_cards[3].available is True


def area_rank_up_sample(title="Area Rank Up!", *, confirm=True, dim=False):
    """Synthetic contract sample, deliberately not presented as a game capture."""
    frame = np.full((720, 1280, 3), 240, np.uint8)
    words = [Word(title, .99, (470, 110, 810, 160))]
    if confirm:
        frame[520:590, 510:770] = (245, 205, 90)
        words.append(Word("Confirm", .99, (575, 535, 705, 575)))
    if dim:
        frame = (frame.astype(float) * .5).astype(np.uint8)

    class OCR:
        def read(self, _frame):
            return words

    return frame, words, LessonVision(OCR())


@pytest.mark.parametrize("title", ["Area Rank Up!", "Location Rank Up!"])
def test_area_rank_up_requires_exact_title_and_observed_active_confirmation(title):
    frame, _, vision = area_rank_up_sample(title)
    screen = analyze(vision, frame)
    assert screen.kind == "area_rank_up"
    assert screen.dismiss_target == (640, 555)


def test_area_rank_up_does_not_guess_a_tap_anywhere_dismissal():
    frame, _, vision = area_rank_up_sample(confirm=False)
    screen = analyze(vision, frame)
    assert screen.kind == "unknown" and screen.dismiss_target is None


def test_area_rank_up_rejects_dimmed_confirmation_and_ambiguous_buttons():
    frame, _, vision = area_rank_up_sample(dim=True)
    assert analyze(vision, frame).kind == "unknown"
    frame, words, vision = area_rank_up_sample()
    words.append(Word("Confirm", .99, (575, 590, 705, 630)))
    assert analyze(vision, frame).kind == "unknown"


def test_generic_rank_up_cannot_be_mistaken_for_school_rank_up():
    frame, _, vision = area_rank_up_sample("Rank Up!")
    assert analyze(vision, frame).kind == "unknown"


def test_missing_card_header_is_preserved_as_incomplete_not_omitted():
    frame, ocr, vision = replay("schale-list")
    ocr.words = [word for word in ocr.words if not (129 <= word.center[0] <= 465 and 181 <= word.center[1] <= 245)]
    screen = analyze(vision, frame)
    assert len(screen.room_cards) == 7
    assert screen.room_cards[0].available is None
    assert not screen.inspection_complete


def test_unreadable_bond_remains_unknown_not_zero():
    frame, ocr, vision = replay("schale-list")
    ocr.crops.clear()
    screen = analyze(vision, frame)
    student = screen.room_cards[0].students[1]
    assert student.owned is True and student.bond is None


def test_damaged_ownership_border_is_unknown_not_unowned():
    frame, ocr, vision = replay("schale-list")
    # Keep the portrait and heart, remove both supported ownership borders.
    frame[259:263, 221:287] = (220, 220, 220)
    frame[259:314, 221:225] = (220, 220, 220)
    screen = analyze(vision, frame)
    assert screen.room_cards[0].students[1].owned is None


def test_confirmation_uses_its_title_students_and_single_ticket_arrow():
    frame, ocr, vision = replay("theater")
    # An underlying card or off-panel button cannot supply confirmation evidence.
    ocr.words.extend([Word("Lv. 3 Wrong Room", 1, (130, 200, 275, 230)),
                      Word("Start Lesson", 1, (1050, 650, 1250, 680))])
    screen = analyze(vision, frame)
    assert screen.kind == "confirm"
    assert screen.room_name == "Theater Room"
    assert (screen.tickets, screen.tickets_after) == (7, 6)
    assert screen.start_target == (639, 550)
    assert [(student.owned, student.bond) for student in screen.students] == [(False, None), (True, 21), (True, 4)]
    assert screen.inspection_complete


def test_confirmation_allows_right_aligned_portraits_but_rejects_internal_gaps():
    frame, _, vision = replay("theater")
    # An empty leading slot is valid for a two-student preview.
    frame[156:211, 774:840] = (245, 245, 245)
    screen = analyze(vision, frame)
    assert screen.inspection_complete and len(screen.students) == 2
    # A missing middle portrait between observed students is incomplete evidence.
    frame, _, vision = replay("theater")
    frame[156:211, 843:909] = (245, 245, 245)
    screen = analyze(vision, frame)
    assert not screen.inspection_complete


@pytest.mark.parametrize("name", ["exp-on", "schale-ready", "schale-list", "theater"])
def test_dimmed_underlying_controls_cannot_be_a_recognized_active_screen(name):
    frame, _, vision = replay(name)
    assert analyze(vision, (frame.astype(float) * .45).astype(np.uint8)).kind == "unknown"
