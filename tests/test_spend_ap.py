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
    assert task_plan("scan_ap", c) == ("restart", "scan_ap")
    assert task_plan("spend_ap", c) == ("restart", "spend_ap")
    assert "spend_ap" not in task_plan("cafe", c)
    enabled = replace(c, ap_schedule_enabled=True)
    for task in ("cafe", "mail", "packs", "daily"):
        assert task_plan(task, enabled)[-1] == "spend_ap"
    assert task_plan("scan_ap", enabled) == ("restart", "scan_ap")


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
