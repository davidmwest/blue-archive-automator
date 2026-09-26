from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
from ba_automator.config import Config
from ba_automator.ap_vision import APScreen
from ba_automator.ap_state import read_state, write_state
from ba_automator.spend_ap import APRunner
from ba_automator.shop_runtime import ShopFrame
from ba_automator.runtime import Capture, TaskError
from ba_automator.tasks import task_plan


@pytest.mark.parametrize("failure,pending,existing_hold,retry", [
    ("navigation", False, False, True),
    ("navigation", True, False, False),
    ("navigation", False, True, False),
    ("unexpected", False, False, False),
    ("stopped", False, False, False),
])
def test_transient_failure_retries_only_without_unresolved_spend(
    tmp_path, monkeypatch, failure, pending, existing_hold, retry
):
    from datetime import datetime, timezone, timedelta
    from ba_automator.spend_ap import run_spend_ap

    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    c = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
               state_dir=tmp_path / "state", run_dir=tmp_path / "runs",
               lock_dir=tmp_path / "locks")
    state = read_state(c)
    if pending:
        state["pending"] = dict(strategy="elephs", stage="1-1", ap_before=160,
                                cost=20, count=1, floor=100)
    if existing_hold:
        state["blocked_reason"] = "earlier uncertain result"
    write_state(c, state)

    def fail_run(self):
        self.state = read_state(c)
        if failure == "unexpected":
            raise ValueError("unexpected bug")
        if failure == "stopped":
            raise KeyboardInterrupt()
        self.fail("spend_ap did not reach detail")

    monkeypatch.setattr(APRunner, "run", fail_run)
    device = SimpleNamespace(connect=lambda: None, verify_package=lambda: None)
    with pytest.raises((TaskError, ValueError, KeyboardInterrupt)):
        run_spend_ap(c, device, None, vision=object(), wall_clock=lambda: now)
    saved = read_state(c)
    assert saved["pending"] == state["pending"]
    if retry:
        assert saved["blocked_reason"] is None
        assert datetime.fromisoformat(saved["next_check_at"]) == now + timedelta(minutes=15)
        assert "retry in 15 minutes" in saved["last_summary"]
    else:
        assert saved["blocked_reason"]


@pytest.fixture
def runner(tmp_path):
    c = Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        state_dir=tmp_path / "state",
        run_dir=tmp_path / "runs",
    )
    r = APRunner(
        c, None, None, vision=object(), monotonic=lambda: 0, sleep=lambda _: None
    )
    r.state = read_state(c)
    yield r
    r.journal.close()


def frame(r, screen):
    return ShopFrame(Capture(b"fixture", 0, r.config.package), screen)


@pytest.mark.parametrize("right", [False, True])
def test_area_step_reobserves_an_arrow_hidden_by_the_previous_tap(runner, right):
    initial = frame(runner, APScreen("hard_list", area=5))
    visible = frame(runner, APScreen("hard_list", area=5, left=True, right=True))
    observations = iter([initial, visible])
    runner.capture = lambda: next(observations)
    pauses, taps = [], []
    runner.sleep = pauses.append
    runner.tap = lambda *args: taps.append(args)
    destination = frame(runner, APScreen("hard_list", area=5 + (1 if right else -1)))

    def wait(kind, *, predicate):
        assert kind == "hard_list" and predicate(destination.screen)
        assert not predicate(visible.screen)
        return destination

    runner.wait = wait
    assert runner.area_step(initial, right) is destination
    assert pauses == [0.7, 0.7]
    assert len(taps) == 1
    assert taps[0][0] is visible
    assert taps[0][1] == (1240 if right else 42, 358)


def test_area_step_visible_arrow_needs_no_extra_capture(runner):
    initial = frame(runner, APScreen("hard_list", area=5, left=True))
    runner.capture = lambda: pytest.fail("visible arrow needs no recheck")
    runner.sleep = lambda _: pytest.fail("no arrow settling delay needed")
    taps = []
    runner.tap = lambda *args: taps.append(args)
    runner.wait = lambda *args, **kwargs: "destination"
    assert runner.area_step(initial, False) == "destination"
    assert taps[0][0] is initial


