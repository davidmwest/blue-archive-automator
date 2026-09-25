"""Regression tests from sanitized Total Assault captures and withheld evidence."""
from dataclasses import replace
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator.assault_vision import (
    AssaultVision, DAMAGE_POINTS, classify_assault, damage_type, formation_stars,
    read_formation,
)
from ba_automator.vision import StartupVision, Word, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"
STARS = (5, 4, 3, 3, 4, 4)


def capture(name):
    frame = decode_frame((FIXTURES / f"assault-{name}.png").read_bytes())
    words = [Word(w["text"], w["confidence"], tuple(w["box"]))
             for w in json.loads((FIXTURES / f"assault-{name}.json").read_text())]
    return frame, words


@pytest.fixture(scope="module")
def vision():
    return AssaultVision(StartupVision())


@pytest.mark.parametrize("name,kind", [("menu", "menu"), ("detail", "detail"),
                                      ("formation", "formation"),
                                      ("formation-empty", "formation"), ("quick", "quick")])
def test_sanitized_live_screens_with_local_ocr(vision, name, kind):
    screen = vision.analyze((FIXTURES / f"assault-{name}.png").read_bytes())
    assert screen.kind == kind
    if name == "formation":
        assert [member.student_id for member in screen.team] == [
            "Aris (Maid)", "Kayoko (New Year)", "Koyuki", "Izuna (Swimsuit)",
            "Ako", "Yuzu (Armed)"]
        assert [member.stars for member in screen.team] == list(STARS)
        assert [member.level for member in screen.team] == [79, 79, 79, 79, 79, 78]
        assert {member.damage_type for member in screen.team} == {"mystic"}
        assert screen.target == (1177, 659)
    if name == "formation-empty":
        assert not screen.team and screen.target is None


def test_menu_records_observed_unlocked_tiers_and_event_dates():
    result = classify_assault(*capture("menu"))
    assert result.tickets == 6 and result.boss == "Drumbarka"
    assert result.event_period == "09/21 19:00 – 09/28 11:59"
    assert [stage.difficulty for stage in result.stages] == ["very_hard", "hardcore", "extreme"]
    assert all(stage.locked is False and stage.target for stage in result.stages)


def test_menu_missing_enter_is_unknown_lock_not_evidence_for_unlock_ladder():
    frame, words = capture("menu")
    words = [word for word in words if word.normalized != "enter"]
    result = classify_assault(frame, words)
    assert result.kind == "menu"
    assert all(stage.locked is None and stage.target is None for stage in result.stages)


def test_official_unlock_phrase_is_bound_to_its_row_without_external_assets():
    frame, words = capture("menu")
    words = [word for word in words if not (word.normalized == "enter" and word.center[1] > 450)]
    words.append(Word("Unlocks from clearing the lower difficulty.", .99, (722, 538, 1195, 563)))
    result = classify_assault(frame, words)
    assert [stage.locked for stage in result.stages] == [False, False, True]
    assert result.stages[-1].boss == "Drumbarka" and result.stages[-1].target is None


@pytest.mark.parametrize("phrase", ["Unlocked", "Unlocks after an event", "Unlocks from clearing difficulty."])
def test_missing_enter_and_similar_text_do_not_invent_a_locked_row(phrase):
    frame, words = capture("menu")
    words = [word for word in words if word.normalized != "enter"]
    words.append(Word(phrase, .99, (1055, 532, 1240, 559)))
    assert all(stage.locked is None for stage in classify_assault(frame, words).stages)


def test_contradictory_enter_and_lock_evidence_expose_no_input():
    frame, words = capture("menu")
    words.append(Word("Unlocks from clearing the lower difficulty.", .99, (722, 550, 1195, 573)))
    stage = classify_assault(frame, words).stages[-1]
    assert stage.locked is None and stage.target is None


def test_detail_reads_ticket_cost_but_never_targets_disabled_sweep():
    result = classify_assault(*capture("detail"))
    assert (result.difficulty, result.boss, result.tickets) == ("hardcore", "Drumbarka", 6)
    assert result.mock_target == (803, 545)
    assert result.enter_target == (1010, 545)
    assert result.sweep_target is None


def test_entry_requires_one_ticket_projection_and_visible_yellow_control():
    frame, words = capture("detail")
    inconsistent = [replace(word, text="6→4") if word.text == "6→5" else word for word in words]
    assert classify_assault(frame, inconsistent).enter_target is None
    missing = [word for word in words if word.text != "6→5"]
    assert classify_assault(frame, missing).enter_target is None
    frame[505:585, 900:1140] = 128
    assert classify_assault(frame, words).enter_target is None


