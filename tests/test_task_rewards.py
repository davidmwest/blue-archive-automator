"""Tasks badge gating, receipt scrolling, claim verification, and queue integration."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest

from ba_automator import loot
from ba_automator.config import Config
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame
from ba_automator.task_rewards import MAX_CLAIMS, TaskRewardsRunner
from ba_automator.task_rewards_vision import (
    ALL_TAB,
    HOME_BUTTON,
    HOME_TASKS,
    TaskRewardsScreen as Screen,
    TaskRewardsVision,
    classify_task_rewards,
)
from ba_automator.tasks import task_plan
from ba_automator.vision import StartupVision, Word, decode_frame

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def vision():
    return TaskRewardsVision(StartupVision())


@pytest.mark.parametrize(
    "name,kind,claim",
    [
        ("home-dot", "home", None),
        ("home-clear", "home", None),
        ("claim-all", "tasks", "all"),
        ("daily-bonus", "tasks", "daily completion"),
        ("empty", "tasks_empty", None),
        ("receipt-start", "receipt", None),
        ("receipt-end", "receipt", None),
        ("receipt-small", "receipt", None),
        ("receipt-bonus", "receipt", None),
    ],
)
def test_sanitized_live_screens(vision, name, kind, claim):
    screen = vision.analyze((FIXTURES / f"task-rewards-{name}.png").read_bytes())
    assert (screen.kind, screen.claim) == (kind, claim)
    if kind == "home":
        assert screen.red_dot == (name == "home-dot")
    if name == "receipt-start":
        assert {"name": "Credit Points", "quantity": 20000} in screen.items
        assert {"name": "Pyroxenes", "quantity": 20} in screen.items
        assert {"name": "AP", "quantity": 300} in screen.items
    if name == "receipt-end":
        assert {"name": "Normal Activity Report", "quantity": 3} in screen.items
        assert not any(i["name"] == "ermit" for i in screen.items)
    if name == "receipt-bonus":
        assert screen.items == ({"name": "Pyroxenes", "quantity": 20},)


def test_other_red_badges_and_dimmed_home_cannot_authorize_entry(vision):
    png = (FIXTURES / "task-rewards-home-dot.png").read_bytes()
    frame = decode_frame(png)
    words = vision.startup.read(frame)
    moved = frame.copy()
    moved[246:263, 180:196] = moved[246:263, 66:82]
    moved[246:263, 66:82] = 0
    assert not classify_task_rewards(moved, words, home=True).red_dot
    assert classify_task_rewards(frame, words, home=False).kind == "unknown"
    assert not classify_task_rewards(
        (frame * 0.5).astype("uint8"), words, home=True
    ).red_dot
    assert (
        classify_task_rewards(
            frame, [w for w in words if w.normalized != "tasks"], home=True
        ).kind
        == "unknown"
    )


def test_native_ignored_entry_preserves_home_and_targets_tasks_label(vision):
    png = (FIXTURES / "task-rewards-home-ignored-native.png").read_bytes()
    screen = vision.analyze(png)
    assert screen.kind == "home" and screen.red_dot
    # The native failure screen still has the exact label required by vision.
    # Its center is below the decorative illustration that ignored early taps.
    assert HOME_TASKS == (50, 261)
    frame = decode_frame(png)
    assert classify_task_rewards(frame, [], home=True).kind == "unknown"
    assert classify_task_rewards(frame, [], home=False).kind == "unknown"


def test_task_page_needs_all_anchors_and_unshaded_controls(vision):
    frame = decode_frame((FIXTURES / "task-rewards-claim-all.png").read_bytes())
    words = vision.startup.read(frame)
    for name in ("tasks", "all", "daily", "weekly", "achievement", "challenges"):
        assert (
            classify_task_rewards(
                frame, [w for w in words if w.normalized != name]
            ).kind
            == "unknown"
        )
    assert classify_task_rewards((frame * 0.5).astype("uint8"), words).kind == "unknown"
    assert classify_task_rewards(frame[:500], words).kind == "unknown"
    # Visible Claim text alone must not turn a disabled control into a target.
    gray = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    gray[95:125, 550:600] = frame[95:125, 550:600]
    assert classify_task_rewards(gray, words).target is None


def test_receipt_requires_its_own_heading_and_continue_control(vision):
    frame = decode_frame((FIXTURES / "task-rewards-receipt-start.png").read_bytes())
    words = vision.startup.read(frame)
    assert classify_task_rewards((frame * 0.5).astype("uint8"), words).kind != "receipt"
    for missing in ("reward acquired", "touch to continue"):
        assert (
            classify_task_rewards(
                frame, [w for w in words if w.normalized != missing]
            ).kind
            != "receipt"
        )


def recorded_native_receipt():
    png = (FIXTURES / "task-rewards-receipt-merged-native.png").read_bytes()
    words = [Word(**item) for item in json.loads(
        (FIXTURES / "task-rewards-receipt-merged-native.json").read_text()
    )]
    return png, words


def test_native_receipt_merged_background_title_uses_exact_crop(vision, monkeypatch):
    from ba_automator import task_rewards_vision as tv

    png, words = recorded_native_receipt()
    assert any(w.text == "REWARD ACQUIRED!k(s)" for w in words)
    assert classify_task_rewards(decode_frame(png), words).kind == "unknown"
    # Replay the actual full-frame segmentation; the native isolated crop still
    # uses real OCR. Removing account pixels must not erase the original defect.
    monkeypatch.setattr(tv, "read_game_words", lambda *args: words)
    parsed = vision.analyze(png)
    assert parsed.kind == "receipt" and parsed.target == (640, 631)
    assert {"name": "Keystone", "quantity": 1} in parsed.items
    assert {"name": "Normal Activity Report", "quantity": 3} in parsed.items
    assert parsed.claim is None


def test_native_receipt_real_ocr_smoke(vision):
    png, _ = recorded_native_receipt()
    parsed = vision.analyze(png)
    assert parsed.kind == "receipt" and parsed.claim is None


def test_expert_permit_receipt_with_split_isolated_title(vision, monkeypatch):
    from ba_automator import task_rewards_vision as tv
    from ba_automator.loot_receipts import reward_heading_words

    stem = FIXTURES / "task-rewards-expert-permit-split-native"
    png = stem.with_suffix('.png').read_bytes()
    words = [Word(**item) for item in json.loads(stem.with_suffix('.json').read_text())]
    assert classify_task_rewards(decode_frame(png), words).kind == 'unknown'
    # This real crop splits the title; both words together are an exact match.
    heading = reward_heading_words(png, vision.startup, words)
    assert [word.normalized for word in heading] == ['reward', 'acquired']
    monkeypatch.setattr(tv, 'read_game_words', lambda *args: words)
    result = vision.analyze(png)
    assert result.kind == 'receipt' and result.claim is None
    assert {'name': 'Expert Permit', 'quantity': 100} in result.items


@pytest.mark.parametrize("text,confidence,box", [
    ("REWARD ACQUIRED!k(s)", .999, (20, 10, 580, 100)),
    ("REWARD AGQUIRED!", .999, (20, 10, 580, 100)),
    ("REWARD", .999, (20, 10, 580, 100)),
    ("REWARD ACQUIRED!", .94, (20, 10, 580, 100)),
    ("REWARD ACQUIRED!", float("nan"), (20, 10, 580, 100)),
    ("REWARD ACQUIRED!", .999, (20, 120, 580, 200)),
])
def test_native_receipt_fallback_rejects_ambiguous_title(
    monkeypatch, text, confidence, box,
):
    from ba_automator import task_rewards_vision as tv

    png, words = recorded_native_receipt()
    monkeypatch.setattr(tv, "read_game_words", lambda *args: words)
    startup = SimpleNamespace(
        matches=lambda frame: {},
        # Keep the simulated word in the same location at every OCR scale.
        read=lambda frame: [Word(text, confidence, tuple(
            round(value * frame.shape[1] / 610) for value in box))],
    )
    assert TaskRewardsVision(startup).analyze(png).kind == "unknown"


@pytest.mark.parametrize("missing", ["continue", "yellow"])
def test_native_receipt_fallback_retains_other_guards(monkeypatch, missing):
    from ba_automator import task_rewards_vision as tv

    png, words = recorded_native_receipt()
    if missing == "continue":
        words = [w for w in words if w.normalized != "touch to continue"]
    else:
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        image[268:364, 750:1802] = 0
        png = cv2.imencode(".png", image)[1].tobytes()
    monkeypatch.setattr(tv, "read_game_words", lambda *args: words)

    def no_crop(frame):
        pytest.fail("A missing receipt guard must not trigger title recovery")

    startup = SimpleNamespace(matches=lambda frame: {}, read=no_crop)
    assert TaskRewardsVision(startup).analyze(png).kind == "unknown"


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Device:
    def __init__(self, package, steps=(), *, red_dot=True, other=False):
        self.package, self.steps, self.other = package, list(steps), other
        self.screen = Screen("home", red_dot=red_dot)
        self.index, self.claims, self.ignore = 0, 0, False
        self.taps, self.swipes = [], []
        self.png = cv2.imencode(".png", np.zeros((720, 1280, 3), dtype="uint8"))[
            1
        ].tobytes()

    def foreground_package(self):
        return self.package

    def screenshot(self):
        return self.png

    def page(self):
        return (
            self.steps[self.index][0]
            if self.index < len(self.steps)
            else Screen("tasks_empty")
        )

    def tap(self, x, y, **kwargs):
        self.taps.append((x, y))
        if self.screen.kind == "home":
            assert (x, y) == HOME_TASKS
            self.screen = Screen("tasks_other", ALL_TAB) if self.other else self.page()
        elif self.screen.kind == "tasks_other":
            assert (x, y) == ALL_TAB
            self.screen = self.page()
        elif self.screen.kind == "tasks":
            assert (x, y) == self.screen.target
            self.claims += 1
            if not self.ignore:
                self.screen = self.steps[self.index][1]
        elif self.screen.kind == "receipt":
            assert (x, y) == (640, 631)
            self.index += 1
            self.screen = self.page()
        elif self.screen.kind == "tasks_empty":
            assert (x, y) == HOME_BUTTON
            self.screen = Screen("home")
        else:
            pytest.fail("unexpected device input")
        return True

    def swipe(self, *points, **kwargs):
        assert self.screen.kind == "receipt"
        self.swipes.append(points)
        return True


@pytest.fixture
def config(tmp_path):
    return Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        run_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
        lock_dir=tmp_path / "locks",
    )


def receipt(name, amount):
    return Screen(
        "receipt",
        (640, 631),
        items=({"name": name, "quantity": amount},),
        positions=(640,),
    )


def harness(config, device):
    clock = Clock()
    runner = TaskRewardsRunner(
        config,
        device,
        None,
        vision=SimpleNamespace(analyze=lambda *args, **kwargs: device.screen),
        monotonic=clock,
        sleep=clock.sleep,
    )
    return runner


def actions(config):
    path = config.state_dir / "important-actions.jsonl"
    return (
        [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
    )


def test_no_badge_sends_no_input_or_reward_log(config):
    device = Device(config.package, red_dot=False)
    r = harness(config, device)
    try:
        result = r.run()
        assert result.status == "success" and result.actions == 0
        assert device.taps == [] and device.swipes == [] and actions(config) == []
    finally:
        r.journal.close()


def test_ignored_home_entry_retries_are_paced_and_bounded(config):
    device = Device(config.package)
    r = harness(config, device)
    taps = []

    def ignore(x, y, **kwargs):
        taps.append((x, y, r.clock()))
        return True

    device.tap = ignore
    try:
        with pytest.raises(TaskError, match="did not reach tasks, tasks_empty"):
            r.enter()
        assert taps == [(50, 261, 0), (50, 261, 11.7), (50, 261, 23.4)]
        assert device.claims == 0
    finally:
        r.journal.close()


def test_later_tasks_entry_succeeds_without_replaying_a_claim(config):
    device = Device(config.package, [(Screen("tasks", (1150, 670), claim="all"), receipt("AP", 50))])
    r = harness(config, device)
    original_tap = device.tap
    times = []

    def delayed(x, y, **kwargs):
        times.append(r.clock())
        if len(times) < 3:
            device.taps.append((x, y))
            return True
        return original_tap(x, y, **kwargs)

    device.tap = delayed
    try:
        assert r.enter().screen.kind == "tasks"
        assert times == pytest.approx([0, 11.7, 23.4])
        assert device.taps == [HOME_TASKS] * 3
        assert device.claims == 0
    finally:
        r.journal.close()


def test_tasks_immediate_entry_does_not_wait_for_home_settle(config):
    device = Device(config.package, other=True)
    r = harness(config, device)
    try:
        assert r.enter().screen.kind == "tasks_empty"
        assert device.taps == [HOME_TASKS, ALL_TAB]
        assert r.clock() == pytest.approx(3.4)
    finally:
        r.journal.close()


def test_third_home_attempt_can_still_select_all_tab(config):
    device = Device(config.package, other=True)
    r = harness(config, device)
    original_tap = device.tap

    def delayed(x, y, **kwargs):
        if len(device.taps) < 2:
            assert device.screen.kind == "home"
            device.taps.append((x, y))
            return True
        return original_tap(x, y, **kwargs)

    device.tap = delayed
    try:
        assert r.enter().screen.kind == "tasks_empty"
        assert device.taps == [HOME_TASKS] * 3 + [ALL_TAB]
        assert r.clock() == pytest.approx(26.8)
        assert device.claims == 0
    finally:
        r.journal.close()


def test_tasks_all_tab_retry_budget_remains_bounded(config):
    device = Device(config.package, other=True)
    r = harness(config, device)
    original_tap = device.tap

    def ignored_all(x, y, **kwargs):
        if device.screen.kind == "tasks_other":
            device.taps.append((x, y))
            return True
        return original_tap(x, y, **kwargs)

    device.tap = ignored_all
    try:
        with pytest.raises(TaskError, match="did not reach tasks, tasks_empty"):
            r.enter()
        assert device.taps == [HOME_TASKS] + [ALL_TAB] * 3
        assert device.claims == 0
    finally:
        r.journal.close()


def test_changed_screen_during_tasks_entry_settle_gets_no_retry(config):
    device = Device(config.package)
    r = harness(config, device)
    original_sleep = r.sleep
    device.tap = lambda x, y, **kwargs: device.taps.append((x, y)) or True

    def sleep(seconds):
        original_sleep(seconds)
        if seconds == 10:
            device.screen = Screen("unknown")

    r.sleep = sleep
    try:
        with pytest.raises(TaskError, match="did not reach"):
            r.enter()
        assert device.taps == [HOME_TASKS]
        assert device.claims == 0
    finally:
        r.journal.close()


@pytest.mark.parametrize("fault", ["stale", "foreground"])
def test_tasks_entry_retry_requires_fresh_home_and_foreground(config, fault):
    device = Device(config.package)
    r = harness(config, device)
    original_wait = r.wait
    waits = 0
    device.tap = lambda x, y, **kwargs: device.taps.append((x, y)) or True

    def wait(*args, **kwargs):
        nonlocal waits
        frame = original_wait(*args, **kwargs)
        waits += 1
        if waits == 3:
            if fault == "stale":
                r.sleep(6)
            else:
                device.package = "com.android.settings"
        return frame

    r.wait = wait
    try:
        with pytest.raises(TaskError, match="fresh recognized|Foreground changed"):
            r.enter()
        assert device.taps == [HOME_TASKS]
    finally:
        r.journal.close()


def test_claim_all_newly_unlocked_rewards_and_daily_completion_return_home(config):
    steps = [
        (Screen("tasks", (1150, 670), claim="all"), receipt("AP", 300)),
        (Screen("tasks", (1150, 670), claim="all"), receipt("Keystone", 1)),
        (
            Screen("tasks", (974, 671), claim="daily completion"),
            receipt("Pyroxenes", 20),
        ),
    ]
    device = Device(config.package, steps, other=True)
    r = harness(config, device)
    try:
        assert r.run().status == "success"
        assert device.claims == 3 and ALL_TAB in device.taps
        assert device.screen.kind == "home" and not device.screen.red_dot
        received = [
            a for a in actions(config) if a["action"] == "task_rewards_received"
        ]
        assert len(received) == 3 and all(
            Path(a["evidence"]).is_file() for a in received
        )
        assert loot.snapshot(config)["items"] == [
            {"name": "AP", "quantity": 300},
            {"name": "Keystone", "quantity": 1},
            {"name": "Pyroxenes", "quantity": 20},
        ]
    finally:
        r.journal.close()


def test_missing_receipt_never_repeats_claim_or_reports_loot(config):
    device = Device(
        config.package, [(Screen("tasks", (1150, 670), claim="all"), receipt("AP", 50))]
    )
    device.ignore = True
    r = harness(config, device)
    try:
        with pytest.raises(TaskError, match="did not reach receipt"):
            r.run()
        assert device.claims == 1 and loot.snapshot(config)["receipt_count"] == 0
    finally:
        r.journal.close()


def test_receipt_pages_merge_overlap_without_double_counting(config):
    d = Device(config.package)
    r = harness(config, d)

    def frame(items, positions):
        return ShopFrame(
            Capture(d.png, r.clock(), d.package),
            Screen(
                "receipt",
                (640, 631),
                items=tuple({"name": n, "quantity": q} for n, q in items),
                positions=positions,
            ),
        )

    left = frame([("AP", 300), ("Pyroxenes", 20)], (200, 400))
    right = frame([("Pyroxenes", 20), ("Keystone", 2)], (200, 400))
    views = iter([left, left, right, right, right])
    r.pan_receipt = lambda *args, **kwargs: next(views)
    try:
        r.log_receipt(left)
        assert loot.snapshot(config)["items"] == [
            {"name": "AP", "quantity": 300},
            {"name": "Keystone", "quantity": 2},
            {"name": "Pyroxenes", "quantity": 20},
        ]
        assert loot.snapshot(config)["receipt_count"] == 1
    finally:
        r.journal.close()


@pytest.mark.parametrize("bad", [receipt("AP", 301), receipt("Keystone", 3)])
def test_inconsistent_or_disjoint_receipt_pages_do_not_invent_rewards(config, bad):
    d = Device(config.package)
    r = harness(config, d)
    left = ShopFrame(Capture(d.png, 0, d.package), receipt("AP", 300))
    invalid = ShopFrame(left.capture, bad)
    views = iter([left, left, invalid])
    r.pan_receipt = lambda *args, **kwargs: next(views)
    try:
        with pytest.raises(TaskError):
            r.log_receipt(left)
        assert loot.snapshot(config)["receipt_count"] == 0
    finally:
        r.journal.close()


def test_task_plans_and_daily_order(config):
    assert task_plan("tasks", config) == ("restart", "tasks", "red_dots")
    assert task_plan("daily", config)[-2:] == ("tasks", "red_dots")
    daily = task_plan("daily", replace(config, ap_schedule_enabled=True))
    assert daily[-2:] == ("tasks", "red_dots")
    # AP runs separately at the lowest queue priority, after ready chores.
    assert "spend_ap" not in daily


def test_claim_limit_stops_a_never_ending_sequence(config):
    steps = [(Screen("tasks", (1150, 670), claim="all"), receipt("Keystone", 1))] * (
        MAX_CLAIMS + 1
    )
    device = Device(config.package, steps)
    r = harness(config, device)
    try:
        with pytest.raises(TaskError, match="claim limit"):
            r.run()
        assert device.claims == MAX_CLAIMS
    finally:
        r.journal.close()


def test_remaining_home_dot_is_not_reported_as_success(config):
    d = Device(config.package)
    r = harness(config, d)
    r.home = lambda: None
    r.wait = lambda *args, **kwargs: ShopFrame(
        Capture(d.png, 0, d.package), Screen("home", red_dot=True)
    )
    r.tap = lambda *args: None
    empty = ShopFrame(Capture(d.png, 0, d.package), Screen("tasks_empty"))
    calls = iter([empty, ShopFrame(empty.capture, Screen("home", red_dot=True))])
    r.wait = lambda *args, **kwargs: next(calls)
    try:
        with pytest.raises(TaskError, match="still has a red dot"):
            r.finish_collection(empty)
    finally:
        r.journal.close()
