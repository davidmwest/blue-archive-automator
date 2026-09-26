from datetime import datetime
from dataclasses import replace
from pathlib import Path
import pytest
from ba_automator.config import Config
from ba_automator.club import game_day
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame
from ba_automator.ticket_state import allocation, read_state, write_state
from ba_automator.ticket_vision import TicketScreen, TicketVision, classify_tickets
from ba_automator.tickets import TicketRunner
from ba_automator.tasks import task_plan
from ba_automator.vision import StartupVision, decode_frame, classify


@pytest.mark.parametrize("n", range(40))
@pytest.mark.parametrize("weekday", range(7))
def test_every_ticket_used_and_weekday_extras_wrap(n, weekday):
    day = f"2026-09-{21+weekday}"
    q = allocation(n, day)
    assert sum(q) == n and max(q) - min(q) <= 1
    assert [i for i in range(3) if q[i] > n // 3] == sorted(
        (weekday % 3 + i) % 3 for i in range(n % 3)
    )


def test_reset_is_stable_across_local_midnight():
    assert game_day(datetime.fromisoformat("2026-09-25T01:00:00+00:00")) == "2026-09-24"
    assert allocation(8, "2026-09-24") == [3, 3, 2]
    assert allocation(8, "2026-09-25") == [2, 3, 3]


@pytest.fixture(scope="module")
def vision():
    return TicketVision(StartupVision())


@pytest.mark.parametrize(
    "name,kind,task",
    [
        ("bounty-menu", "menu", "bounties"),
        ("bounty-five", "menu", "bounties"),
        ("bounty-zero", "list", "bounties"),
        ("desert-list", "list", "bounties"),
        ("classroom-list", "list", "bounties"),
        ("bounty-receipt", "receipt", "bounties"),
        ("bounty-overpass", "list", "bounties"),
        ("bounty-detail", "detail", "bounties"),
        ("bounty-confirm", "confirm", "bounties"),
        ("scrimmage-menu-stable", "menu", "scrimmages"),
        ("scrimmage-list", "list", "scrimmages"),
        ("scrimmage-detail", "detail", "scrimmages"),
        ("scrimmage-confirm", "confirm", "scrimmages"),
    ],
)
def test_real_sanitized_ticket_screens(vision, name, kind, task):
    s = vision.analyze(
        (Path(__file__).parent / "fixtures" / f"tickets-{name}.png").read_bytes()
    )
    assert (s.kind, s.task) == (kind, task)
    if kind == "menu":
        assert s.tickets == (5 if name == "bounty-five" else 15)
    if kind == "detail":
        assert (s.tickets, s.after_tickets, s.count, s.stars, s.ap_cost) == (
            15,
            14,
            1,
            3,
            0,
        )
    if kind == "receipt":
        assert s.count == 5
    if name == "bounty-overpass":
        assert [(r.id, r.stars) for r in s.stages] == [
            ("F", 3),
            ("G", 3),
            ("H", 3),
            ("I", 2),
            ("J", 0),
        ]
    if name == "scrimmage-list":
        assert [(r.id, r.stars) for r in s.stages] == [
            ("A", 3),
            ("B", 3),
            ("C", 0),
            ("D", 0),
        ] and s.stages[-1].target is None


def test_attendance_exact_calendar_and_negative_controls(vision):
    png = (Path(__file__).parent / "fixtures/tickets-initial.png").read_bytes()
    v = vision.startup
    assert v.analyze(png).target == (1190, 675)
    words = v.read(decode_frame(png))
    for removed in ["Arona's Attendance!", "Daily"]:
        assert classify([w for w in words if w.text != removed], {}).state == "unknown"
    for day in range(1, 11):
        # The highlighted day's text can disappear, regardless of weekday.
        other = [w for w in words if w.normalized.replace(" ", "") != f"day{day}"]
        assert classify(other, {}).state == "popup"
    partial = [
        w for w in words if w.normalized.replace(" ", "") not in {"day1", "day2"}
    ]
    assert classify(partial, {}).state == "unknown"


