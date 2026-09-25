from dataclasses import replace
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator.assault_battle_vision import auto_state, battle_clock, classify_battle, damage_control
from ba_automator.vision import StartupVision, Word, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


def capture(name="assault-battle-hud"):
    frame = decode_frame((FIXTURES / f"{name}.png").read_bytes())
    words = [Word(w["text"], w["confidence"], tuple(w["box"]))
             for w in json.loads((FIXTURES / f"{name}.json").read_text())]
    return frame, words


def test_real_mock_battle_hud_records_clock_and_auto_off():
    screen = classify_battle(*capture())
    assert screen.kind == "battle"
    assert screen.remaining_seconds == 200.9
    assert screen.auto_on is False and screen.auto_target == (1214, 677)
    assert screen.boss == "Drumbarka" and screen.mock is True
    assert (screen.boss_hp, screen.boss_max_hp) == (2028272, 2400000)
    assert screen.won is None and screen.surviving_striker_ids is None


def test_real_sanitized_hud_local_ocr():
    frame, _ = capture()
    screen = classify_battle(frame, StartupVision().read(frame))
    assert screen.kind == "battle" and screen.remaining_seconds == 200.9
    assert screen.auto_on is False


@pytest.mark.parametrize("local_ocr", [False, True])
def test_real_auto_enabled_hud_never_toggles_auto_off(local_ocr):
    frame, words = capture("assault-battle-auto-on")
    screen = classify_battle(frame, StartupVision().read(frame) if local_ocr else words)
    assert screen.kind == "battle" and screen.remaining_seconds == 106
    assert screen.boss_hp == 966880 and screen.boss_max_hp == 2400000
    assert screen.auto_on is True and screen.auto_target is None
    # A cut-in clips the Mock Battle label. Missing text is not proof of a real run.
    assert screen.mock is None


@pytest.mark.parametrize("missing", ["03:20.900", "BATTLE-BOSS", "Drumbarka", "2,028,272/2,400,000"])
def test_partial_battle_hud_never_authorizes_auto(missing):
    frame, words = capture()
    result = classify_battle(frame, [word for word in words if word.text != missing])
    assert result.kind == "unknown" and result.auto_target is None


@pytest.mark.parametrize("value", ["03:60.000", "3:20", "03:20", "33:00.000", "03:20.OOO"])
def test_unreadable_clock_is_not_a_comfortable_mock_time(value):
    _, words = capture()
    words = [replace(word, text=value) if word.text == "03:20.900" else word for word in words]
    assert battle_clock(words) is None


def test_speed_button_color_and_missing_auto_label_never_authorize_toggle():
    frame, words = capture()
    frame[652:703, 1160:1270] = cv2.cvtColor(np.uint8([[[95, 140, 230]]]), cv2.COLOR_HSV2BGR)[0,0]
    assert auto_state(frame, words) is None
    assert classify_battle(frame, words).auto_target is None
    assert auto_state(frame, [word for word in words if word.normalized != "auto"]) is None
    frame[652:703, 1160:1270] = 80
    assert auto_state(frame, words) is None


def test_zero_boss_hp_does_not_prove_victory_or_surviving_students():
    frame, words = capture()
    words = [replace(word, text="0/2,400,000") if word.text == "2,028,272/2,400,000" else word for word in words]
    result = classify_battle(frame, words)
    assert result.boss_hp == 0 and result.won is None
    assert result.surviving_striker_ids is None


@pytest.mark.parametrize("local_ocr", [False, True])
def test_confirmed_win_layout_records_elapsed_time_without_inventing_survival(local_ocr):
    frame, words = capture("assault-battle-result")
    result = classify_battle(frame, StartupVision().read(frame) if local_ocr else words)
    assert result.kind == "result" and result.elapsed_seconds == 269.667
    assert result.remaining_seconds is None and result.won is True
    assert result.surviving_striker_ids is None
    assert result.damage_target == (1055, 665)
    assert result.confirm_target == result.target == (1170, 665)


