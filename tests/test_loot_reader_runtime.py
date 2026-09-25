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
        page_reads=0,
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
        h.page_reads += 1
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
    monkeypatch.setattr(lr, "reward_layout_stable", lambda before, after: before == after)
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
    with pytest.raises(TaskError, match="card changed|receipt changed"):
        h.reader().run()
    assert h.inputs == []
    assert h.result()["items"] == []
    assert h.result()["items_complete"] is False
    for item in (original, replacement):
        assert lr.known_name(h.runner.config, sha256(item.icon).hexdigest()) is None


def test_named_border_change_cannot_reuse_an_old_input_rectangle(harness):
    h = harness
    original = card("AA", 100)
    replacement = replace(original, box=(100, 250, 149, 222))
    h.pages = [lr.Page("sweep", (original,)), lr.Page("sweep", (replacement,))]

    def change_after_capture(number):
        if number >= 2:
            h.position = 1

    h.on_capture = change_after_capture
    with pytest.raises(TaskError, match="card changed|receipt changed"):
        h.reader().run()
    assert h.inputs == []
    assert h.result()["items"] == []


def test_one_pixel_border_in_overlap_never_duplicates_rewards(harness):
    h = harness
    a, b, c = card("AA", 100), card("BB", 300), card("CC", 300)
    shifted = replace(b, box=(100, 250, 149, 222))
    h.pages = [lr.Page("reward", (a, b)), lr.Page("reward", (shifted, c))]
    result = h.reader().run()
    assert result["items_complete"]
    assert [item["name"] for item in result["items"]] == ["AA", "BB", "CC"]
    assert len([action for action in h.inputs if action[0] == "tap"]) == 6


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


def test_parsed_page_reuse_still_revalidates_every_new_item(harness):
    h = harness
    result = h.reader().run()
    assert result["items_complete"]
    # Initial discovery, then one parse after each tooltip dismissal. There is
    # no redundant full-page OCR immediately before opening the next tooltip.
    assert h.page_reads == 3
    records = [json.loads(line) for line in
               (h.evidence.parent / "events.jsonl").read_text().splitlines()]
    checked = [entry for entry in records if entry["event"] == "receipt_revalidated"]
    assert len(checked) == 2
    assert len(h.inputs) == 4


def test_reward_end_left_partial_was_counted_on_previous_overlapping_page(harness):
    h = harness
    a, b, c = card("AA", 100), card("BB", 300), card("CC", 500)
    h.pages = [
        lr.Page("reward", (a, b), clipped=True, trailing_clipped=True),
        lr.Page("reward", (replace(b, box=a.box), c), clipped=True, leading_clipped=True),
    ]
    result = h.reader().run()
    assert result["items_complete"]
    assert [item["name"] for item in result["items"]] == ["AA", "BB", "CC"]


def test_reward_end_right_partial_remains_incomplete(harness):
    h = harness
    h.pages = [lr.Page("reward", (card("AA", 100),), clipped=True, trailing_clipped=True)]
    assert not h.reader().run()["items_complete"]


def test_reward_start_left_partial_cannot_be_skipped(harness):
    h = harness
    h.pages = [lr.Page("reward", (card("AA", 100),), clipped=True, leading_clipped=True)]
    with pytest.raises(TaskError, match="beginning still contains a partial card"):
        h.reader().run()
    assert not any(action[0] == "tap" for action in h.inputs)


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


