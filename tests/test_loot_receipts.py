"""Real receipt layouts plus an offline state machine with overlapping pages."""

from pathlib import Path
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
