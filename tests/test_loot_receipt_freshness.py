"""Fast freshness checks against sanitized live receipt frames."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from ba_automator import loot_receipts as lr
from ba_automator.vision import VisionError, Word, decode_frame


FIXTURES = Path(__file__).parent / "fixtures"
DAILY = lr.Page(
    "reward",
    (
        lr.Card((458, 254, 148, 222), 18, "Pyroxenes", b""),
        lr.Card((620, 254, 148, 222), 70, "Tactical Challenge Coin", b""),
    ),
)


def daily_frames():
    return [
        (FIXTURES / f"loot-daily-receipt-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]


def test_same_daily_loot_survives_gem_animation_and_background_changes():
    before, after = daily_frames()
    a, b = decode_frame(before), decode_frame(after)
    assert not lr.same_pixels(a[325:410, 480:585], b[325:410, 480:585])
    b[0:100, 0:200] = 120  # The game remains animated behind this overlay.
    assert lr.same_receipt_view(before, lr.encode(b), DAILY)


@pytest.mark.parametrize("change", ["name", "quantity", "heading", "position", "overlay"])
def test_receipt_changes_cannot_authorize_input(change):
    before, after = daily_frames()
    image = decode_frame(after)
    if change == "name":
        image[264:295, 630:700] = 255
    elif change == "quantity":
        image[434:454, 660:730] = 255
    elif change == "heading":
        image[135:160, 400:600] = 0
    elif change == "position":
        image[254:476, 625:773] = image[254:476, 620:768].copy()
        image[254:476, 620:625] = 0
    else:
        image[220:490, 420:810] = (80, 80, 80)
    assert not lr.same_receipt_view(before, lr.encode(image), DAILY)


def test_unreadable_name_requires_unchanged_artwork_too():
    before, after = daily_frames()
    unknown = replace(DAILY, cards=(replace(DAILY.cards[0], name=None), DAILY.cards[1]))
    assert not lr.same_receipt_view(before, after, unknown)


@pytest.mark.parametrize("field", ["name", "quantity"])
@pytest.mark.parametrize("reading,confidence,accepted", [
    ("exact", .99, True), ("different", .99, False),
    ("exact", .84, False), ("missing", 1, False),
])
def test_changed_card_text_requires_exact_confident_reparse(
    monkeypatch, field, reading, confidence, accepted
):
    before, after = daily_frames()
    image = decode_frame(after)
    if field == "name":
        image[262:288, 634:660] = 235
        text = "Tactical Challenge Coin" if reading == "exact" else "Other Coin"
    else:
        image[432:458, 634:660] = 235
        text = "x70" if reading == "exact" else "x71"
    words = [] if reading == "missing" else [Word(text, confidence, (0, 0, 20, 20))]
    calls = []

    def read_crop(*args):
        calls.append(True)
        return words

    monkeypatch.setattr(lr, "read_crop", read_crop)
    assert lr.same_receipt_view(before, lr.encode(image), DAILY, vision=object()) is accepted
    assert calls == [True]


def test_named_card_identity_allows_animation_but_not_changed_name_count_or_size():
    before, after = (decode_frame(png) for png in daily_frames())
    cards = [
        replace(DAILY.cards[0], icon=lr.encode(image[325:410, 480:585]))
        for image in (before, after)
    ]
    assert not lr.same_icon(cards[0].icon, cards[1].icon)
    assert lr.same_card(*cards)
    assert not lr.same_card(*(replace(card, quantity=None) for card in cards))
    for changed in (
        replace(cards[1], name="Other item"),
        replace(cards[1], quantity=19),
        replace(cards[1], box=(458, 254, 146, 222)),
    ):
        assert not lr.same_card(cards[0], changed)


@pytest.mark.parametrize(
    "name", [None, "Pyro-", "Pyro…", "Pyro...", "Tech Notes (Millennium", "Box [Choice"]
)
def test_incomplete_names_never_allow_changed_icons(name):
    before, after = (decode_frame(png) for png in daily_frames())
    cards = [
        replace(DAILY.cards[0], name=name, icon=lr.encode(image[325:410, 480:585]))
        for image in (before, after)
    ]
    assert not lr.complete_name(name)
    assert not lr.same_card(*cards)


def test_unchanged_artwork_cannot_hide_two_different_complete_names():
    original = replace(DAILY.cards[0], icon=b"identical artwork")
    assert not lr.same_card(original, replace(original, name="Other item"))


@pytest.mark.parametrize(
    "filename,kind,change_box",
    [
        ("loot-sweep.png", "sweep", (400, 450, 30, 30)),
        ("loot-full-list.png", "grid", (345, 200, 30, 30)),
        ("lesson-receipt.png", "lesson", (450, 435, 30, 30)),
    ],
)
def test_fixed_receipt_panels_reject_changed_loot(filename, kind, change_box):
    before = (FIXTURES / filename).read_bytes()
    after = decode_frame(before)
    after[0:50, 0:50] = 80
    assert lr.same_receipt_view(before, lr.encode(after), lr.Page(kind))
    x, y, w, h = change_box
    after[y : y + h, x : x + w] = 0
    assert not lr.same_receipt_view(before, lr.encode(after), lr.Page(kind))


def sweep_dismiss_frames():
    return [
        (FIXTURES / f"loot-sweep-dismiss-pulse-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]


def test_sweep_tooltip_dismiss_pulse_does_not_change_final_rewards():
    before, after = sweep_dismiss_frames()
    first, second = (decode_frame(png) for png in (before, after))
    # The exact failed refresh pair: the fading pulse is outside all reward
    # cards and Confirm, but inside the old, overly broad comparison panel.
    assert not lr.same_pixels(first[440:630, 191:1088], second[440:630, 191:1088])
    assert not lr.same_pixels(first[520:630, 191:370], second[520:630, 191:370])
    assert lr.same_receipt_view(before, after, lr.Page("sweep"))


@pytest.mark.parametrize("change_box", [
    pytest.param((392, 465, 30, 28), id="first-card-art"),
    pytest.param((737, 462, 30, 28), id="last-card-art"),
    pytest.param((417, 496, 17, 13), id="quantity"),
    pytest.param((517, 85, 120, 26), id="heading"),
    pytest.param((573, 568, 110, 31), id="confirm"),
])
def test_sweep_pulse_exclusion_still_rejects_changed_receipt(change_box):
    before, after = sweep_dismiss_frames()
    changed = decode_frame(after)
    x, y, w, h = change_box
    changed[y:y + h, x:x + w] = 0
    assert not lr.same_receipt_view(before, lr.encode(changed), lr.Page("sweep"))


def test_tooltip_must_still_be_the_identical_popup():
    before = (FIXTURES / "loot-tooltip-0.png").read_bytes()
    after = decode_frame(before)
    boxes = lr.tooltip_boxes(after)
    assert len(boxes) == 1
    after[0:50, 0:50] = 80
    assert lr.same_receipt_view(before, lr.encode(after), None)
    x, y, w, h = boxes[0]
    after[y + 15 : y + 35, x + 30 : x + 70] = 0
    assert not lr.same_receipt_view(before, lr.encode(after), None)
    assert not lr.same_receipt_view(
        before, (FIXTURES / "loot-sweep.png").read_bytes(), None
    )


def test_unknown_and_wrong_size_frames_cannot_be_refreshed():
    before, after = daily_frames()
    assert not lr.same_receipt_view(before, after, None)
    with pytest.raises(VisionError, match="1280×720"):
        lr.same_receipt_view(
            before, lr.encode(np.zeros((100, 100, 3), np.uint8)), DAILY
        )


def test_scroll_rebound_must_settle_before_reading_labels():
    before, after = [
        (FIXTURES / f"loot-assault-points-rebound-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    assert not lr.reward_layout_stable(before, after)
    assert lr.reward_layout_stable(after, after)
    stationary = [
        (FIXTURES / f"loot-assault-points-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    assert lr.reward_layout_stable(*stationary)