@pytest.fixture
def runner(tmp_path):
    c = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        run_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )
    r = TicketRunner(
        c,
        None,
        None,
        vision=object(),
        monotonic=lambda: 0,
        sleep=lambda _: None,
        wall_clock=lambda: datetime.fromisoformat("2026-09-24T20:00:00+00:00"),
    )
    r.state = dict(
        version=1,
        day="2026-09-24",
        total=15,
        quotas=[5, 5, 5],
        done=[0, 0, 0],
        pending=None,
    )
    yield r
    r.journal.close()


def frame(r, s):
    return ShopFrame(Capture(b"fixture", 0, r.config.package), s)


def detail(**kwargs):
    values = dict(
        kind="detail",
        task="bounties",
        area="Overpass",
        stage="H",
        ap=150,
        tickets=15,
        after_tickets=10,
        after_ap=150,
        count=5,
        ap_cost=0,
        stars=3,
        target=(937, 405),
    )
    return TicketScreen(**(values | kwargs))


def test_quantity_recovers_observed_overshoot_without_spending(runner):
    counts = iter([2, 3, 4, 7, 1, 2, 3, 4, 5])
    taps = []
    runner.tap = lambda frame, target, detail: taps.append(target)

    def wait(kind, *, predicate):
        count = next(counts)
        observed = detail(count=count, after_tickets=15 - count)
        assert kind == "detail" and predicate(observed)
        assert not predicate(replace(observed, area="Classroom"))
        return frame(runner, observed)

    runner.wait = wait
    observed = runner.quantity(frame(runner, detail(count=1, after_tickets=14)), 5)
    assert observed.screen.count == 5
    assert taps == [(1015, 300)] * 4 + [(788, 300)] + [(1015, 300)] * 4
    assert runner.state["pending"] is None


def test_repeated_quantity_overshoot_stops_after_bounded_adjustments(runner):
    taps = []
    runner.tap = lambda frame, target, detail: taps.append(target)

    def wait(kind, *, predicate):
        count = 7 if taps[-1] == (1015, 300) else 1
        observed = detail(count=count, after_tickets=15 - count)
        assert predicate(observed)
        return frame(runner, observed)

    runner.wait = wait
    with pytest.raises(TaskError, match="quantity did not settle"):
        runner.quantity(frame(runner, detail(count=1, after_tickets=14)), 5)
    assert len(taps) == 18
    assert set(taps) == {(1015, 300), (788, 300)}
    assert runner.state["pending"] is None


@pytest.mark.parametrize(
    "s,count",
    [
        (detail(stars=2), 5),
        (detail(tickets=4), 5),
        (detail(ap_cost=15, after_ap=75), 5),
        (detail(target=None), 5),
        (detail(), 6),
        (detail(area="Classroom"), 5),
    ],
)
def test_invalid_spend_sends_no_input(runner, s, count):
    runner.tap = lambda *a: pytest.fail("unsafe input")
    with pytest.raises(TaskError):
        runner.sweep(frame(runner, s), count, 0)


def setup_sweep(r, confirm=None, receipt=None, after=None):
    screens = iter(
        [
            confirm
            or TicketScreen(
                "confirm",
                task="bounties",
                ap=150,
                tickets=5,
                count=5,
                ap_cost=0,
                target=(767, 509),
            ),
            receipt
            or TicketScreen(
                "receipt", task="bounties", ap=150, count=5, target=(640, 583)
            ),
            after or detail(tickets=10, after_tickets=9, count=1),
        ]
    )
    r.wait = lambda *a, **kw: frame(r, next(screens))
    r.journal.save_image = lambda *a: None
    taps = []

    def tap(*args):
        taps.append(args)
        if len(taps) == 2:
            assert read_state(r.config, r.task)["pending"]["count"] == 5

    r.tap = tap
    return taps