def test_area_step_persistent_missing_arrow_still_fails_without_input(runner):
    initial = frame(runner, APScreen("hard_list", area=14))
    captures = []
    runner.capture = lambda: captures.append(1) or initial
    runner.tap = lambda *args: pytest.fail("cannot tap an unobserved arrow")
    with pytest.raises(TaskError, match="not reachable"):
        runner.area_step(initial, True)
    assert len(captures) == 2


@pytest.mark.parametrize("screen", [APScreen("hard_list", area=4, left=True),
                                    APScreen("normal", area=5, left=True),
                                    APScreen("unknown")])
def test_area_step_cannot_reuse_target_after_area_or_screen_changes(runner, screen):
    initial = frame(runner, APScreen("hard_list", area=5))
    runner.capture = lambda: frame(runner, screen)
    runner.tap = lambda *args: pytest.fail("changed page must never get input")
    with pytest.raises(TaskError, match="area changed"):
        runner.area_step(initial, False)


@pytest.mark.parametrize("fault", ["stale", "foreground"])
def test_area_step_arrow_recheck_retains_input_guards(runner, fault):
    initial = frame(runner, APScreen("hard_list", area=5))
    visible = frame(runner, APScreen("hard_list", area=5, left=True))

    def capture():
        if fault == "stale":
            runner.clock = lambda: 6
        return visible

    runner.capture = capture
    runner.device = SimpleNamespace(
        foreground_package=lambda: (
            "com.android.settings" if fault == "foreground" else runner.config.package
        ),
        tap=lambda *args, **kwargs: pytest.fail("unsafe input must not reach ADB"),
    )
    with pytest.raises(TaskError, match="fresh recognized|Foreground changed"):
        runner.area_step(initial, False)


def test_area_step_does_not_retry_past_the_first_area(runner):
    runner.capture = lambda: pytest.fail("area zero cannot exist")
    runner.tap = lambda *args: pytest.fail("cannot navigate past the first area")
    with pytest.raises(TaskError, match="not reachable"):
        runner.area_step(frame(runner, APScreen("hard_list", area=1)), False)


def detail(**kw):
    values = dict(
        kind="detail",
        ap=160,
        strategy="elephs",
        stage="13-3",
        count=1,
        cost=20,
        after=140,
        remaining=3,
        stars=3,
        target=(937, 437),
    )
    values.update(kw)
    return APScreen(**values)