@pytest.mark.parametrize("arrival", ["during_poll", "during_ocr"])
def test_delayed_item_tooltip_is_inspected_without_a_second_item_input(
    harness, monkeypatch, arrival
):
    h = harness
    h.pages = [lr.Page("sweep", (card("Credit Points", 100, 3000000),))]
    original_tap, original_page = h.runner.device.tap, lr.page
    pending = []

    def tap(*args, **kwargs):
        opening = h.tip is None
        result = original_tap(*args, **kwargs)
        if opening:
            pending.append(h.tip)
            h.tip = None
        return result

    def on_capture(number):
        if arrival == "during_poll" and pending and number >= 5:
            h.tip = pending.pop()

    def page(png, vision):
        result = original_page(png, vision)
        if arrival == "during_ocr" and pending:
            h.now += 6
            h.tip = pending.pop()
        return result

    h.runner.device.tap = tap
    h.on_capture = on_capture
    monkeypatch.setattr(lr, "page", page)
    result = h.reader().run()
    assert result["items_complete"]
    assert [(item["name"], item["quantity"]) for item in result["items"]] == [
        ("Credit Points", 3000000)
    ]
    assert h.inputs == [("tap", (174, 361)), ("tap", (270, 550))]
    assert not pending and h.tip is None


def test_missing_tooltip_observation_and_inputs_remain_bounded(harness):
    h = harness
    reader = h.reader()
    cap = reader.capture()
    reader.read(cap)

    def tap(x, y, **kwargs):
        h.inputs.append(("tap", (x, y)))
        return True

    h.runner.device.tap = tap
    _, name = reader.inspect_card(cap, h.pages[0].cards[0], "sweep")
    assert name == "A"
    assert len(h.inputs) == 3  # Tap, long press, tap; no blind dismissal.
    assert h.captures <= 45
    assert h.now < 15


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
    assert refreshed and any(entry["previous_age"] >= 6 for entry in refreshed)
    assert all(entry["fresh_age"] <= 5 for entry in refreshed)


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


def test_heading_validation_cannot_extend_capture_deadline(harness, monkeypatch):
    h = harness
    reader = h.reader()
    cap = reader.capture()
    reader.read(cap)
    h.now += 6

    def slow_heading_check(*args, **kwargs):
        h.now += 6
        return True

    monkeypatch.setattr(lr, "same_receipt_view", slow_heading_check)
    with pytest.raises(TaskError, match="input expired or foreground changed"):
        reader.input(cap, h.pages[0].cards[0].target)
    assert h.inputs == []


def test_failed_revalidation_preserves_both_observed_frames(harness, monkeypatch):
    h = harness
    reader = h.reader()
    cap = reader.capture()
    reader.read(cap)
    h.now += 6
    h.unknown = True
    monkeypatch.setattr(lr, "same_receipt_view", lambda *args, **kwargs: False)
    with pytest.raises(TaskError, match="Reward receipt changed"):
        reader.input(cap, h.pages[0].cards[0].target)
    assert (h.evidence.parent / "receipt-refresh-before.png").read_bytes() == cap.png
    fresh = (h.evidence.parent / "receipt-refresh-after.png").read_bytes()
    assert json.loads(fresh)["unknown"] is True
    assert h.inputs == []


def test_reward_revalidation_retries_without_input_using_original_evidence(harness, monkeypatch):
    h = harness
    h.pages = [replace(h.pages[0], kind="reward")]
    reader = h.reader()
    cap = reader.capture()
    reader.read(cap)
    h.now += 6
    checks = []

    def verify(before, after, expected, **kwargs):
        checks.append((before, expected))
        assert h.inputs == []
        return len(checks) == 3

    monkeypatch.setattr(lr, "same_receipt_view", verify)
    reader.input(cap, h.pages[0].cards[0].target)
    assert checks == [(cap.png, h.pages[0])] * 3
    assert len(h.inputs) == 1
    assert (h.evidence.parent / "receipt-refresh-rejected-1.png").is_file()
    assert (h.evidence.parent / "receipt-refresh-rejected-2.png").is_file()


def test_reward_revalidation_retries_remain_bounded_and_never_accept_mismatch(harness, monkeypatch):
    h = harness
    h.pages = [replace(h.pages[0], kind="reward")]
    reader = h.reader()
    cap = reader.capture()
    reader.read(cap)
    h.now += 6
    checks = []

    def verify(*args, **kwargs):
        checks.append(h.now)
        return False

    monkeypatch.setattr(lr, "same_receipt_view", verify)
    with pytest.raises(TaskError, match="Reward receipt changed"):
        reader.input(cap, h.pages[0].cards[0].target)
    assert len(checks) == 3
    assert h.inputs == []