def test_quick_formation_controls_are_separate_from_mobilization():
    result = classify_assault(*capture("quick"))
    assert result.auto_target == (623, 593)
    assert result.confirm_target == (1168, 593)
    assert result.assistant_target == (1083, 156)
    assert not result.team and result.enter_target is None


@pytest.mark.parametrize("name", ["menu", "detail", "formation", "quick"])
def test_dimmed_background_never_authorizes_navigation(name):
    frame, words = capture(name)
    result = classify_assault((frame * .5).astype("uint8"), words, stars=STARS)
    assert result.kind == "unknown"
    assert result.target is None


@pytest.mark.parametrize("missing", ["Aris (Maid)", "Year)", "Lv.78"])
def test_missing_team_metadata_never_produces_partial_verified_team(missing):
    frame, words = capture("formation")
    words = [word for word in words if word.text != missing]
    result = classify_assault(frame, words, stars=STARS)
    assert result.kind == "formation" and not result.team and result.target is None


def test_missing_or_invalid_stars_never_produces_verified_team():
    frame, words = capture("formation")
    assert not read_formation(frame, words, stars=STARS[:-1])
    assert not read_formation(frame, words, stars=(5, 4, 3, 3, 4, 0))


def test_attack_color_is_not_inferred_from_another_student_or_armor():
    frame, words = capture("formation")
    x, y = DAMAGE_POINTS[2]
    frame[y-4:y+5, x-2:x+3] = 128
    assert damage_type(frame, (x, y)) is None
    assert not read_formation(frame, words, stars=STARS)


@pytest.mark.parametrize("hue,expected", [(0, "explosive"), (25, "piercing"),
                                        (101, "mystic"), (145, "sonic"), (70, None)])
def test_damage_color_bands(hue, expected):
    frame = cv2.cvtColor(np.full((20, 20, 3), (hue, 200, 160), dtype=np.uint8), cv2.COLOR_HSV2BGR)
    assert damage_type(frame, (10, 10)) == expected


def test_star_ocr_rejects_ambiguous_crops_and_uses_one_pass():
    class OCR:
        calls = 0
        def read(self, frame):
            self.calls += 1
            assert frame.shape == (130, 1020, 3)
            return [Word(str(n), .99, (i*170+45, 10, i*170+120, 100)) for i,n in enumerate(STARS)]
    frame, _ = capture("formation")
    reader = OCR()
    assert formation_stars(frame, reader) == STARS
    assert reader.calls == 1
    reader.read = lambda frame: [Word("4", .99, (0, 0, 900, 100))]
    assert formation_stars(frame, reader) == ()


def test_equipped_blue_stars_normalize_to_base_rarity_five(vision):
    screen = vision.analyze((FIXTURES / "assault-assistant-formation.png").read_bytes())
    assert screen.kind == "formation" and len(screen.team) == 6
    assert [(member.student_id, member.stars) for member in screen.team[:2]] == [
        ("Aris (Maid)", 5), ("Aris (Armed)", 5)]
    assert screen.team[1].level == 90 and screen.target == (1177, 659)
    # Lender provenance is not visible on the formation nameplate.
    assert not screen.team[1].assistant


def test_blue_equipment_star_does_not_require_readable_equipment_digit():
    frame, _ = capture("assistant-formation")
    class OCR:
        def read(self, frame):
            return [Word(str(n), .99, (i*170+45, 10, i*170+120, 100))
                    for i,n in enumerate((0, 0, 3, 3, 4, 4)) if i >= 2]
    assert formation_stars(frame, OCR()) == (5, 5, 3, 3, 4, 4)


def test_assistant_fee_notice_is_recognized_before_dimmed_formation(vision):
    frame, words = capture("assistant-fee")
    screen = classify_assault(frame, words)
    assert screen.kind == "assistant_confirm" and screen.confirm_target == (768, 510)
    assert (screen.credit_fee, screen.assistant_level, screen.assistant_stars) == (40000, 90, 5)
    local = vision.analyze((FIXTURES / "assault-assistant-fee.png").read_bytes())
    assert local.credit_fee == 40000 and local.confirm_target == (768, 510)


@pytest.mark.parametrize("missing", ["40,000", "Lv.90", "Cancel", "Confirm"])
def test_assistant_fee_notice_requires_every_payment_field(missing):
    frame, words = capture("assistant-fee")
    screen = classify_assault(frame, [word for word in words if word.text != missing])
    assert screen.kind == "assistant_confirm" and screen.confirm_target is None