@pytest.mark.parametrize("limit", ["time", "inputs"])
def test_hard_visit_yields_after_verified_sweep_and_resumes_next_stage(runner, limit):
    from datetime import datetime, timezone, timedelta
    from ba_automator.ap_vision import APStage
    from ba_automator.spend_ap import (
        SWEEP_INPUTS_RESERVE, SWEEP_SECONDS_RESERVE, VISIT_INPUTS, VISIT_SECONDS,
    )

    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    runner.state.update(hard_stages=["13-3", "13-2"], surveyed_at=now.isoformat(),
                        next_stage="13-3")
    runner.save()
    paid_stages = []

    def visit(r, stage, ap, current_time):
        elapsed = [0]
        r.clock = lambda: elapsed[0]
        r.started = 0
        r.wall_clock = lambda: current_time
        listing = APScreen("hard_list", area=13, stages=(
            APStage("13-3", 3, (100, 200), remaining=3),
            APStage("13-2", 3, (100, 300), remaining=3),
        ))
        screens = iter([
            APScreen("home", ap=ap), detail(stage=stage, ap=ap, after=ap-20),
            APScreen("confirm", ap=ap, cost=20, count=1, target=(767, 505)),
            APScreen("receipt", ap=ap-20, count=1, target=(640, 583)),
            detail(stage=stage, ap=ap-20, after=ap-40, remaining=2), listing,
            *[APScreen("home", ap=ap-20) for _ in range(3)],
        ])
        observed = []

        def wait(kinds, *, predicate=lambda s: True, **kwargs):
            screen = next(screens)
            assert screen.kind in ({kinds} if isinstance(kinds, str) else kinds)
            assert predicate(screen)
            observed.append(screen.kind)
            return frame(r, screen)

        def tap(f, target, description):
            r.budget()
            if f.screen.kind == "confirm":
                pending = read_state(r.config)["pending"]
                assert pending["stage"] == stage and pending["count"] == 1
                paid_stages.append(stage)
            r.actions += 1

        r.wait, r.tap = wait, tap
        r.enter_hard = lambda: frame(r, listing)
        r.hard_area = lambda f, requested: f
        actual_sweep = r.sweep

        def sweep(*args, **kwargs):
            result = actual_sweep(*args, **kwargs)
            # The previous receipt used the remaining visit budget. The next
            # stage must be left unpaid, with enough room to verify Home.
            if limit == "time":
                elapsed[0] = VISIT_SECONDS - SWEEP_SECONDS_RESERVE
            else:
                r.actions = VISIT_INPUTS - SWEEP_INPUTS_RESERVE
            return result

        r.sweep = sweep
        result = r.run()
        assert result.status == "success"
        assert observed[-3:] == ["home"] * 3
        saved = read_state(r.config)
        assert saved["pending"] is None and saved["blocked_reason"] is None
        assert saved["last_ap"] == ap - 20
        assert datetime.fromisoformat(saved["next_check_at"]) == current_time + timedelta(minutes=1)
        assert "continuing in one minute" in saved["last_summary"]
        assert "ap_visit_yielded" in (r.config.state_dir / "important-actions.jsonl").read_text()
        return saved

    assert visit(runner, "13-3", 180, now)["next_stage"] == "13-2"
    following = APRunner(runner.config, None, None, vision=object(),
                         monotonic=lambda: 0, sleep=lambda _: None)
    try:
        assert visit(following, "13-2", 160, now + timedelta(minutes=1))["next_stage"] == "13-3"
    finally:
        following.journal.close()
    assert paid_stages == ["13-3", "13-2"]


@pytest.mark.parametrize("limit", ["time", "inputs"])
def test_hard_navigation_consuming_reserve_yields_before_spending(runner, limit):
    from datetime import datetime, timezone, timedelta
    from ba_automator.ap_vision import APStage
    from ba_automator.spend_ap import (
        SWEEP_INPUTS_RESERVE, SWEEP_SECONDS_RESERVE, VISIT_INPUTS, VISIT_SECONDS,
    )

    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    runner.wall_clock = lambda: now
    elapsed = [0]
    runner.clock = lambda: elapsed[0]
    runner.state.update(hard_stages=["13-3"], surveyed_at=now.isoformat(), next_stage="13-3")
    runner.save()
    listing = APScreen("hard_list", area=13, stages=(APStage("13-3", 3, (100, 200), remaining=3),))
    screens = iter([APScreen("home", ap=160), detail(), listing,
                    *[APScreen("home", ap=160) for _ in range(3)]])
    taps = []

    def wait(kinds, *, predicate=lambda s: True, **kwargs):
        screen = next(screens)
        assert screen.kind in ({kinds} if isinstance(kinds, str) else kinds)
        assert predicate(screen)
        if screen.kind == "detail":
            if limit == "time":
                elapsed[0] = VISIT_SECONDS - SWEEP_SECONDS_RESERVE
            else:
                runner.actions = VISIT_INPUTS - SWEEP_INPUTS_RESERVE
        return frame(runner, screen)

    def tap(f, target, description):
        runner.budget()
        taps.append(description)
        runner.actions += 1

    runner.wait, runner.tap = wait, tap
    runner.enter_hard = lambda: frame(runner, listing)
    runner.hard_area = lambda f, stage: f
    runner.sweep = lambda *args, **kwargs: pytest.fail("must reserve receipt time before spending")
    assert runner.run().status == "success"
    saved = read_state(runner.config)
    assert taps == ["Inspect Hard 13-3", "Close mission info", "Return home"]
    assert saved["next_stage"] == "13-3" and saved["last_ap"] == 160
    assert saved["pending"] is None and saved["blocked_reason"] is None
    assert datetime.fromisoformat(saved["next_check_at"]) == now + timedelta(minutes=1)


