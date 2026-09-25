"""Relationship gains are observed separately from consumable loot."""

import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from ba_automator.relationship import (
    RelationshipIncrease, RelationshipStat, parse_relationship_rank_up,
    read_relationship_rank_up, record_relationship_increase,
    same_relationship_screen,
)
from ba_automator.vision import Word


FIXTURES = Path(__file__).parent / "fixtures"


def word(text, x=640, y=681, confidence=.99):
    return Word(text, confidence, (x - 30, y - 12, x + 30, y + 12))


def banner():
    return word("Relationship rank up!", y=622)


def fixture_words():
    return [Word(w["text"], w["confidence"], tuple(w["box"]))
            for w in json.loads((FIXTURES / "relationship-rank-up-gift.json").read_text())["words"]]


def test_authorized_gift_capture_reads_actual_rank_and_stat_delta():
    result = parse_relationship_rank_up(fixture_words(), student="Tsurugi")
    assert result == RelationshipIncrease("Tsurugi", 3, (RelationshipStat("ATK", delta=8),))
    assert result.fields()["stats"] == [{"name": "ATK", "before": None, "after": None, "delta": 8}]
    assert result.detail() == "Tsurugi reached relationship rank 3; ATK +8."


def test_portrait_and_rank_alone_do_not_invent_student_name():
    result = parse_relationship_rank_up(fixture_words())
    assert result.student is None
    assert result.rank == 3
    assert result.fields()["unread_fields"] == ["student"]


def test_rank_is_read_from_enlarged_isolated_heart_when_full_ocr_omits_it():
    image = cv2.imread(str(FIXTURES / "relationship-rank-up-gift.png"))
    observed = []

    def read(crop):
        observed.append(crop)
        return [word("3", x=80, y=80)]

    words = [w for w in fixture_words() if w.text != "3"]
    result = read_relationship_rank_up(image, SimpleNamespace(read=read), words=words)
    assert result.rank == 3
    assert len(observed) == 1
    assert observed[0].shape == (165, 192, 3)
    assert np.array_equal(observed[0], cv2.resize(image[512:567, 608:672], (192, 165),
                                               interpolation=cv2.INTER_CUBIC))


def test_uncertain_or_conflicting_rank_readings_remain_unknown():
    result = parse_relationship_rank_up(fixture_words(), rank_words=[word("8")])
    assert result.rank is None
    assert parse_relationship_rank_up([banner(), word("3", y=540, confidence=.7)]).rank is None


def test_no_rank_is_inferred_from_an_unrelated_counter_or_stat_delta():
    result = parse_relationship_rank_up([banner(), word("79", x=200, y=60), word("ATK +8")])
    assert result.rank is None
    assert result.stats == (RelationshipStat("ATK", delta=8),)


def test_generic_relationship_notice_and_heart_feedback_are_not_levelups():
    for heading in ("Relationship Rank", "Relationship increased!", "Relationship Points"):
        words = [word(heading, y=622), word("3", y=540), word("ATK +8")]
        assert parse_relationship_rank_up(words) is None
    assert parse_relationship_rank_up([word("Relationship rank up!", y=50)]) is None


def test_two_displayed_stat_changes_are_preserved_without_total_estimates():
    result = parse_relationship_rank_up([banner(), word("ATK +8 HP +1,200")])
    assert result.stats == (RelationshipStat("ATK", delta=8), RelationshipStat("HP", delta=1200))


def test_split_stat_label_and_amount_are_read_in_visual_order():
    result = parse_relationship_rank_up([banner(), word("+10", x=675), word("Healing", x=590)])
    assert result.stats == (RelationshipStat("Healing", delta=10),)


def test_conflicting_stats_are_not_silently_chosen_or_summed():
    result = parse_relationship_rank_up([banner(), word("ATK +8", x=450), word("ATK +9", x=620),
                                         word("HP +20", x=800)])
    assert result.stats == (RelationshipStat("HP", delta=20),)


def test_bad_stat_quantity_and_background_text_are_not_gains():
    for text in ("ATK +1,2", "ATK +8O", "ATK +12.5", "ATK +12/20", "ATK +12a",
                 "ATK +", "Level 80", "Credits +100"):
        assert parse_relationship_rank_up([banner(), word(text)]).stats == ()
    assert parse_relationship_rank_up([banner(), word("ATK +8", y=200)]).stats == ()


def test_one_canonical_action_keeps_original_screenshot_and_null_totals(tmp_path):
    events = []
    record_relationship_increase(
        object(), parse_relationship_rank_up(fixture_words(), student="Tsurugi"),
        evidence=tmp_path / "rank-up.png", run_dir=tmp_path, task="cafe", cafe=1,
        record_action=lambda *args, **kwargs: events.append((args, kwargs)))
    assert len(events) == 1
    args, fields = events[0]
    assert args[1] == "relationship_rank_increased"
    assert fields["student"] == "Tsurugi"
    assert fields["rank"] == 3
    assert fields["evidence"] == str(tmp_path / "rank-up.png")
    assert fields["run_dir"] == str(tmp_path)
    assert fields["stats"][0] == {"name": "ATK", "before": None, "after": None, "delta": 8}


def test_existing_lessons_fixture_retains_delta_even_with_masked_rank():
    raw = json.loads((FIXTURES / "lesson-relationship-rank-up.json").read_text())
    words = [Word(w["text"], w["confidence"], tuple(w["box"])) for w in raw["words"]]
    result = parse_relationship_rank_up(words)
    assert result.rank is None and result.student is None
    assert result.stats == (RelationshipStat("ATK", delta=20),)


def test_celebration_identity_ignores_small_particle_but_not_student_or_rank():
    original = cv2.imread(str(FIXTURES / "relationship-rank-up-gift.png"))
    assert same_relationship_screen(original, original)
    particle = original.copy()
    particle[180:185, 530:535] = 255
    assert same_relationship_screen(original, particle)
    different_student = original.copy()
    different_student[150:400, 450:800] = 180
    assert not same_relationship_screen(original, different_student)
    different_rank = original.copy()
    different_rank[522:555, 618:663] = 255
    assert not same_relationship_screen(original, different_rank)