@pytest.mark.parametrize("bounds", [(663, 303, 688, 328), (527, 401, 578, 449), (660, 475, 872, 544)])
def test_assistant_fee_notice_requires_marker_currency_and_enabled_confirmation(bounds):
    frame, words = capture("assistant-fee")
    x1,y1,x2,y2 = bounds
    frame[y1:y2,x1:x2] = 100
    assert classify_assault(frame, words).confirm_target is None


def test_changed_assistant_fee_is_not_accepted():
    frame, words = capture("assistant-fee")
    words = [replace(word, text="400,000") if word.text == "40,000" else word for word in words]
    assert classify_assault(frame, words).confirm_target is None


def test_unreadable_assistant_stars_without_badge_evidence_stop_confirmation():
    frame, words = capture("assistant-fee")
    words = [word for word in words if word.text != "5"]
    frame[369:397, 588:615] = 100
    assert classify_assault(frame, words).confirm_target is None


def test_dimmed_assistant_notice_never_authorizes_another_confirmation():
    frame, words = capture("assistant-fee")
    assert classify_assault((frame*.5).astype(np.uint8), words).confirm_target is None


def test_live_ticket_confirmation_requires_tier_and_exact_one_ticket_projection(vision):
    screen = classify_assault(*capture("entry-confirmation"))
    assert screen.kind == "entry_confirm" and screen.confirm_target == (767, 503)
    assert (screen.difficulty, screen.tickets, screen.after_tickets, screen.count) == ("hardcore", 6, 5, 1)
    local = vision.analyze((FIXTURES / "assault-entry-confirmation.png").read_bytes())
    assert local.confirm_target == screen.confirm_target and local.tickets == 6


@pytest.mark.parametrize("missing", ["Hardcore", "6→5", "Confirm", "Cancel"])
def test_incomplete_ticket_modal_never_exposes_confirm(missing):
    frame, words = capture("entry-confirmation")
    screen = classify_assault(frame, [word for word in words if word.text != missing])
    assert screen.kind == "entry_confirm" and screen.confirm_target is None


def test_ticket_confirmation_does_not_guess_an_unreadable_or_changed_projection():
    frame, words = capture("entry-confirmation")
    for text in ("6→4", "6", "O→O", "0→0"):
        altered = [replace(word, text=text) if word.text == "6→5" else word for word in words]
        assert classify_assault(frame, altered).confirm_target is None


def test_real_clear_reward_is_recognized_before_dimmed_result(vision):
    screen = classify_assault(*capture("clear-receipt"))
    assert screen.kind == "receipt" and screen.target == (771, 660)
    assert screen.items == ({"name": "Total Assault Coin", "quantity": 100},
                            {"name": "Advanced Total Assault Coin", "quantity": 10})
    local = vision.analyze((FIXTURES / "assault-clear-receipt.png").read_bytes())
    assert local.kind == "receipt" and local.target == screen.target
    assert local.items == screen.items


@pytest.mark.parametrize("missing", ["Confirm", "x100", "x10"])
def test_clear_reward_never_guesses_a_missing_control_or_quantity(missing):
    frame, words = capture("clear-receipt")
    screen = classify_assault(frame, [word for word in words if word.text != missing])
    if missing == "Confirm":
        assert screen.target is None
    else:
        assert len(screen.items) == 1  # Full receipt reader inspects every card before closing.


def test_dimmed_clear_reward_never_exposes_confirm():
    frame, words = capture("clear-receipt")
    assert classify_assault((frame * .5).astype(np.uint8), words).target is None


def test_live_season_record_confirmation_is_not_a_reward_claim(vision):
    screen = classify_assault(*capture("season-record"))
    assert screen.kind == "season_record" and screen.target == (640, 530)
    assert screen.items == () and screen.boss is None and screen.difficulty is None
    local = vision.analyze((FIXTURES / "assault-season-record.png").read_bytes())
    assert local.kind == "season_record" and local.target == screen.target


@pytest.mark.parametrize("missing", ["Confirm", "Best Rank Points", "Total Season Points"])
def test_incomplete_season_record_notice_has_no_confirmation(missing):
    frame, words = capture("season-record")
    screen = classify_assault(frame, [word for word in words if word.text != missing])
    assert screen.kind == "season_record" and screen.target is None


def test_dimmed_season_record_notice_has_no_confirmation():
    frame, words = capture("season-record")
    assert classify_assault((frame*.5).astype(np.uint8), words).target is None


def test_live_sweep_detail_exposes_max_only_with_verified_remaining_tickets(vision):
    screen = classify_assault(*capture("sweep-detail"))
    assert (screen.kind, screen.count, screen.tickets, screen.after_tickets) == ("detail", 1, 5, 4)
    assert screen.sweep_target == (940, 385) and screen.sweep_max_target == (1085, 290)
    local = vision.analyze((FIXTURES / "assault-sweep-detail.png").read_bytes())
    assert local.sweep_max_target == screen.sweep_max_target