@pytest.mark.parametrize(
    "screen,count",
    [
        (detail(ap=119, after=99), 1),
        (detail(stars=2), 1),
        (detail(remaining=0), 1),
        (detail(cost=None), 1),
        (detail(target=None), 1),
        (detail(), 4),
    ],
)
def test_no_input_when_stage_or_floor_does_not_allow_spend(runner, screen, count):
    runner.tap = lambda *args: pytest.fail("must not tap")
    with pytest.raises(TaskError):
        runner.sweep(frame(runner, screen), count)


@pytest.mark.parametrize(
    "confirm",
    [
        APScreen("confirm", ap=160, cost=40, count=2),
        APScreen("confirm", ap=159, cost=20, count=1),
        APScreen("confirm", ap=160, cost=21, count=1),
    ],
)
def test_changed_confirmation_never_spends(runner, confirm):
    taps = []
    runner.tap = lambda *args: taps.append(args)
    runner.wait = lambda *args, **kwargs: frame(runner, confirm)
    with pytest.raises(TaskError, match="confirmation"):
        runner.sweep(frame(runner, detail()), 1)
    assert len(taps) == 1 and read_state(runner.config)["pending"] is None


def test_confirmed_sweep_persists_cursor_only_after_receipt_and_attempt_count(runner):
    screens = iter(
        [
            APScreen("confirm", ap=160, cost=20, count=1, target=(767, 505)),
            APScreen("receipt", ap=140, count=1, target=(640, 583)),
            detail(ap=140, after=120, remaining=2),
        ]
    )
    runner.wait = lambda *args, **kwargs: frame(runner, next(screens))
    inputs = []

    def tap(f, *args):
        if f.screen.kind == "confirm":
            state = read_state(runner.config)
            assert state["pending"]["cost"] == 20 and state["next_stage"] is None
        inputs.append(f.screen.kind)

    runner.tap = tap
    result = runner.sweep(frame(runner, detail()), 1, next_stage="13-2")
    assert result.screen.ap == 140 and inputs == ["detail", "confirm", "receipt"]
    saved = read_state(runner.config)
    assert saved["pending"] is None and saved["next_stage"] == "13-2"
    assert (
        "ap_spent" in (runner.config.state_dir / "important-actions.jsonl").read_text()
    )


def test_missing_receipt_blocks_replay_and_retains_old_cursor(runner):
    runner.tap = lambda *args: None
    calls = []

    def wait(*args, **kwargs):
        if not calls:
            calls.append(1)
            return frame(
                runner, APScreen("confirm", ap=160, cost=20, count=1, target=(767, 505))
            )
        runner.fail("receipt missing")

    runner.wait = wait
    with pytest.raises(TaskError, match="receipt"):
        runner.sweep(frame(runner, detail()), 1, next_stage="13-2")
    assert (
        read_state(runner.config)["pending"]
        and read_state(runner.config)["next_stage"] is None
    )
    runner.allow_retry = True
    runner.wait = lambda *args, **kwargs: pytest.fail("must not navigate or repeat")
    with pytest.raises(TaskError, match="unresolved"):
        runner.run()


