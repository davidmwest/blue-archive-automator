"""Real receipt layouts plus an offline state machine with overlapping pages."""

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest
import numpy as np

from ba_automator import loot_receipts as lr
from ba_automator.config import Config
from ba_automator.runtime import Journal, TaskError
from ba_automator.vision import StartupVision, decode_frame, Word

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def vision():
    return StartupVision()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("loot-tooltip-0", "Heat Pack Blueprint"),
        ("loot-tooltip-1", "Insulated Messenger Bag Blueprint"),
        ("loot-tooltip-2", "Knitted Wool Hat Blueprint"),
        ("loot-tooltip-3", "General Amulet Blueprint"),
        ("loot-tooltip-4", "General Bag Blueprint"),
        ("loot-tooltip-credits", "Credit Points"),
        ("loot-tooltip-credit-divider", "Credit Points"),
    ],
)
def test_exact_tooltip_names_and_currency_without_owned(vision, name, expected):
    im = decode_frame((FIXTURES / (name + ".png")).read_bytes())
    assert lr.read_tooltip(im, vision) == expected
    assert lr.page((FIXTURES / (name + ".png")).read_bytes(), vision).kind == "tooltip"


def test_delayed_tooltip_is_expected_transition_but_not_the_same_input_evidence(vision):
    before, after = [
        (FIXTURES / f"loot-assault-points-delayed-tooltip-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    parsed = lr.page(before, vision)
    assert parsed.kind == "reward"
    assert parsed.cards[0].name == "Credit Points"
    assert not lr.has_tooltip(decode_frame(before))
    assert lr.has_tooltip(decode_frame(after))
    assert lr.read_tooltip(decode_frame(after), vision) == "Credit Points"
    assert not lr.same_receipt_view(before, after, parsed, vision=vision)


def test_final_row_only_and_owned_is_never_quantity(vision):
    p = lr.page((FIXTURES / "loot-sweep.png").read_bytes(), vision)
    assert p.kind == "sweep" and len(p.cards) == 9 and not p.clipped
    assert [c.quantity for c in p.cards] == [2, 1, 2, 2, 2, 2, 2, 1, 672]
    assert all(c.box[1] >= 449 for c in p.cards)
    assert lr.amount([Word("Owned: 436", 1, (0, 0, 20, 20))]) is None
    assert (
        lr.amount([Word("x1", 1, (0, 0, 20, 20)), Word("x2", 1, (0, 0, 20, 20))])
        is None
    )


@pytest.mark.parametrize(
    "name,names,qty",
    [
        ("red-dots-free-receipt", ["Credit Points", "AP"], [10000, 10]),
        ("craft-collected", ["Normal Tech Notes (Shanhaijing)"], [1]),
        ("tactical-time-receipt", ["Credit Points"], [70570]),
        ("cafe_receipt", ["Credit Points", "AP"], [73957, 81]),
        (
            "task-rewards-receipt-end",
            [
                "Intact Firing Pin",
                "Keystone",
                "Normal Activity Report",
                "Beginner Tech Notes (Gehenna)",
                "Beginner Tech Notes (Millennium)",
                "Beginner Tech Notes (Highlander)",
            ],
            [1, 1, 3, 2, 1, 1],
        ),
    ],
)
def test_labeled_cards(vision, name, names, qty):
    p = lr.page((FIXTURES / (name + ".png")).read_bytes(), vision)
    assert p.kind == "reward"
    assert [c.name for c in p.cards] == names
    assert [c.quantity for c in p.cards] == qty


def test_crafting_gift_sparkles_are_the_same_receipt_after_no_op_scroll(vision):
    """The live 21:20 collection failed after reading both gifts successfully.

    Tiny animated sparkles changed the cube artwork between end-of-list checks;
    complete labels and quantities, rather than exact icon bytes, identify it.
    """
    pages = [
        lr.page((FIXTURES / f"loot-craft-gifts-{suffix}.png").read_bytes(), vision)
        for suffix in ("before", "after")
    ]
    for p in pages:
        assert p.kind == "reward" and not p.clipped
        assert [(c.name, c.quantity) for c in p.cards] == [
            ("Brain Teaser Puzzle Cube", 1), ("Wind-Up Music Box", 1)
        ]
    assert not lr.same_icon(pages[0].cards[0].icon, pages[1].cards[0].icon)
    assert lr.ReceiptReader.same(*pages)


@pytest.mark.parametrize("fixture", [
    "loot-assault-points", "loot-assault-points-merged-heading",
    "loot-assault-points-tooltip-return",
])
def test_assault_points_heading_sparkle_keeps_exact_loot_identity(vision, fixture):
    before, after = [
        (FIXTURES / f"{fixture}-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    p = lr.page(before, vision)
    assert [(c.name, c.quantity) for c in p.cards] == [
        ("Credit Points", 3000000), ("Pyroxenes", 200),
        ("Normal Enhancement Stone", 32), ("Advanced Enhancement Stone", 32),
        ("Superior Enhancement Stone", 4), ("Advanced Activity Report", 12),
    ]
    assert p.clipped and p.trailing_clipped and not p.leading_clipped
    assert not lr.same_receipt_view(before, after, p)
    assert lr.same_receipt_view(before, after, p, vision=vision)
    changed = decode_frame(after)
    changed[433:459, 295:430] = 255  # The Pyroxene quantity disappears.
    assert not lr.same_receipt_view(before, lr.encode(changed), p, vision=vision)
    changed = decode_frame(after)
    changed[130:183, 375:640] = 0  # A partial heading cannot be repaired by OCR.
    assert not lr.same_receipt_view(before, lr.encode(changed), p, vision=vision)
    for words in (
        [Word("REWARD ACQUIRED!", .94, (364, 126, 914, 191))],
        [Word("REWARD EXPECTED!", 1, (364, 126, 914, 191))],
        [Word("REWARD ACQUIRED!", 1, (364, 525, 914, 590))],
    ):
        uncertain = SimpleNamespace(read=lambda image: words)
        assert not lr.same_receipt_view(before, after, p, vision=uncertain)
    moved = iter([
        [Word("REWARD ACQUIRED!", 1, (364, 126, 914, 191))],
        [Word("REWARD ACQUIRED!", 1, (372, 126, 922, 191))],
    ])
    assert not lr.same_receipt_view(
        before, after, p, vision=SimpleNamespace(read=lambda image: next(moved))
    )


@pytest.mark.parametrize("kind,height", [("sweep", 64), ("lesson", 64), ("grid", 90)])
def test_compact_icon_keeps_artwork_beneath_quantity_overlay(kind, height):
    # Lower artwork is real icon content even though the game overlays xN there.
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[250 + height - 12:250 + height, 100:180] = (31, 110, 223)
    png = lr.card_icon(image, (100, 250, 80, height), kind)
    icon = lr.cv2.imdecode(np.frombuffer(png, np.uint8), lr.cv2.IMREAD_COLOR)
    assert icon.shape == (height, 80, 3)
    assert np.all(icon[-12:] == (31, 110, 223))


def test_scrolled_reward_overlap_accepts_only_one_pixel_border_rasterization(vision):
    before, after = [
        lr.page((FIXTURES / f"loot-assault-points-overlap-{suffix}.png").read_bytes(), vision)
        for suffix in ("before", "after")
    ]
    assert before.kind == after.kind == "reward"
    assert [(c.name, c.quantity) for c in before.cards[-4:]] == [
        ("Normal Enhancement Stone", 32), ("Advanced Enhancement Stone", 32),
        ("Superior Enhancement Stone", 4), ("Advanced Activity Report", 12),
    ]
    # The first Stone is now partly inside the viewport's fade band. Only the
    # three completely exposed cards establish ordered overlap.
    assert after.leading_clipped
    assert [(c.name, c.quantity) for c in after.cards[:3]] == [
        (c.name, c.quantity) for c in before.cards[-3:]
    ]
    old, new = before.cards[-1], after.cards[2]
    assert old.box[2:] == (147, 222) and new.box[2:] == (148, 222)
    assert all(lr.same_card(a, b) for a, b in zip(before.cards[-3:], after.cards[:3]))
    assert not lr.same_card(old, replace(new, name="Superior Activity Report"))
    assert not lr.same_card(old, replace(new, quantity=13))
    assert not lr.same_card(old, replace(new, box=(*new.box[:2], 149, 222)))


def test_label_raster_noise_requires_exact_fresh_name_and_amount_ocr(vision):
    before, after = [
        (FIXTURES / f"loot-assault-points-name-raster-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    p = lr.page(before, vision)
    assert p.cards[3].name == "Advanced Enhancement Stone"
    assert p.cards[3].quantity == 32
    assert lr.reward_card_boxes(decode_frame(before)) == lr.reward_card_boxes(decode_frame(after))
    assert not lr.same_receipt_view(before, after, p)
    assert lr.same_receipt_view(before, after, p, vision=vision)
    # The same guard rejects a real name change even with fresh OCR available.
    changed = decode_frame(after)
    changed[260:307, 618:756] = changed[260:307, 456:594].copy()
    assert not lr.same_receipt_view(before, lr.encode(changed), p, vision=vision)
    changed = decode_frame(after)
    changed[428:467, 621:753] = 255
    assert not lr.same_receipt_view(before, lr.encode(changed), p, vision=vision)


def test_heading_ocr_whitespace_does_not_change_exact_receipt_identity(vision):
    before, after = [
        (FIXTURES / f"loot-assault-points-heading-space-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    parsed = lr.page(before, vision)
    assert [(card.name, card.quantity) for card in parsed.cards] == [
        ("Superior Activity Report", 4), ("Eligma", 40),
        ("Broken Quimbaya Relic", 6), ("Repaired Quimbaya Relic", 4),
        ("Broken Istanbul Rocket", 6), ("Repaired Istanbul Rocket", 4),
    ]
    assert not lr.same_receipt_view(before, after, parsed)
    assert lr.same_receipt_view(before, after, parsed, vision=vision)
    for text, confidence, box in (
        ("REWARDACQUIRED!", .94, (364, 126, 914, 191)),
        ("REWARDREQUIRED!", .99, (364, 126, 914, 191)),
        ("REWARDACQUIRED!", .99, (372, 126, 922, 191)),
    ):
        headings = iter([
            [Word("REWARDACQUIRED!", .99, (364, 126, 914, 191))],
            [Word(text, confidence, box)],
        ])
        assert not lr.same_receipt_view(
            before, after, parsed,
            vision=SimpleNamespace(read=lambda image: next(headings)),
        )


def test_delayed_receipt_scroll_invalidates_targets_but_retains_ordered_overlap(vision):
    before, after = [
        (FIXTURES / f"loot-assault-points-resumed-scroll-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    p, q = (lr.page(png, vision) for png in (before, after))
    assert p.kind == q.kind == "reward"
    assert p.cards[3].name == q.cards[2].name == "Broken Quimbaya Relic"
    assert not lr.same_receipt_view(before, after, p, vision=vision)
    assert not lr.reward_layout_stable(before, after)
    assert len(p.cards) == 6 and len(q.cards) == 5
    assert all(lr.same_card(a, b) for a, b in zip(p.cards[1:], q.cards))
    assert abs(p.cards[-1].box[0] - q.cards[-1].box[0]) == 107


def test_faded_edge_fragment_is_not_a_full_card_or_an_inspection_target(vision):
    frames = [
        (FIXTURES / f"loot-assault-points-clipped-edge-{suffix}.png").read_bytes()
        for suffix in ("before", "after")
    ]
    before, after = [lr.page(png, vision) for png in frames]
    expected = [
        ("Advanced Enhancement Stone", 32), ("Superior Enhancement Stone", 4),
        ("Advanced Activity Report", 12), ("Superior Activity Report", 4),
        ("Eligma", 40),
    ]
    for parsed in (before, after):
        assert parsed.kind == "reward" and parsed.leading_clipped and parsed.trailing_clipped
        assert [(c.name, c.quantity) for c in parsed.cards] == expected
        assert all(c.box[0] + c.box[2] < 1176 for c in parsed.cards)
    # The faded last 8–9 pixels of Broken Quimbaya are offscreen. Never guess
    # its final letters or accept it until the next ordered page exposes it.
    assert lr.reward_card_boxes(decode_frame(frames[0])) == lr.reward_card_boxes(decode_frame(frames[1]))
    assert lr.same_receipt_view(*frames, before, vision=vision)
    following = lr.page((FIXTURES / "loot-assault-points-resumed-scroll-before.png").read_bytes(), vision)
    assert any(c.name == "Broken Quimbaya Relic" for c in following.cards)
    assert all(lr.same_card(a, b) for a, b in zip(before.cards[-3:], following.cards[:3]))


def test_lesson_inventory_row_excludes_students(vision):
    p = lr.page((FIXTURES / "lesson-receipt.png").read_bytes(), vision)
    assert p.kind == "lesson" and [c.quantity for c in p.cards] == [100, 1]
    assert all(c.box[1] > 420 for c in p.cards)


def test_unrelated_screen_cannot_authorize_loot_inputs(vision):
    assert (
        lr.page((FIXTURES / "home_controls.png").read_bytes(), vision).kind == "unknown"
    )


def test_overlapping_pages_are_inspected_once_and_receipt_survives_clear(
    tmp_path, monkeypatch
):
    clock = [0.0]
    inputs = []
    position = [0]
    tip = [None]
    c = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        state_dir=tmp_path / "state",
        run_dir=tmp_path / "runs",
    )
    directory = c.run_dir / "test"
    j = Journal(directory, lambda: clock[0], 0)
    colors = [lr.encode(np.full((10, 10, 3), n, np.uint8)) for n in (50, 100, 150)]
    cards = [
        lr.Card((100 + 200 * i, 250, 148, 222), i + 1, chr(65 + i), colors[i])
        for i in range(3)
    ]
    pages = [lr.Page("reward", tuple(cards[:2])), lr.Page("reward", tuple(cards[1:]))]

    def current(png, vision):
        return lr.Page("tooltip") if tip[0] else pages[position[0]]

    def tap(x, y, **kw):
        inputs.append(("tap", x, y))
        if tip[0]:
            tip[0] = None
        else:
            tip[0] = next(
                card.name for card in pages[position[0]].cards if card.target == (x, y)
            )
        return True

    def swipe(start, end, **kw):
        inputs.append(("swipe", start, end))
        position[0] = 0 if end[0] > start[0] else 1
        return True

    device = SimpleNamespace(
        foreground_package=lambda: c.package,
        screenshot=lambda: b"image",
        tap=tap,
        swipe=swipe,
    )

    def fail(message):
        raise TaskError(message, directory)

    r = SimpleNamespace(
        config=c,
        device=device,
        clock=lambda: clock[0],
        sleep=lambda n: clock.__setitem__(0, clock[0] + n),
        actions=0,
        journal=j,
        fail=fail,
        task="tasks",
    )
    monkeypatch.setattr(lr, "page", current)
    monkeypatch.setattr(lr, "decode_frame", lambda png: png)
    monkeypatch.setattr(lr, "has_tooltip", lambda image: bool(tip[0]))
    monkeypatch.setattr(lr, "read_tooltip", lambda image, vision: tip[0])
    monkeypatch.setattr(lr, "reward_layout_stable", lambda before, after: before == after)
    evidence = directory / "receipt.png"
    evidence.write_bytes(b"image")
    try:
        result = lr.ReceiptReader(
            r, SimpleNamespace(read=lambda im: []), evidence
        ).run()
        assert result["items_complete"] is True
        assert [(i["name"], i["quantity"]) for i in result["items"]] == [
            ("A", 1),
            ("B", 2),
            ("C", 3),
        ]
        assert (
            sum(op[0] == "tap" for op in inputs) == 6
        )  # Three open/dismiss pairs, overlap skipped.
        assert json.loads(evidence.with_suffix(".loot.json").read_text()) == result
    finally:
        j.close()


@pytest.mark.parametrize("file", ["loot-sweep-more.png", "ap-hard-receipt.png"])
def test_hidden_final_rewards_require_full_list(vision, file):
    p = lr.page((FIXTURES / file).read_bytes(), vision)
    assert p.kind == "sweep" and p.expand_target and p.clipped
    assert p.cards == ()  # Never call only the visible first nine cards complete.


def test_full_list_reads_all_twelve_cards_in_row_order(vision):
    p = lr.page((FIXTURES / "loot-full-list.png").read_bytes(), vision)
    assert p.kind == "grid" and not p.clipped and len(p.cards) == 12
    assert [c.quantity for c in p.cards] == [1, 1, 1, 2, 1, 1, 7, 8, 10, 3, 1, 723]


def test_uncertain_wrapped_suffix_cannot_be_silently_dropped(vision):
    from dataclasses import replace

    im = decode_frame((FIXTURES / "loot-tooltip-1.png").read_bytes())
    words = [
        replace(w, confidence=0.70) if w.text == "Blueprint" else w
        for w in vision.read(im)
    ]
    assert lr.tooltip(im, words) is None


def test_visual_overlap_accepts_only_tiny_raster_noise():
    original = np.full((60, 80, 3), 200, np.uint8)
    shimmer = original.copy()
    shimmer[2, 3] = 190
    different = original.copy()
    different[20:30, 25:40] = 70
    assert lr.same_icon(lr.encode(original), lr.encode(shimmer))
    assert not lr.same_icon(lr.encode(original), lr.encode(different))
    assert not lr.same_icon(lr.encode(original), b"not an icon")