def test_confirmed_receipt_balances_and_persisted_share(runner):
    taps = setup_sweep(runner)
    runner.sweep(frame(runner, detail()), 5, 0)
    saved = read_state(runner.config, runner.task)
    assert saved["done"] == [5, 0, 0] and saved["pending"] is None and len(taps) == 3


@pytest.mark.parametrize(
    "elapsed,ap,tickets,accepted",
    [
        (240, 151, 10, True),
        (359, 152, 10, False),
        (361, 152, 10, True),
        (721, 153, 10, True),
        (721, 154, 10, False),
        (721, 149, 10, False),
        (721, 153, 11, False),
    ],
)
def test_loot_inspection_allows_only_time_bounded_ap_recovery(
    runner, monkeypatch, elapsed, ap, tickets, accepted
):
    from ba_automator import tickets as module

    clock = [0.0]
    runner.clock = lambda: clock[0]
    taps = setup_sweep(runner, after=detail(ap=ap, tickets=tickets))

    def inspect(task, receipt, evidence):
        assert read_state(task.config, task.task)["pending"] is not None
        clock[0] += elapsed
        return replace(receipt, capture=replace(receipt.capture, captured_at=clock[0]))

    monkeypatch.setattr(module, "inspect_receipt", inspect)
    if accepted:
        runner.sweep(frame(runner, detail()), 5, 0)
        assert read_state(runner.config, runner.task)["pending"] is None
        assert read_state(runner.config, runner.task)["done"] == [5, 0, 0]
    else:
        with pytest.raises(TaskError, match="Post-sweep"):
            runner.sweep(frame(runner, detail()), 5, 0)
        assert read_state(runner.config, runner.task)["pending"] is not None
        assert read_state(runner.config, runner.task)["done"] == [0, 0, 0]
    assert len(taps) == 3


@pytest.mark.parametrize(
    "confirm",
    [
        TicketScreen("confirm", task="bounties", ap=150, tickets=4, count=4, ap_cost=0),
        TicketScreen("confirm", task="bounties", ap=150, tickets=5, count=5, ap_cost=1),
        TicketScreen("confirm", task="bounties", ap=149, tickets=5, count=5, ap_cost=0),
    ],
)
def test_changed_confirmation_does_not_spend(runner, confirm):
    taps = setup_sweep(runner, confirm=confirm)
    with pytest.raises(TaskError, match="confirmation"):
        runner.sweep(frame(runner, detail()), 5, 0)
    assert len(taps) == 1 and read_state(runner.config, runner.task)["pending"] is None


@pytest.mark.parametrize(
    "receipt,after",
    [
        (TicketScreen("receipt", task="bounties", ap=150, count=4), None),
        (None, detail(tickets=11)),
        (TicketScreen("receipt", task="bounties", ap=130, count=5), None),
    ],
)
def test_uncertain_result_blocks_replay(runner, receipt, after):
    setup_sweep(runner, receipt=receipt, after=after)
    with pytest.raises(TaskError):
        runner.sweep(frame(runner, detail()), 5, 0)
    assert read_state(runner.config, runner.task)["pending"] is not None
    with pytest.raises(TaskError, match="unresolved"):
        runner.run()


def test_pending_blocks_even_after_reset(runner):
    setup_sweep(
        runner, receipt=TicketScreen("receipt", task="bounties", ap=150, count=1)
    )
    with pytest.raises(TaskError):
        runner.sweep(frame(runner, detail()), 5, 0)
    runner.wall_clock = lambda: datetime.fromisoformat("2026-09-25T20:00:00+00:00")
    with pytest.raises(TaskError, match="unresolved"):
        runner.run()


def test_daily_ticket_plan_and_optional_toggles(runner):
    assert task_plan("bounties", runner.config) == ("restart", "bounties", "red_dots")
    assert task_plan("scrimmages", runner.config) == ("restart", "scrimmages", "red_dots")
    plan = task_plan("daily", runner.config)
    assert plan.index("bounties") < plan.index("scrimmages") < plan.index("lessons")
    disabled = replace(
        runner.config,
        bounties_enabled_in_daily=False,
        scrimmages_enabled_in_daily=False,
    )
    assert "bounties" not in task_plan(
        "daily", disabled
    ) and "scrimmages" not in task_plan("daily", disabled)