@pytest.mark.parametrize("local_ocr", [False, True])
def test_damage_report_maps_exact_variant_names_and_zero_damage(local_ocr):
    frame, words = capture("assault-damage-report")
    result = classify_battle(frame, StartupVision().read(frame) if local_ocr else words)
    assert result.kind == "damage" and result.target == (932, 122)
    assert result.damage_by_student == {
        "Aris (Maid)": 2838775, "Kayoko (New Year)": 247026,
        "Koyuki": 1195988, "Izuna (Swimsuit)": 697317,
        "Ako": 0, "Yuzu (Armed)": 75146,
    }


@pytest.mark.parametrize("missing", ["247026", "(New Year)", "0", "Ako"])
def test_unreadable_damage_cannot_select_a_striker(missing):
    frame, words = capture("assault-damage-report")
    result = classify_battle(frame, [word for word in words if word.text != missing])
    if missing == "(New Year)":
        # Reading a different exact name still fails the policy's full-team match.
        assert "Kayoko (New Year)" not in result.damage_by_student
    else:
        assert result.damage_by_student == {}


def test_dimmed_result_cannot_authorize_confirm_through_damage_overlay():
    frame, words = capture("assault-battle-result")
    assert classify_battle((frame * .4).astype(np.uint8), words).kind == "unknown"
    frame, words = capture("assault-damage-report")
    assert classify_battle((frame * .4).astype(np.uint8), words).kind == "unknown"


def test_centered_loss_confirm_cannot_match_the_verified_win_layout():
    frame, words = capture("assault-battle-result")
    frame[627:702, 1020:1245] = 128
    words = [replace(word, box=(577, 636, 700, 683)) if word.normalized == "confirm" else word
             for word in words]
    result = classify_battle(frame, words)
    assert result.kind == "unknown" and result.won is None


def offline_loss_layout():
    """Synthetic layout guard, not a claim that a defeat was live captured.

    The centered Confirm coordinates come from BAAS's separate English loss
    recognizer. Other controls reuse our sanitized current victory capture.
    """
    frame, words = capture("assault-battle-result")
    report_button = frame[627:702, 1020:1095].copy()
    confirm_button = cv2.resize(frame[634:696, 1101:1238], (123, 62))
    heading = frame[15:66, 50:470]
    frame[15:66, 50:470] = cv2.cvtColor(cv2.cvtColor(heading, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    frame[627:702, 1020:1245] = 128
    frame[627:702, 500:575] = report_button
    frame[634:696, 577:700] = confirm_button
    words = [replace(word, box=(577, 636, 700, 683)) if word.normalized == "confirm" else word
             for word in words]
    return frame, words


def test_offline_centered_loss_layout_exposes_only_defeat_and_observed_report_control():
    frame, words = offline_loss_layout()
    result = classify_battle(frame, words)
    assert result.kind == "result" and result.won is False
    assert result.confirm_target == (638, 659)
    assert result.damage_target == (537, 667)
    assert result.surviving_striker_ids is None


@pytest.mark.parametrize("missing", ["battle complete", "striker", "special", "confirm"])
def test_partial_offline_defeat_layout_remains_unknown(missing):
    frame, words = offline_loss_layout()
    result = classify_battle(frame, [word for word in words if word.normalized != missing])
    assert result.kind == "unknown" and result.won is None


@pytest.mark.parametrize("change", ["gold_title", "no_report", "two_reports", "dimmed", "extra_confirm"])
def test_ambiguous_or_obscured_offline_defeat_never_authorizes_result_controls(change):
    frame, words = offline_loss_layout()
    if change == "gold_title":
        original, _ = capture("assault-battle-result")
        frame[15:66, 50:470] = original[15:66, 50:470]
    elif change == "no_report":
        frame[627:702, 500:575] = 245  # A plain white button is not Damage Report.
    elif change == "two_reports":
        frame[627:702, 900:975] = frame[627:702, 500:575]
    elif change == "dimmed":
        frame = (frame * .4).astype(np.uint8)
    else:
        words.append(Word("Confirm", .99, (1120, 646, 1222, 681)))
    result = classify_battle(frame, words)
    assert result.kind == "unknown" and result.won is None


def test_report_icon_search_finds_live_glyph_without_assuming_result_layout():
    frame, _ = capture("assault-battle-result")
    assert damage_control(frame) == (1057, 667)