def test_reward_scroll_waits_for_two_stationary_samples_before_ocr(harness, monkeypatch):
    h = harness
    h.pages = [replace(h.pages[0], kind="reward")]
    reader = h.reader()
    states = iter([False, True, False, True, True])
    checks = []

    def stable(*args):
        checks.append(h.now)
        return next(states)

    monkeypatch.setattr(lr, "reward_layout_stable", stable)
    cap, page = reader.pan(reader.capture(), "reward", True)
    assert len(checks) == 5
    assert len(h.inputs) == 1
    assert reader.observed == (cap.png, page)


def test_reward_scroll_that_never_settles_stops_without_more_inputs(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(lr, "reward_layout_stable", lambda *args: False)
    reader = h.reader()
    with pytest.raises(TaskError, match="did not settle after scrolling"):
        reader.pan(reader.capture(), "reward", True)
    assert len(h.inputs) == 1
    assert reader.observed is None
    assert (h.evidence.parent / "receipt-scroll-unsettled.png").is_file()


def test_scroll_resuming_during_ocr_reobserves_before_using_new_target(harness, monkeypatch):
    h = harness
    h.pages = [lr.Page("reward", (card("AA", x),)) for x in (100, 207)]
    h.page_delay = 6
    original_page = lr.page

    def page(png, vision):
        parsed = original_page(png, vision)
        h.position = 1  # Delayed scrolling starts while the first OCR is running.
        return parsed

    monkeypatch.setattr(lr, "page", page)
    monkeypatch.setattr(lr, "same_receipt_view", lambda a, b, *args, **kwargs: a == b)
    reader = h.reader()
    cap, parsed = reader.pan(reader.capture(), "reward", True)
    assert h.page_reads == 2
    assert len(h.inputs) == 1  # Re-observation sends no additional swipe or tap.
    assert parsed.cards[0].box[0] == 207
    assert reader.observed == (cap.png, parsed)
    assert cap.is_fresh(h.runner.clock())
    reader.input(cap, parsed.cards[0].target)
    assert h.inputs[-1] == ("tap", h.pages[1].cards[0].target)
    assert (h.evidence.parent / "receipt-scroll-read-1.png").is_file()
    assert (h.evidence.parent / "receipt-scroll-refresh-1.png").is_file()


@pytest.mark.parametrize("failure", ["moving", "expired"])
def test_post_ocr_scroll_verification_is_bounded_and_keeps_freshness(harness, monkeypatch, failure):
    h = harness
    h.pages = [replace(h.pages[0], kind="reward")]
    checks = []

    def check(*args, **kwargs):
        checks.append(True)
        if failure == "expired":
            h.now += 6
            return True
        return False

    monkeypatch.setattr(lr, "same_receipt_view", check)
    reader = h.reader()
    with pytest.raises(TaskError, match="did not remain stable through OCR"):
        reader.pan(reader.capture(), "reward", True)
    assert len(checks) == 4
    assert len(h.inputs) == 1


@pytest.mark.parametrize("limit", ["time", "reward_time", "inputs"])
def test_extended_receipt_budget_remains_bounded(harness, limit):
    h = harness
    reader = h.reader()
    assert reader.timeout == lr.RECEIPT_TIMEOUT == 240
    if limit == "reward_time":
        h.pages = [replace(h.pages[0], kind="reward")]
        reader.read(reader.capture())
        assert reader.timeout == lr.REWARD_RECEIPT_TIMEOUT == 900
    if limit in {"time", "reward_time"}:
        h.now = reader.timeout + .001
    else:
        reader.inputs = 160
    with pytest.raises(TaskError, match="bounded limit"):
        reader.capture()
    assert h.inputs == [] and h.captures == (1 if limit == "reward_time" else 0)