def test_config_snapshot_and_dashboard_settings_round_trip(tmp_path):
    from ba_automator.server import _config_document, _toml, DashboardController

    c = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        state_dir=tmp_path / "state",
        run_dir=tmp_path / "runs",
    )
    path = tmp_path / "config.toml"
    path.write_text(_toml(_config_document(c)))
    assert Config.from_file(path).bounties_enabled_in_daily is True
    controller = DashboardController(path)
    try:
        changed = controller.update_settings(
            {"bounties_enabled_in_daily": False, "scrimmages_enabled_in_daily": False}
        )
        assert changed["config"]["bounties_enabled_in_daily"] is False
        reloaded = Config.from_file(path)
        assert (
            reloaded.scrimmages_enabled_in_daily is False
            and reloaded.bounties_enabled_in_daily is False
        )
    finally:
        controller.close()


@pytest.mark.parametrize(
    "name", ["bounties_enabled_in_daily", "scrimmages_enabled_in_daily"]
)
def test_daily_switches_reject_non_booleans(runner, name):
    from ba_automator.config import ConfigError

    with pytest.raises(ConfigError):
        replace(runner.config, **{name: 1})


def test_scrimmage_reads_nonzero_ap_cost_and_rejects_inconsistent_projection(vision):
    png = (Path(__file__).parent / "fixtures/tickets-scrimmage-detail.png").read_bytes()
    frame = decode_frame(png)
    words = vision.startup.read(frame)
    changed = [
        (
            replace(w, text=w.text.replace("414→414", "414→399"))
            if "414→414" in w.text
            else w
        )
        for w in words
    ]
    s = classify_tickets(frame, changed)
    assert s.kind == "detail" and s.ap_cost == 15 and s.after_ap == 399
    bad = [
        replace(w, text=w.text.replace("15→14", "15→13")) if "15→14" in w.text else w
        for w in changed
    ]
    assert classify_tickets(frame, bad).kind == "unknown"


def test_real_split_scrimmage_projection_preserves_both_resource_counters(vision):
    png = (Path(__file__).parent / "fixtures/tickets-scrimmage-split-projection.png").read_bytes()
    screen = vision.analyze(png)
    assert (screen.kind, screen.task, screen.area, screen.stage) == (
        "detail", "scrimmages", "Trinity", "B"
    )
    assert (screen.ap, screen.after_ap, screen.tickets, screen.after_tickets,
            screen.count, screen.ap_cost, screen.stars) == (643, 643, 15, 14, 1, 0, 3)
    frame = decode_frame(png)
    words = vision.startup.read(frame)
    paid = [replace(w, text="643→628") if w.text == "643→643" else w for w in words]
    assert classify_tickets(frame, paid).ap_cost == 15
    inconsistent = [replace(w, text="15→13") if w.text == "15→14" else w for w in words]
    assert classify_tickets(frame, inconsistent).kind == "unknown"


def test_scrimmage_rejects_projection_with_no_resource_boundary(vision):
    from ba_automator.vision import Word
    from ba_automator.crafting_vision import within

    png = (Path(__file__).parent / "fixtures/tickets-scrimmage-split-projection.png").read_bytes()
    frame = decode_frame(png)
    words = vision.startup.read(frame)
    projection = within(words, (855, 337, 1108, 384))
    words = [word for word in words if word not in projection]
    words.append(Word("643→64315→14", .99, (874, 347, 1097, 378)))
    assert classify_tickets(frame, words).kind == "unknown"


