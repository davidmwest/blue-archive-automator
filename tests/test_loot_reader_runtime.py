"""Receipt-reader input guards and durable partial results without a device."""

from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from ba_automator import loot_receipts as lr
from ba_automator.config import Config
from ba_automator.runtime import Capture, Journal, TaskError
from ba_automator.shop_runtime import ShopFrame


def card(name, x, quantity=1, icon=None):
    return lr.Card((x, 250, 148, 222), quantity, name, icon or name.encode())


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Snapshots keep their page/tooltip identity even after a later device input."""
    config = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        state_dir=tmp_path / "state",
        run_dir=tmp_path / "runs",
    )
    h = SimpleNamespace(
        now=0.0,
        inputs=[],
        captures=0,
        foreground=config.package,
        position=0,
        tip=None,
        unknown=False,
        capture_delay=0.0,
        page_delay=0.0,
        on_capture=None,
        broken_item=None,
        pages=[lr.Page("sweep", (card("A", 100), card("B", 300, 2)))],
    )
    directory = config.run_dir / "reader"
    journal = Journal(directory, lambda: h.now, h.now)
    evidence = directory / "receipt.png"
    evidence.write_bytes(b"original receipt")

    def fail(message):
        raise TaskError(message, directory)

    def sleep(seconds):
        h.now += seconds

    def screenshot():
        h.captures += 1
        h.now += h.capture_delay
        if h.on_capture:
            h.on_capture(h.captures)
        return json.dumps(
            {"page": h.position, "tip": h.tip, "unknown": h.unknown}
        ).encode()

    def tap(x, y, *, deadline, monotonic):
        assert monotonic() <= deadline
        h.inputs.append(("tap", (x, y)))
        if h.tip:
            h.tip = None
        else:
            selected = next(c for c in h.pages[h.position].cards if c.target == (x, y))
            if selected.name == h.broken_item:
                h.unknown = True
            else:
                h.tip = selected.name
        return True

    def swipe(start, end, *, duration_ms, deadline, monotonic):
        assert monotonic() <= deadline
        h.inputs.append(("swipe", start, end))
        if end[0] > start[0]:
            h.position = max(0, h.position - 1)
        elif end[0] < start[0]:
            h.position = min(len(h.pages) - 1, h.position + 1)
        return True

    def page(png, vision):
        h.now += h.page_delay
        snapshot = json.loads(png)
        if snapshot["unknown"]:
            return lr.Page("unknown")
        if snapshot["tip"]:
            return lr.Page("tooltip")
        return h.pages[snapshot["page"]]

    device = SimpleNamespace(
        foreground_package=lambda: h.foreground,
        screenshot=screenshot,
        tap=tap,
        swipe=swipe,
    )
    h.vision = SimpleNamespace(read=lambda image: [])
    h.runner = SimpleNamespace(
        config=config,
        device=device,
        clock=lambda: h.now,
        sleep=sleep,
        actions=0,
        journal=journal,
        fail=fail,
        task="tasks",
        vision=SimpleNamespace(startup=h.vision),
    )
    h.evidence = evidence
    h.reader = lambda: lr.ReceiptReader(h.runner, h.vision, evidence)
    h.result = lambda: json.loads(evidence.with_suffix(".loot.json").read_text())
    h.events = lambda: [
        json.loads(line)
        for line in (config.state_dir / "important-actions.jsonl")
        .read_text()
        .splitlines()
    ]
    monkeypatch.setattr(lr, "page", page)
    monkeypatch.setattr(lr, "decode_frame", json.loads)
    monkeypatch.setattr(lr, "has_tooltip", lambda image: bool(image["tip"]))
    monkeypatch.setattr(lr, "read_tooltip", lambda image, vision: image["tip"])
    yield h
    journal.close()


@pytest.mark.parametrize("failure", ["slow_capture", "foreign_before", "foreign_after"])
def test_stale_capture_and_foreign_foreground_send_no_input(harness, failure):
    h = harness
    if failure == "slow_capture":
        h.capture_delay = 6
    elif failure == "foreign_before":
        h.foreground = "com.android.settings"
    else:

        def change_after_capture(number):
            if number == 2:
                h.foreground = "com.android.settings"

        h.on_capture = change_after_capture
    with pytest.raises(TaskError):
        h.reader().run()
    assert h.inputs == []
    assert h.result()["items_complete"] is False
    if failure == "foreign_before":
        assert h.result()["items"] == []
    assert h.evidence.read_bytes() == b"original receipt"
    assert len(h.events()) == 1


def test_same_box_and_quantity_with_changed_icon_cannot_tap_or_learn(harness):
    h = harness
    original = card("A", 100)
    replacement = replace(original, name="Other item", icon=b"different icon")
    h.pages = [lr.Page("sweep", (original,)), lr.Page("sweep", (replacement,))]

    def change_after_capture(number):
        if number >= 2:
            h.position = 1

    h.on_capture = change_after_capture
    with pytest.raises(TaskError, match="card changed"):
        h.reader().run()
    assert h.inputs == []
    assert h.result()["items"] == []
    assert h.result()["items_complete"] is False
    for item in (original, replacement):
        assert lr.known_name(h.runner.config, sha256(item.icon).hexdigest()) is None


def test_distinct_duplicate_cards_count_twice_and_page_overlap_is_reused(harness):
    h = harness
    a, b1, b2, c = (
        card("A", 100),
        card("B", 300, 2),
        card("B", 500, 2),
        card("C", 500, 3),
    )
    h.pages = [
        lr.Page("reward", (a, b1, b2)),
        lr.Page("reward", (replace(b1, box=a.box), replace(b2, box=b1.box), c)),
    ]
    result = h.reader().run()
    assert result["items_complete"] is True
    assert [(i["name"], i["quantity"]) for i in result["items"]] == [
        ("A", 1),
        ("B", 2),
        ("B", 2),
        ("C", 3),
    ]
    totals = Counter()
    for item in result["items"]:
        totals[item["name"]] += item["quantity"]
    assert totals == {"A": 1, "B": 4, "C": 3}
    assert sum(entry[0] == "tap" for entry in h.inputs) == 8
    assert h.result() == result
    assert len(h.events()) == 1


def test_later_tooltip_failure_preserves_received_items_and_evidence(harness):
    h = harness
    # The second item's quantity/icon are visible, but its name requires a
    # tooltip. Preserve that unresolved card alongside the first verified one.
    h.pages = [
        lr.Page("sweep", (card("A", 100), replace(card("B", 300, 2), name=None)))
    ]
    h.broken_item = None
    with pytest.raises(TaskError, match="Unrecognized item detail"):
        h.reader().run()
    result = h.result()
    assert result["items_complete"] is False
    assert [(i["name"], i["quantity"]) for i in result["items"]] == [
        ("A", 1),
        (None, 2),
    ]
    assert "Unrecognized item detail" in result["note"]
    assert h.evidence.read_bytes() == b"original receipt"
    assert (h.evidence.parent / "receipt-page-00.png").is_file()
    assert (h.evidence.parent / "receipt-item-000.png").is_file()
    assert (h.evidence.parent / "receipt-item-001.png").is_file()
    assert len(h.inputs) == 3  # Open A, close A, open B; no blind dismissal.
    (event,) = h.events()
    assert event["action"] == "loot_received"
    assert event["items"] == result["items"]
    assert lr.known_name(h.runner.config, sha256(b"A").hexdigest()) == "A"
    assert lr.known_name(h.runner.config, sha256(b"B").hexdigest()) is None


def test_inspect_receipt_returns_fresh_caller_frame_after_real_reader(harness):
    h = harness
    original = ShopFrame(
        Capture(b"old receipt", -20, h.runner.config.package),
        SimpleNamespace(kind="receipt", target=(640, 630)),
    )
    waits = []

    def wait(kind):
        waits.append(kind)
        assert h.tip is None and not h.unknown
        return ShopFrame(
            Capture(h.runner.device.screenshot(), h.now, h.runner.config.package),
            SimpleNamespace(kind="receipt", target=(640, 630)),
        )

    h.runner.wait = wait
    result = lr.inspect_receipt(h.runner, original, h.evidence)
    assert waits == ["receipt"]
    assert result is not original
    assert result.capture.is_fresh(h.runner.clock())
    assert not original.capture.is_fresh(h.runner.clock())
    assert result.screen.target == (640, 630)
    assert h.result()["items_complete"] is True


def test_bare_receipt_cannot_authorize_tooltip_dismissal(harness):
    h = harness
    reader = h.reader()
    with pytest.raises(TaskError, match="No item tooltip"):
        reader.dismiss_tooltip(reader.capture(), "sweep")
    assert h.inputs == []


@pytest.mark.parametrize("kind", ["reward", "sweep"])
def test_slow_ocr_refreshes_unchanged_receipt_before_each_input(harness, monkeypatch, kind):
    h = harness
    h.pages = [replace(h.pages[0], kind=kind)]
    h.page_delay = 6.0

    def slow_tooltip(image, vision):
        h.now += 6.0
        return image["tip"]

    monkeypatch.setattr(lr, "read_tooltip", slow_tooltip)
    result = h.reader().run()
    assert result["items_complete"] is True
    assert [(i["name"], i["quantity"]) for i in result["items"]] == [("A", 1), ("B", 2)]
    assert sum(action[0] == "tap" for action in h.inputs) == 4
    records = [
        json.loads(line)
        for line in (h.evidence.parent / "events.jsonl").read_text().splitlines()
    ]
    refreshed = [entry for entry in records if entry["event"] == "receipt_revalidated"]
    assert refreshed and all(entry["previous_age"] >= 6 for entry in refreshed)


@pytest.mark.parametrize("change", ["receipt", "foreground"])
def test_slow_ocr_refresh_cannot_authorize_a_changed_screen(harness, change):
    h = harness
    reader = h.reader()
    cap = reader.capture()
    reader.read(cap)
    h.now += 6.0
    if change == "receipt":
        h.unknown = True
    else:
        h.foreground = "com.android.settings"
    with pytest.raises(TaskError):
        reader.input(cap, h.pages[0].cards[0].target)
    assert h.inputs == []


def test_refresh_does_not_extend_deadline_during_device_preflight(harness):
    h = harness
    reader = h.reader()
    cap = reader.capture()
    reader.read(cap)
    h.now += 6.0

    def expired_tap(x, y, *, deadline, monotonic):
        assert deadline == 11.0  # New capture began at 6; the original cap is still 0.
        h.now += 6.0
        return monotonic() <= deadline

    h.runner.device.tap = expired_tap
    with pytest.raises(TaskError, match="expired during device preflight"):
        reader.input(cap, h.pages[0].cards[0].target)
    assert cap.captured_at == 0 and cap.deadline == 5
    assert h.inputs == []