def test_jobs_share_queue_plans_and_only_scan_has_no_spend(runner):
    c = runner.config
    assert task_plan("scan_ap", c) == ("restart", "scan_ap", "red_dots")
    assert task_plan("spend_ap", c) == ("restart", "spend_ap", "red_dots")
    assert "spend_ap" not in task_plan("cafe", c)
    enabled = replace(c, ap_schedule_enabled=True)
    for task in ("cafe", "mail", "packs"):
        assert task_plan(task, enabled)[-2:] == ("spend_ap", "red_dots")
    assert task_plan("daily", enabled)[-3:] == ("spend_ap", "tasks", "red_dots")
    assert task_plan("scan_ap", enabled) == ("restart", "scan_ap", "red_dots")


def test_commission_survey_rejects_a_gap_instead_of_guessing_highest(runner):
    from ba_automator.ap_vision import APStage

    a = APScreen(
        "commission_list", strategy="reports", stages=(APStage("A", 3, (1, 1)),)
    )
    c = APScreen(
        "commission_list", strategy="reports", stages=(APStage("C", 3, (1, 1)),)
    )
    runner.enter_commission = lambda _: frame(runner, a)
    runner.commission_top = lambda f: f
    runner.swipe = lambda *args, **kwargs: None
    runner.wait = lambda *args, **kwargs: frame(runner, c)
    runner.go_home = lambda *args: pytest.fail("a partial survey must fail")
    with pytest.raises(TaskError, match="missed a stage"):
        runner.survey_commission("reports")
    assert read_state(runner.config)["commissions"] == {}


def test_one_ignored_scroll_is_not_the_end_of_the_commission_list(runner):
    from ba_automator.ap_vision import APStage

    def page(letters):
        return frame(
            runner,
            APScreen(
                "commission_list",
                strategy="reports",
                stages=tuple(
                    APStage(s, 3 if s <= "J" else 0, (1, 1) if s <= "K" else None)
                    for s in letters
                ),
            ),
        )

    first = page("ABCDE")
    middle = page("EFGHI")
    last = page("JKLMN")
    screens = iter([first, middle, last, last, last])
    runner.enter_commission = lambda _: first
    runner.commission_top = lambda f: f
    runner.swipe = lambda *args, **kwargs: None
    runner.wait = lambda *args, **kwargs: next(screens)
    runner.go_home = lambda *args: None
    assert runner.survey_commission("reports") == "J"


def test_commission_survey_cannot_downgrade_a_known_clear(runner):
    from ba_automator.ap_vision import APStage

    first = frame(
        runner,
        APScreen(
            "commission_list", strategy="reports", stages=(APStage("A", 3, (1, 1)),)
        ),
    )
    runner.state["commissions"]["reports"] = "J"
    runner.enter_commission = lambda _: first
    runner.commission_top = lambda f: f
    runner.swipe = lambda *args, **kwargs: None
    runner.wait = lambda *args, **kwargs: first
    with pytest.raises(TaskError, match="previously verified"):
        runner.survey_commission("reports")


def test_commission_batch_verifies_total_cost_without_moving_hard_cursor(runner):
    original = APScreen(
        "detail",
        ap=210,
        strategy="reports",
        stage="J",
        count=2,
        cost=40,
        after=130,
        stars=3,
        target=(937, 405),
    )
    result = replace(original, ap=130, after=50)
    screens = iter(
        [
            APScreen("confirm", ap=210, cost=80, count=2, target=(767, 505)),
            APScreen("receipt", ap=130, count=2, target=(640, 583)),
            result,
        ]
    )
    runner.state["next_stage"] = "4-2"
    runner.wait = lambda *args, **kwargs: frame(runner, next(screens))

    def tap(f, *args):
        if f.screen.kind == "confirm":
            intent = read_state(runner.config)["pending"]
            assert (
                intent["count"] == 2 and intent["cost"] == 40 and intent["floor"] == 100
            )

    runner.tap = tap
    assert runner.sweep(frame(runner, original), 2).screen.ap == 130
    state = read_state(runner.config)
    assert state["pending"] is None and state["next_stage"] == "4-2"