def test_real_bounty_quantity_jump_remains_readable_before_confirmation(vision):
    png = (Path(__file__).parent / "fixtures/tickets-bounty-quantity-seven.png").read_bytes()
    screen = vision.analyze(png)
    assert (screen.kind, screen.task, screen.area, screen.stage) == (
        "detail", "bounties", "Desert Railroad", "H"
    )
    assert (screen.tickets, screen.after_tickets, screen.count, screen.ap_cost) == (10, 3, 7, 0)


def test_scrimmage_paid_ap_confirmation(vision):
    png = (
        Path(__file__).parent / "fixtures/tickets-scrimmage-confirm.png"
    ).read_bytes()
    frame = decode_frame(png)
    words = vision.startup.read(frame)
    changed = [replace(w, text=w.text.replace("AP 0", "AP 15")) for w in words]
    s = classify_tickets(frame, changed)
    assert (s.kind, s.ap_cost, s.count) == ("confirm", 15, 1)


def test_same_day_completed_shares_are_not_redistributed(runner):
    runner.state["done"] = [5, 5, 5]
    write_state(runner.config, runner.task, runner.state)
    runner.wait = lambda *a, **k: frame(runner, TicketScreen("home"))
    runner.enter_menu = lambda: frame(
        runner, TicketScreen("menu", task="bounties", tickets=3)
    )
    runner.enter_area = lambda *a: pytest.fail(
        "completed share repeated or late tickets reallocated"
    )
    runner.tap = lambda f, target, *a: (
        None if target == (1237, 24) else pytest.fail("unexpected input")
    )
    runner.home = lambda: None
    runner.run()
    assert read_state(runner.config, runner.task)["total"] == 15


def test_partial_run_preserves_original_allocation(runner):
    runner.state["done"] = [5, 0, 0]
    write_state(runner.config, runner.task, runner.state)
    runner.wait = lambda *a, **k: frame(runner, TicketScreen("home"))
    runner.enter_menu = lambda: frame(
        runner, TicketScreen("menu", task="bounties", tickets=10)
    )
    visited = []

    def enter(area):
        visited.append(area)
        assert area == "Desert Railroad" and runner.state["quotas"] == [5, 5, 5]
        raise RuntimeError("stop before further input")

    runner.enter_area = enter
    with pytest.raises(RuntimeError, match="stop before"):
        runner.run()
    assert visited == ["Desert Railroad"]


def test_external_ticket_decrease_stops_before_entering_an_area(runner):
    write_state(runner.config, runner.task, runner.state)
    runner.wait = lambda *a, **k: frame(runner, TicketScreen("home"))
    runner.enter_menu = lambda: frame(
        runner, TicketScreen("menu", task="bounties", tickets=14)
    )
    runner.enter_area = lambda *a: pytest.fail("allocation changed without inspection")
    with pytest.raises(TaskError, match="outside this allocation"):
        runner.run()


def test_last_ticket_returns_directly_to_list(runner):
    s = detail(tickets=5, after_tickets=0)
    runner.state["done"] = [0, 5, 5]
    setup_sweep(
        runner,
        after=TicketScreen("list", task="bounties", area="Overpass", ap=150, tickets=0),
    )
    runner.sweep(frame(runner, s), 5, 0)
    assert read_state(runner.config, runner.task)["done"] == [5, 5, 5]


@pytest.mark.parametrize("name", ["scrimmage-zero", "scrimmage-receipt"])
def test_exhausted_scrimmage_receipt_and_zero_selector(vision, name):
    png = (Path(__file__).parent / "fixtures" / f"tickets-{name}.png").read_bytes()
    s = vision.analyze(png)
    if name.endswith("zero"):
        assert (s.kind, s.area, s.count, s.tickets, s.after_tickets, s.target) == (
            "detail",
            "Millennium",
            0,
            0,
            0,
            None,
        )
    else:
        assert (s.kind, s.task, s.count) == ("receipt", "scrimmages", 5)


