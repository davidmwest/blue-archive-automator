"""Offline cafe decisions: recognized controls, honest receipts, and exact invitations."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import ba_automator.cafe as cafe_module
from ba_automator.cafe import (CafeRunner, PAN_LEFT, earnings_amounts, find_button, reward_amounts,
                               invitation_list, invitation_rows, floor_from_switch)
from ba_automator.cafe_vision import CafeVision
from ba_automator.config import Config
from ba_automator.restart import RestartError
from ba_automator.vision import Observation, Word


def word(text, x=640, y=300):
    return Word(text, .99, (x - 35, y - 10, x + 35, y + 10))


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Device:
    def __init__(self, package, png):
        self.package = package
        self.foreground = package
        self.png = png
        self.taps = []
        self.swipes = []

    def connect(self):
        pass

    def verify_package(self):
        pass

    def screenshot(self):
        return self.png

    def foreground_package(self):
        return self.foreground

    def tap(self, x, y, *, deadline=None, monotonic=None):
        if deadline is not None and monotonic() > deadline:
            return False
        self.taps.append((x, y))
        return True

    def swipe(self, start, end, *, duration_ms=400, deadline=None, monotonic=None):
        if deadline is not None and monotonic() > deadline:
            return False
        self.swipes.append((start, end))
        return True


@pytest.fixture
def cafe(tmp_path, monkeypatch):
    selected = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                      run_dir=tmp_path / "runs", lock_dir=tmp_path / "locks", state_dir=tmp_path / "state")
    clock = Clock()
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, encoded = cv2.imencode(".png", image)
    png = encoded.tobytes()
    device = Device(selected.package, png)
    vision = SimpleNamespace(visitor_notice=lambda image, words: None, is_cafe=lambda image: True, words=lambda image: [], markers=lambda image: [],
                             relationship_feedback=lambda before, after, target: False)
    startup = SimpleNamespace(analyze=lambda png: Observation("home", "verified home"))
    monkeypatch.setattr(cafe_module, "CafeVision", lambda startup: vision)
    runner = CafeRunner(selected, device, startup, monotonic=clock.monotonic, sleep=clock.sleep)
    runner.floor = 1

    def capture(words=()):
        vision.words = lambda image: list(words)
        return clock.now, png, image, list(words)

    def script(*screens):
        index = 0

        def next_capture(**kwargs):
            nonlocal index
            words = screens[min(index, len(screens) - 1)]
            index += 1
            return capture(words)

        monkeypatch.setattr(runner, "capture", next_capture)

    try:
        yield SimpleNamespace(runner=runner, device=device, clock=clock, vision=vision,
                              capture=capture, script=script, startup=startup, config=selected)
    finally:
        if not runner.journal.stream.closed:
            runner.journal.close()


def action_records(config):
    path = config.state_dir / "important-actions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def earnings_words(*values, claim=True):
    words = [word("Cafe Earnings", 640, 180), word("Earnings Status", 640, 240)]
    for x, text in zip((415, 640, 870), values):
        words.append(word(text, x, 438))
    if claim:
        words.append(word("Claim", 640, 590))
    return words


def invite_list_words(name="Hoshino", *, variant=None, row=0, button="Invite"):
    y = 222 + row * 78
    words = [word("MomoTalk", 600, 100), word(name, 560, y - 10), word(button, 787, y)]
    if variant:
        words.append(word(variant, 560, y + 13))
    return words


def invitation_dialog(name="Hoshino"):
    return [word("Free Invitation", 640, 180), word(name, 640, 300), word("Confirm", 640, 505)]


def test_storage_amounts_require_fixed_columns_and_ignore_unreadable_values():
    values = earnings_amounts([
        word("1,250 / 100,000", 415, 438), word("not readable", 640, 438),
        word("2 345 / 200 000", 870, 438), word("999 / 999", 640, 250),
    ])
    assert values == {"credits1": 1250, "credits2": 2345}


def test_conflicting_ocr_in_one_storage_column_remains_unknown():
    values = earnings_amounts([
        word("500 / 10,000", 415, 438), word("0 / 10,000", 415, 438),
        word("0 / 10,000", 415, 438), word("0 / 300", 640, 438),
        word("0 / 10,000", 870, 438),
    ])
    assert values == {"ap": 0, "credits2": 0}


def test_malformed_storage_numerators_do_not_turn_into_zero_or_raise():
    assert earnings_amounts([word(", / 1,000", 415, 438), word("0 / ,", 640, 438)]) == {}


def test_reward_receipt_amounts_require_matching_labels_and_positions():
    words = [word("AP", 520, 280), word("Credit Points", 780, 280),
             word("× 123", 520, 440), word("x45,678", 780, 440), word("999", 100, 440)]
    assert reward_amounts(words) == {"ap": 123, "credits": 45678}
    assert reward_amounts([word("× 123", 520, 440)]) == {}


def test_malformed_reward_receipt_amounts_stay_unknown():
    assert reward_amounts([word("AP", 520, 280), word("× ,", 520, 440)]) == {}
    assert reward_amounts([word("Credit Points", 780, 280), word(",,", 780, 440)]) == {}


def test_conflicting_reward_receipt_amounts_are_not_reported_as_zero_or_last_value():
    words = [word("AP", 520, 280), word("Credit Points", 780, 280),
             word("×123", 520, 440), word("×0", 520, 440), word("×0", 520, 440),
             word("×5,000", 780, 440)]
    assert reward_amounts(words) == {"credits": 5000}


def test_consistent_duplicate_reward_ocr_does_not_double_count():
    words = [word("AP", 520, 280), word("×123", 520, 440), word("×123", 520, 440)]
    assert reward_amounts(words) == {"ap": 123}


def test_duplicate_claim_buttons_are_not_an_authorized_target():
    assert find_button([word("Claim", 500, 590), word("Claim", 750, 590)], {"claim"}) is None


def test_two_zero_columns_do_not_silently_skip_an_unread_third_column(cafe, monkeypatch):
    runner = cafe.runner
    screens = iter([
        cafe.capture(earnings_words("0 / 10000", "0 / 300")),
        cafe.capture([word("Reward Acquired", 640, 180), word("Credit Points", 780, 280), word("x500", 780, 440)]),
        cafe.capture(earnings_words("0 / 10000", "0 / 300", "0 / 10000")),
    ])

    def wait_words(predicate, **kwargs):
        screen = next(screens)
        assert predicate(screen[3])
        return screen

    monkeypatch.setattr(runner, "wait_cafe", lambda **kwargs: cafe.capture())
    monkeypatch.setattr(runner, "wait_words", wait_words)
    runner.collect()
    assert cafe.device.taps == [(1165, 655), (640, 590), (640, 630), (982, 145)]
    records = action_records(cafe.config)
    assert [record["action"] for record in records] == ["earnings_claim_attempted", "earnings_collected"]
    assert records[-1]["credits"] == 500


def test_all_three_known_zero_columns_skip_claim_without_recording_collection(cafe, monkeypatch):
    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    monkeypatch.setattr(cafe.runner, "wait_words", lambda predicate, **kwargs:
                        cafe.capture(earnings_words("0 / 10000", "0 / 300", "0 / 10000")))
    cafe.runner.collect()
    assert cafe.device.taps == [(1165, 655), (982, 145)]
    assert action_records(cafe.config) == []


def test_ambiguous_claim_stops_before_pressing_any_claim_button(cafe, monkeypatch):
    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    monkeypatch.setattr(cafe.runner, "wait_words", lambda predicate, **kwargs:
                        cafe.capture(earnings_words("500 / 10000") + [word("Claim", 800, 590)]))
    with pytest.raises(RestartError, match="unambiguous Claim"):
        cafe.runner.collect()
    assert cafe.device.taps == [(1165, 655)]
    assert action_records(cafe.config) == []


def test_missing_reward_receipt_never_logs_successful_collection(cafe, monkeypatch):
    calls = 0

    def wait_words(predicate, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return cafe.capture(earnings_words("500 / 10000"))
        cafe.runner.fail("Expected Cafe dialog did not appear")

    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    monkeypatch.setattr(cafe.runner, "wait_words", wait_words)
    with pytest.raises(RestartError):
        cafe.runner.collect()
    assert [record["action"] for record in action_records(cafe.config)] == ["earnings_claim_attempted"]


def test_pan_never_sends_a_swipe_without_verified_cafe_hud(cafe):
    cafe.vision.is_cafe = lambda image: False
    cafe.script([])
    with pytest.raises(RestartError, match="unobstructed"):
        cafe.runner.pan(PAN_LEFT)
    assert cafe.device.swipes == []
    assert cafe.device.taps == []


def test_verified_cafe_pan_uses_the_requested_camera_vector(cafe):
    cafe.script([])
    cafe.runner.pan(PAN_LEFT)
    assert cafe.device.swipes == [PAN_LEFT]


def test_cafe_hud_requires_all_three_bright_anchors():
    vision = CafeVision.__new__(CafeVision)
    random = np.random.default_rng(27)
    vision.assets = {name: random.integers(70, 240, (10, 10, 3), dtype=np.uint8)
                     for name in ("title", "edit", "comfort")}
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for name, x, y in (("title", 102, 6), ("edit", 70, 658), ("comfort", 964, 626)):
        image[y:y + 10, x:x + 10] = vision.assets[name]
    assert vision.is_cafe(image)
    assert not vision.is_cafe((image.astype(float) * .5).astype(np.uint8))
    image[626:660, 964:1062] = 0
    assert not vision.is_cafe(image)


def test_stale_cafe_frame_never_sends_input_or_counts_an_action(cafe):
    screen = cafe.capture()
    cafe.clock.now += 6
    with pytest.raises(RestartError, match="expired"):
        cafe.runner.tap(screen, (640, 400), "test tap")
    assert cafe.device.taps == []
    assert cafe.runner.actions == 0


def test_wrong_foreground_stops_before_ocr_or_game_input(cafe):
    cafe.device.foreground = "com.android.vending"
    cafe.vision.words = lambda image: pytest.fail("OCR should not run after foreground validation fails")
    with pytest.raises(RestartError, match="foreground"):
        cafe.runner.capture(ocr=True)
    assert cafe.device.taps == []


def test_disabled_invitation_never_opens_invitation_ui(cafe):
    cafe.runner.invite()
    assert cafe.device.taps == []


def test_invitation_cooldown_skips_opening_the_selector(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([word("15:30:22", 890, 595)])
    cafe.runner.invite()
    assert cafe.device.taps == []
    assert action_records(cafe.config) == []


def test_paid_invitation_dialog_never_selects_or_confirms_student(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([], [word("Invitation", 640, 180), word("Use 100 pyroxenes", 640, 300),
                    word("Hoshino", 400, 350)])
    with pytest.raises(RestartError, match="purchase|free invitation"):
        cafe.runner.invite()
    assert cafe.device.taps == [(883, 652)]


@pytest.mark.parametrize("configured, wrong", [("Aru", "Haruna"), ("Hoshino", "Hoshino (Swimsuit)")])
def test_invitation_confirmation_must_name_the_exact_configured_student(cafe, configured, wrong):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student=configured)
    cafe.script([], invite_list_words(configured), invitation_dialog(wrong))
    with pytest.raises(RestartError):
        cafe.runner.invite()
    assert cafe.device.taps == [(883, 652), (787, 222)]
    assert action_records(cafe.config) == []


def test_exact_free_invitation_records_success_only_after_new_cooldown(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([], invite_list_words(), invitation_dialog(),
                invite_list_words(button="Invited"), [], [word("19:59:59", 890, 595)])
    assert cafe.runner.invite() is True
    assert cafe.device.taps == [(883, 652), (787, 222), (640, 505), (837, 95)]
    records = action_records(cafe.config)
    assert [record["action"] for record in records] == ["invitation_attempted", "student_invited"]
    assert all(record["student"] == "Hoshino" for record in records)


def test_wrapped_variant_is_part_of_its_row_identity_and_uses_row_button():
    words = invite_list_words("Hoshino", variant="(Swimsuit)")
    rows = invitation_rows(words)
    assert rows == [{"identity": "hoshino swimsuit", "target": (787, 222), "enabled": True}]
    assert invitation_list(words)


def test_invitation_rows_do_not_join_neighbouring_students():
    words = invite_list_words("Hoshino", variant="(Swimsuit)") + invite_list_words("Haruna", row=1)[1:]
    assert [row["identity"] for row in invitation_rows(words)] == ["hoshino swimsuit", "haruna"]


def test_invitation_waits_for_transition_and_ignores_background_bonus_control(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    background = [word("Invitation", 883, 652), word("Bonus Invitation", 1165, 655)]
    cafe.script(background, background, invite_list_words() + background, invitation_dialog(),
                invite_list_words(button="Invited") + background, [word("19:59:59", 890, 595)])
    cafe.runner.invite()
    assert cafe.clock.now >= .5
    assert cafe.device.taps == [(883, 652), (787, 222), (640, 505), (837, 95)]


def test_known_cooldown_notice_is_dismissed_without_attempting_an_invitation(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([], [word("You can send an invite after the cooldown is over.", 640, 360),
                     word("Confirm", 640, 505)], [])
    assert cafe.runner.invite() is False
    assert cafe.device.taps == [(883, 652), (640, 505)]
    assert action_records(cafe.config) == []


def test_invitation_scrolls_inside_the_list_before_tapping_exact_wrapped_variant(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino (Swimsuit)")
    cafe.script([], invite_list_words("Haruna"), invite_list_words("Hoshino", variant="(Swimsuit)", row=1),
                invitation_dialog("Hoshino (Swimsuit)"), invite_list_words("Haruna"),
                [word("19:59:59", 890, 595)])
    cafe.runner.invite()
    assert cafe.device.swipes == [((650, 550), (650, 250))]
    assert cafe.device.taps == [(883, 652), (787, 300), (640, 505), (837, 95)]


def test_generic_normal_confirmation_is_tied_to_previously_verified_exact_row(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([], invite_list_words(), [word("Would you like to invite this student?", 640, 330),
                                         word("Confirm", 640, 505)], [word("19:59:59", 890, 595)])
    cafe.runner.invite()
    assert [record["action"] for record in action_records(cafe.config)] == ["invitation_attempted", "student_invited"]


@pytest.mark.parametrize("message", ["Would you like to replace the student in the other Cafe?",
                                      "Would you like to spend 100 pyroxenes?"])
def test_other_cafe_replacement_or_paid_confirmation_is_never_accepted(cafe, message):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([], invite_list_words(), [word(message, 640, 330), word("Confirm", 640, 505)])
    with pytest.raises(RestartError):
        cafe.runner.invite()
    assert cafe.device.taps == [(883, 652), (787, 222)]
    assert action_records(cafe.config) == []


def test_confirmation_overlay_prevents_tapping_the_lists_background_close(cafe):
    words = invite_list_words() + [word("Cancel", 500, 505), word("Confirm", 780, 505)]
    assert not invitation_list(words)


def test_invitation_success_is_not_recorded_without_new_cooldown(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([], invite_list_words(), invitation_dialog(), invite_list_words(button="Invited"), [])
    with pytest.raises(RestartError, match="cooldown"):
        cafe.runner.invite()
    assert [record["action"] for record in action_records(cafe.config)] == ["invitation_attempted"]
    assert cafe.device.taps.count((837, 95)) == 1


def test_ambiguous_rows_never_press_either_invite_control(cafe):
    cafe.runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    cafe.script([], invite_list_words() + invite_list_words(row=1)[1:])
    with pytest.raises(RestartError, match="ambiguous"):
        cafe.runner.invite()
    assert cafe.device.taps == [(883, 652)]


def test_cafe_does_not_report_success_from_stale_home_ocr(cafe, monkeypatch):
    prepare_two_floor_run(cafe, monkeypatch)
    for method in ("invite", "sweep"):
        monkeypatch.setattr(cafe.runner, method, lambda: None)

    def slow_home(png):
        cafe.clock.now += 6
        return Observation("home", "stale home")

    cafe.startup.analyze = slow_home
    with pytest.raises(RestartError, match="home"):
        cafe.runner.run()
    assert not any(record["action"] == "cafe_visit_completed" for record in action_records(cafe.config))


@pytest.mark.parametrize("heart, rank_up, count", [(True, True, 1), (False, True, 1),
                                                  (True, False, 1), (False, False, 0)])
def test_each_student_tap_counts_heart_or_rank_up_once(cafe, monkeypatch, heart, rank_up, count):
    markers = iter([[(.99, (600, 300))], [], [], []])
    cafe.vision.markers = lambda image: next(markers)
    cafe.vision.relationship_feedback = lambda before, after, target: heart
    cafe.vision.is_cafe = lambda image: not rank_up
    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    monkeypatch.setattr(cafe.runner, "capture", lambda **kwargs:
                        cafe.capture([word("Relationship rank up!", 640, 600)] if rank_up else []))
    cafe.runner.pet_visible()
    assert cafe.runner.confirmed == count
    assert cafe.device.taps == [(600, 300)] + ([(640, 630)] if rank_up else [])
    events = [json.loads(line) for line in
              (cafe.runner.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    interactions = [event for event in events if event["event"] == "student_interaction"]
    assert len(interactions) == 1
    assert interactions[0]["confirmed"] is bool(count)
    assert interactions[0]["heart_feedback"] is heart
    assert interactions[0]["rank_increased"] is rank_up


def test_generic_relationship_notice_does_not_prove_an_increase(cafe, monkeypatch):
    cafe.vision.is_cafe = lambda image: False
    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    cafe.script([word("Relationship Rank", 640, 250), word("Confirm", 640, 505)])
    assert cafe.runner.clear_relationship_popup() is False
    assert cafe.runner.confirmed == 0
    assert cafe.device.taps == [(640, 505)]


def test_rankup_saves_observed_values_and_verified_name_context(cafe, monkeypatch):
    cafe.vision.is_cafe = lambda image: False
    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    cafe.script([word("Relationship rank up!", 640, 600), word("3", 640, 540),
                 word("ATK +8", 640, 681)])
    assert cafe.runner.clear_relationship_popup(student="Tsurugi", student_evidence="gift-confirm.png")
    event = action_records(cafe.config)[-1]
    assert event["action"] == "relationship_rank_increased"
    assert event["student"] == "Tsurugi" and event["rank"] == 3
    assert event["stats"] == [{"name": "ATK", "before": None, "after": None, "delta": 8}]
    assert Path(event["evidence"]).is_file()
    assert event["student_evidence"] == "gift-confirm.png"


def test_extra_rank_ocr_recaptures_stale_overlay_before_dismissal(cafe, monkeypatch):
    cafe.vision.is_cafe = lambda image: False
    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    cafe.script([word("Relationship rank up!", 640, 600), word("ATK +8", 640, 681)])

    def slow_crop(image):
        cafe.clock.sleep(6)
        return [word("3", 80, 80)]

    cafe.startup.read = slow_crop
    assert cafe.runner.clear_relationship_popup()
    assert cafe.device.taps == [(640, 630)]
    assert action_records(cafe.config)[-1]["rank"] == 3


def test_stale_rankup_followed_by_other_screen_sends_no_dismissal(cafe, monkeypatch):
    cafe.vision.is_cafe = lambda image: False
    cafe.script([word("Relationship rank up!", 640, 600)], [word("Confirm", 640, 505)])

    def slow_crop(image):
        cafe.clock.sleep(6)
        return [word("3", 80, 80)]

    cafe.startup.read = slow_crop
    with pytest.raises(RestartError, match="celebration changed"):
        cafe.runner.clear_relationship_popup()
    assert cafe.device.taps == []


def test_same_lingering_relationship_popup_is_logged_once(cafe, monkeypatch):
    cafe.vision.is_cafe = lambda image: False
    monkeypatch.setattr(cafe.runner, "wait_cafe", lambda **kwargs: cafe.capture())
    cafe.script([word("Relationship rank up!", 640, 600), word("3", 640, 540),
                 word("ATK +8", 640, 681)])
    cafe.runner.clear_relationship_popup()
    cafe.runner.clear_relationship_popup()
    assert len([event for event in action_records(cafe.config)
                if event["action"] == "relationship_rank_increased"]) == 1
    assert cafe.device.taps == [(640, 630), (640, 630)]


def prepare_two_floor_run(cafe, monkeypatch):
    runner = cafe.runner
    runner.config = replace(cafe.config, cafe_invite_enabled=True, cafe_invite_student="Hoshino")
    monkeypatch.setattr(runner, "enter", lambda: None)
    monkeypatch.setattr(runner, "collect", lambda: None)
    observed_floor = 1
    send_tap = cafe.device.tap

    def tap(x, y, **kwargs):
        nonlocal observed_floor
        sent = send_tap(x, y, **kwargs)
        if sent and (x, y) == (132, 103):
            observed_floor = 3 - observed_floor
        return sent

    monkeypatch.setattr(cafe.device, "tap", tap)
    monkeypatch.setattr(runner, "capture", lambda **kwargs:
                        cafe.capture([word(f"Move to Cafe No. {3 - observed_floor}")]))
    monkeypatch.setattr(runner, "wait_cafe", lambda **kwargs: cafe.capture())


def test_optional_invitation_failure_happens_after_both_required_floor_scans(cafe, monkeypatch):
    prepare_two_floor_run(cafe, monkeypatch)
    events = []
    monkeypatch.setattr(cafe.runner, "sweep", lambda: events.append(("scan", cafe.runner.floor)))

    def fail_invite():
        events.append(("invite", cafe.runner.floor))
        cafe.runner.fail("Unrecognized optional invitation")

    monkeypatch.setattr(cafe.runner, "invite", fail_invite)
    with pytest.raises(RestartError, match="optional invitation"):
        cafe.runner.run()
    assert events == [("scan", 1), ("scan", 2), ("invite", 2)]


@pytest.mark.parametrize("success_floor", [None, 1, 2])
def test_successful_invitation_rescans_its_floor_and_cooldown_skips_do_not(cafe, monkeypatch, success_floor):
    prepare_two_floor_run(cafe, monkeypatch)
    events = []
    monkeypatch.setattr(cafe.runner, "sweep", lambda: events.append(("scan", cafe.runner.floor)))

    def invite():
        events.append(("invite", cafe.runner.floor))
        return cafe.runner.floor == success_floor

    monkeypatch.setattr(cafe.runner, "invite", invite)
    assert cafe.runner.run().status == "success"
    expected = [("scan", 1), ("scan", 2), ("invite", 2)]
    if success_floor == 2:
        expected.append(("scan", 2))
    else:
        expected.append(("invite", 1))
        if success_floor == 1:
            expected.append(("scan", 1))
    assert events == expected
    assert action_records(cafe.config)[-1]["detail"] == "Cafe visit complete; 0 relationship increases verified."


@pytest.mark.parametrize("labels, expected", [
    (["Move to Cafe No. 2"], 1), (["Move to Cafe No. 1"], 2),
    ([], None), (["Move to Cafe No. 12"], None),
    (["Move to Cafe No. 1", "Move to Cafe No. 2"], None),
])
def test_floor_identity_requires_one_unambiguous_switch_destination(labels, expected):
    assert floor_from_switch([word(label) for label in labels]) == expected


def test_floor_recognition_retries_transient_missing_or_ambiguous_labels(cafe):
    cafe.script([], [word("Move to Cafe No. 1"), word("Move to Cafe No. 2")],
                [word("Move to Cafe No. 2")])
    cap = cafe.runner.wait_floor(expected=1)
    assert floor_from_switch(cap[3]) == 1
    assert cafe.clock.now >= 1.2
    assert cafe.device.taps == []


def test_floor_label_under_an_obstructed_cafe_does_not_authorize_a_switch(cafe):
    cafe.vision.is_cafe = lambda image: False
    cafe.script([word("Move to Cafe No. 2")])
    with pytest.raises(RestartError, match="floor availability could not be verified"):
        cafe.runner.wait_floor(expected=1)
    assert cafe.device.taps == []


@pytest.mark.parametrize("labels", [[], [word("Move to Cafe No. 1"), word("Move to Cafe No. 2")]])
def test_unknown_initial_floor_stops_before_collection_or_student_actions(cafe, monkeypatch, labels):
    monkeypatch.setattr(cafe.runner, "enter", lambda: None)
    monkeypatch.setattr(cafe.runner, "collect", lambda: pytest.fail("Unknown floor must stop before collection"))
    monkeypatch.setattr(cafe.runner, "sweep", lambda: pytest.fail("Unknown floor must stop before a scan"))
    cafe.script(labels)
    with pytest.raises(RestartError, match="floor availability could not be verified"):
        cafe.runner.run()
    assert cafe.device.taps == []
    assert action_records(cafe.config) == []


def test_missing_second_floor_switch_never_becomes_partial_success(cafe, monkeypatch):
    prepare_two_floor_run(cafe, monkeypatch)
    scans = []

    def scan():
        scans.append(cafe.runner.floor)
        cafe.script([])

    monkeypatch.setattr(cafe.runner, "sweep", scan)
    with pytest.raises(RestartError, match="floor availability could not be verified"):
        cafe.runner.run()
    assert scans == [1]
    assert cafe.device.taps == []
    assert not any(record["action"] == "cafe_visit_completed" for record in action_records(cafe.config))


def test_floor_change_waits_for_the_destination_label_before_scanning(cafe, monkeypatch):
    prepare_two_floor_run(cafe, monkeypatch)
    scans = []
    monkeypatch.setattr(cafe.runner, "sweep", lambda: scans.append(cafe.runner.floor))
    # Initial and pre-switch labels are valid, but the destination never appears.
    cafe.script([word("Move to Cafe No. 2")])
    with pytest.raises(RestartError, match="still on cafe_1 after 3 verified taps"):
        cafe.runner.run()
    assert scans == [1]
    assert cafe.device.taps == [(132, 103)] * 3
    assert cafe.runner.floor == 1
    assert not any(record["action"] == "cafe_visit_completed" for record in action_records(cafe.config))


def navigation_screen(cafe, monkeypatch, state):
    """Render the selected state on each fresh fake capture."""
    def capture(**kwargs):
        current = state()
        cafe.vision.is_cafe = lambda frame: current.startswith("cafe")
        cafe.startup.analyze = lambda png: Observation(current, current)
        floor = int(current[-1]) if current in {"cafe_1", "cafe_2"} else None
        return cafe.capture([word(f"Move to Cafe No. {3 - floor}")] if floor else [])
    monkeypatch.setattr(cafe.runner, "capture", capture)


def test_entry_retries_a_missed_tap_only_while_home_stays_verified(cafe, monkeypatch):
    navigation_screen(cafe, monkeypatch, lambda: "cafe_1" if len(cafe.device.taps) == 2 else "home")
    cafe.runner.enter()
    assert cafe.device.taps == [(100, 659)] * 2
    assert cafe.clock.now >= 5
    records = [json.loads(line) for line in (cafe.runner.run_dir / "events.jsonl").read_text().splitlines()]
    assert records[-1]["event"] == "navigation_arrived"
    assert records[-1]["attempts"] == 2
    assert (cafe.runner.run_dir / records[-1]["frame"]).is_file()


def test_entry_stops_after_three_unaccepted_taps(cafe, monkeypatch):
    navigation_screen(cafe, monkeypatch, lambda: "home")
    with pytest.raises(RestartError, match="still on home after 3 verified taps"):
        cafe.runner.enter()
    assert cafe.device.taps == [(100, 659)] * 3
    assert cafe.clock.now < 30


@pytest.mark.parametrize("during", ["popup", "unknown", "cafe_2"])
def test_entry_never_retries_over_a_dialog_or_after_arrival(cafe, monkeypatch, during):
    navigation_screen(cafe, monkeypatch, lambda: during if cafe.device.taps else "home")
    if during == "cafe_2":
        cafe.runner.enter()
    else:
        with pytest.raises(RestartError, match="unexpected"):
            cafe.runner.enter()
    assert cafe.device.taps == [(100, 659)]


def test_floor_navigation_allows_loading_longer_than_thirty_seconds(cafe, monkeypatch):
    def state():
        if not cafe.device.taps:
            return "cafe_1"
        return "loading" if cafe.clock.now < 45 else "cafe_2"
    navigation_screen(cafe, monkeypatch, state)
    cafe.runner.move_to_floor(2)
    assert cafe.runner.floor == 2
    assert cafe.device.taps == [(132, 103)]
    assert cafe.clock.now >= 45


def test_floor_navigation_does_not_retap_an_indefinite_loading_screen(cafe, monkeypatch):
    navigation_screen(cafe, monkeypatch, lambda: "loading" if cafe.device.taps else "cafe_1")
    with pytest.raises(RestartError, match="within 120s.*last screen: loading"):
        cafe.runner.move_to_floor(2)
    assert cafe.runner.floor == 1
    assert cafe.device.taps == [(132, 103)]
    assert 120 <= cafe.clock.now < 121


def test_floor_retry_requires_source_visible_continuously(cafe, monkeypatch):
    # A loading interruption resets the five-second source-screen confirmation.
    def state():
        if len(cafe.device.taps) >= 2:
            return "cafe_2"
        return "loading" if 4 <= cafe.clock.now < 6 else "cafe_1"
    navigation_screen(cafe, monkeypatch, state)
    cafe.runner.move_to_floor(2)
    assert cafe.device.taps == [(132, 103)] * 2
    assert cafe.clock.now >= 11


def test_floor_label_on_obstructed_screen_cannot_authorize_retry(cafe, monkeypatch):
    navigation_screen(cafe, monkeypatch, lambda: "popup" if cafe.device.taps else "cafe_1")
    cafe.vision.words = lambda frame: [word("Move to Cafe No. 2")]
    with pytest.raises(RestartError, match="unexpected popup"):
        cafe.runner.move_to_floor(2)
    assert cafe.device.taps == [(132, 103)]
    assert cafe.runner.floor == 1


@pytest.mark.parametrize("dismissals", [1, 2])
def test_visitor_notice_is_dismissed_with_popup_evidence_before_arrival(cafe, monkeypatch, dismissals):
    def state():
        if not cafe.device.taps:
            return "home"
        return "cafe_1" if len(cafe.device.taps) > dismissals else "unknown"
    navigation_screen(cafe, monkeypatch, state)
    cafe.vision.visitor_notice = lambda frame, words: (640, 458)
    cafe.runner.enter()
    assert cafe.device.taps == [(100, 659)] + [(640, 458)] * dismissals
    records = [json.loads(line) for line in (cafe.runner.run_dir / "events.jsonl").read_text().splitlines()]
    popups = [r for r in records if r['event'] == 'popup_dismissal']
    assert popups[-1]['result'] == 'changed'
    assert popups[-1]['after_state'] == 'cafe'
    assert (cafe.runner.run_dir / popups[-1]['before']).is_file()
    assert (cafe.runner.run_dir / popups[-1]['after']).is_file()
    if dismissals == 2:
        assert any(p['result'] == 'unchanged' for p in popups)
    assert records[-1]['event'] == 'navigation_arrived'


def test_visitor_notice_that_does_not_close_has_bounded_retries(cafe, monkeypatch):
    navigation_screen(cafe, monkeypatch, lambda: "unknown" if cafe.device.taps else "home")
    cafe.vision.visitor_notice = lambda frame, words: (640, 458)
    with pytest.raises(RestartError, match="notice remained after three"):
        cafe.runner.enter()
    assert cafe.device.taps == [(100, 659)] + [(640, 458)] * 3


def test_navigation_recaptures_slow_ocr_instead_of_retrying_on_old_frame(cafe, monkeypatch):
    navigation_screen(cafe, monkeypatch, lambda: "home")
    original = cafe.runner.capture
    def capture(**kwargs):
        result = original(**kwargs)
        def analyze(png):
            if cafe.device.taps:
                cafe.clock.sleep(4.5)
            return Observation("home", "home")
        cafe.startup.analyze = analyze
        return result
    monkeypatch.setattr(cafe.runner, 'capture', capture)
    with pytest.raises(RestartError, match="within 120s"):
        cafe.runner.enter()
    assert cafe.device.taps == [(100, 659)]