def test_disabled_sweep_or_unreadable_max_does_not_expose_count_input():
    assert classify_assault(*capture("detail")).sweep_max_target is None
    frame, words = capture("sweep-detail")
    assert classify_assault(frame, [word for word in words if word.text != "Max"]).sweep_max_target is None
    frame[268:311, 1055:1117] = 128
    assert classify_assault(frame, words).sweep_max_target is None


def test_sweep_already_at_max_does_not_repeat_count_input():
    frame, words = capture("sweep-detail")
    words = [replace(word, text="5") if word.text == "1" else
             replace(word, text="5→0") if word.text == "5→4" and word.center[1] < 400 else word
             for word in words]
    screen = classify_assault(frame, words)
    assert screen.count == 5 and screen.sweep_target and screen.sweep_max_target is None


def test_live_sweep_confirmation_binds_exact_count_tier_and_projection(vision):
    screen = classify_assault(*capture("sweep-confirmation"))
    assert screen.kind == "sweep_confirm" and screen.target == (765, 503)
    assert (screen.count, screen.difficulty, screen.tickets, screen.after_tickets) == (5, "hardcore", 5, 0)
    assert screen.boss is None  # Obscured boss text is not used as evidence.
    local = vision.analyze((FIXTURES / "assault-sweep-confirmation.png").read_bytes())
    assert (local.kind, local.target, local.count, local.difficulty,
            local.tickets, local.after_tickets) == ("sweep_confirm", (765, 503), 5, "hardcore", 5, 0)


@pytest.mark.parametrize("missing", ["Room Info", "HARDCORE", " 5 →0", "Cancel", "Confirm"])
def test_incomplete_sweep_confirmation_has_no_target(missing):
    frame, words = capture("sweep-confirmation")
    screen = classify_assault(frame, [word for word in words if word.text != missing])
    assert screen.kind == "sweep_confirm" and screen.target is None


@pytest.mark.parametrize("body", ["Use 5 Total Assault Ticket to Sweep 4", "Use 0 Total Assault Ticket to Sweep 0",
                                  "Use 5 Total Assault Ticket to Sweep 50"])
def test_sweep_count_mismatch_never_authorizes_confirmation(body):
    frame, words = capture("sweep-confirmation")
    words = [replace(word, text=body) if word.text.startswith("Use 5") else word for word in words]
    assert classify_assault(frame, words).target is None


def test_dimmed_sweep_confirmation_has_no_target():
    frame, words = capture("sweep-confirmation")
    assert classify_assault((frame*.5).astype(np.uint8), words).target is None


def test_live_sweep_receipt_uses_final_cards_only(vision):
    from ba_automator.loot_receipts import page
    screen = classify_assault(*capture("sweep-receipt"))
    assert screen.kind == "receipt" and screen.target == (640, 583)
    png = (FIXTURES / "assault-sweep-receipt.png").read_bytes()
    local = vision.analyze(png)
    assert local.kind == "receipt" and local.target == screen.target
    receipt = page(png, vision.startup)
    assert receipt.kind == "sweep" and not receipt.clipped
    assert [card.quantity for card in receipt.cards] == [500, 50]


@pytest.mark.parametrize("missing", ["Final", "Confirm"])
def test_incomplete_sweep_receipt_has_no_close_control(missing):
    frame, words = capture("sweep-receipt")
    assert classify_assault(frame, [word for word in words if word.text != missing]).target is None


def test_dimmed_sweep_receipt_has_no_close_control():
    frame, words = capture("sweep-receipt")
    assert classify_assault((frame*.5).astype(np.uint8), words).target is None


def test_live_zero_ticket_room_uses_explicit_zero_evidence(vision):
    screen = classify_assault(*capture("zero-detail"))
    assert screen.kind == "detail" and screen.tickets == 0 and screen.count == 0
    assert screen.difficulty == "hardcore" and screen.boss == "Drumbarka"
    assert screen.enter_target is None and screen.sweep_target is None and screen.sweep_max_target is None
    local = vision.analyze((FIXTURES / "assault-zero-detail.png").read_bytes())
    assert local.tickets == 0 and local.count == 0


@pytest.mark.parametrize("missing", ["0", "0 →-"])
def test_unreadable_zero_budget_does_not_invent_zero(missing):
    frame, words = capture("zero-detail")
    screen = classify_assault(frame, [word for word in words if word.text != missing])
    assert screen.kind == "detail" and screen.tickets is None