def test_exhausted_scrimmage_preserves_spaced_zero_arrow(vision):
    png = (Path(__file__).parent / "fixtures/tickets-scrimmage-zero.png").read_bytes()
    frame = decode_frame(png)
    words = [replace(w, text=w.text.replace("0→-", "0 → -"))
             for w in vision.startup.read(frame)]
    screen = classify_tickets(frame, words)
    assert (screen.kind, screen.count, screen.tickets, screen.after_tickets, screen.target) == (
        "detail", 0, 0, 0, None,
    )


@pytest.fixture(scope="module")
def bounty_zero_selector(vision):
    import json
    from ba_automator.vision import Word

    png = (Path(__file__).parent / "fixtures/tickets-bounty-zero-selector.png").read_bytes()
    observed = json.loads((Path(__file__).parent / "fixtures/tickets-bounty-zero-selector.json").read_text())
    return png, [Word(**word) for word in observed["words"]]


def test_real_exhausted_bounty_recovers_omitted_projection_dash(vision, bounty_zero_selector):
    png, words = bounty_zero_selector
    assert classify_tickets(decode_frame(png), words).kind == "unknown"
    # Masking unrelated pixels changes full-frame OCR. Replay the original
    # sanitized observations while running actual OCR on both unchanged crops.
    class Startup:
        def __init__(self):
            self.crops = []

        def read(self, frame):
            if frame.shape[:2] == (720, 1280):
                return words
            self.crops.append(frame.shape[:2])
            return vision.startup.read(frame)

        def matches(self, frame):
            return {}

    startup = Startup()
    screen = TicketVision(startup).analyze(png)
    assert startup.crops == [(102, 190), (153, 285)]
    assert (screen.kind, screen.task, screen.area, screen.stage) == (
        "detail", "bounties", "Classroom", "H",
    )
    assert (screen.ap, screen.after_ap, screen.count, screen.tickets,
            screen.after_tickets, screen.target) == (740, 740, 0, 0, 0, None)
    assert vision.analyze(png) == screen


@pytest.mark.parametrize("readings", [
    [[], []],
    [[("0→-", .99)], [("0→1", .99)]],
    [[("0→-", .99)], [("0→-", .89)]],
    [[("0→", .99)], [("0→-", .99)]],
    [[("0→-", .99), ("1", .99)], [("0→-", .99)]],
])
def test_exhausted_bounty_fallback_rejects_unproven_zero(bounty_zero_selector, readings):
    from ba_automator.vision import Word

    png, words = bounty_zero_selector

    class Startup:
        def __init__(self):
            self.crops = iter(readings)

        def read(self, frame):
            if frame.shape[:2] == (720, 1280):
                return words
            return [Word(text, confidence, (0, 0, 100, 30))
                    for text, confidence in next(self.crops)]

        def matches(self, frame):
            return {}

    assert TicketVision(Startup()).analyze(png).kind == "unknown"


@pytest.mark.parametrize("change", ["nonzero_quantity", "no_ap", "wrong_stage", "no_heading"])
def test_exhausted_bounty_fallback_requires_complete_readonly_detail(bounty_zero_selector, change):
    from ba_automator.crafting_vision import within

    png, original = bounty_zero_selector
    words = original
    if change == "nonzero_quantity":
        count = within(words, (904, 277, 970, 330))
        words = [replace(word, text="1") if word in count else word for word in words]
    elif change == "no_ap":
        words = [word for word in words if "/218" not in word.text]
    elif change == "wrong_stage":
        words = [replace(word, text="Besieged Classroom G")
                 if "Besieged Classroom" in word.text else word for word in words]
    else:
        words = [word for word in words if word.normalized != "mission info"]

    class Startup:
        def read(self, frame):
            assert frame.shape[:2] == (720, 1280), "invalid detail must not start fallback OCR"
            return words

        def matches(self, frame):
            return {}

    assert TicketVision(Startup()).analyze(png).kind == "unknown"


def test_last_ticket_zero_selector_clears_pending_after_balance_verification(runner):
    runner.state["done"] = [0, 5, 5]
    setup_sweep(runner, after=detail(tickets=0, after_tickets=0, count=0,
                                   ap_cost=None, target=None))
    runner.sweep(frame(runner, detail(tickets=5, after_tickets=0)), 5, 0)
    saved = read_state(runner.config, runner.task)
    assert saved["done"] == [5, 5, 5]
    assert saved["pending"] is None


@pytest.fixture(scope="module")
def scrimmage_missing_projection():
    import json
    from ba_automator.vision import Word

    fixture = Path(__file__).parent / "fixtures/tickets-scrimmage-zero-missing-projection"
    observed = json.loads(fixture.with_suffix(".json").read_text())
    return fixture.with_suffix(".png").read_bytes(), [Word(**word) for word in observed["words"]]


def test_scrimmage_recovers_entire_missing_zero_label(vision, scrimmage_missing_projection):
    png, words = scrimmage_missing_projection
    assert classify_tickets(decode_frame(png), words).kind == "unknown"

    class Startup:
        def __init__(self):
            self.crops = []

        def read(self, frame):
            if frame.shape[:2] == (720, 1280):
                return words
            self.crops.append(frame.shape[:2])
            return vision.startup.read(frame)

        def matches(self, frame):
            return {}

    startup = Startup()
    screen = TicketVision(startup).analyze(png)
    assert startup.crops == [(92, 138), (138, 207)]
    assert (screen.kind, screen.task, screen.area, screen.stage) == (
        "detail", "scrimmages", "Millennium", "B",
    )
    assert (screen.ap, screen.after_ap, screen.count, screen.tickets,
            screen.after_tickets, screen.target) == (720, 720, 0, 0, 0, None)
    assert vision.analyze(png) == screen


@pytest.mark.parametrize("readings", [
    [[], []],
    [[("0→-", .99)], [("0→1", .99)]],
    [[("0→-", .99)], [("0→-", .89)]],
    [[("0→", .99)], [("0→-", .99)]],
    [[("0→-", .99), ("1", .99)], [("0→-", .99)]],
])
def test_missing_scrimmage_label_requires_two_complete_reads(scrimmage_missing_projection, readings):
    from ba_automator.vision import Word

    png, words = scrimmage_missing_projection

    class Startup:
        def __init__(self):
            self.readings = iter(readings)

        def read(self, frame):
            if frame.shape[:2] == (720, 1280):
                return words
            return [Word(text, confidence, (0, 0, 100, 30))
                    for text, confidence in next(self.readings)]

        def matches(self, frame):
            return {}

    assert TicketVision(Startup()).analyze(png).kind == "unknown"


@pytest.mark.parametrize("change", [
    "nonzero_quantity", "no_ap", "wrong_stage", "no_heading", "ap_changed", "ticket_conflict", "bounty",
])
def test_missing_scrimmage_label_does_not_infer_other_fields(scrimmage_missing_projection, change):
    from ba_automator.crafting_vision import within
    from ba_automator.vision import Word

    png, original = scrimmage_missing_projection
    words = original
    if change == "nonzero_quantity":
        count = within(words, (904, 277, 970, 330))
        words = [replace(word, text="1") if word in count else word for word in words]
    elif change == "no_ap":
        words = [word for word in words if "/218" not in word.text]
    elif change == "wrong_stage":
        words = [replace(word, text="Millennium C") if word.text == "Millennium B" else word for word in words]
    elif change == "no_heading":
        words = [word for word in words if word.normalized != "mission info"]
    elif change == "ap_changed":
        words = [replace(word, text="720→719") if word.text == "720→720" else word for word in words]
    elif change == "ticket_conflict":
        words = [*words, Word("5→4", .99, (1040, 348, 1093, 378))]
    else:
        words = [replace(word, text="Bounty") if word.normalized == "scrimmage" else word for word in words]

    class Startup:
        def read(self, frame):
            assert frame.shape[:2] == (720, 1280), "ambiguous detail must not start crop recovery"
            return words

        def matches(self, frame):
            return {}

    assert TicketVision(Startup()).analyze(png).kind == "unknown"
