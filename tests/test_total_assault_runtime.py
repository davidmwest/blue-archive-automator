"""Ticket boundary and prerequisite regressions using a scripted device flow."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from ba_automator import assault_state
from ba_automator.assault_policy import AssaultContext, MockResult, TeamMember
from ba_automator.assault_vision import AssaultScreen, AssaultStage
from ba_automator.config import Config
from ba_automator.runtime import TaskError
from ba_automator.total_assault import TotalAssaultRunner


NOW = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)
CONTEXT = AssaultContext("09/21 19:00 – 09/28 11:59", "Drumbarka", "hardcore", "2026-09-24")
TEAM = tuple(TeamMember(f"Student {index}", index, "striker" if index < 4 else "special",
                        "mystic", 5, 90) for index in range(6))
WIN = MockResult(True, 60, None, {member.student_id: 100 for member in TEAM})


def frame(kind, **kwargs):
    return SimpleNamespace(screen=AssaultScreen(kind, **kwargs),
                           capture=SimpleNamespace(is_fresh=lambda _: True, png=b""))


@pytest.fixture
def runner(tmp_path):
    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    run_dir=tmp_path / "runs", state_dir=tmp_path / "state",
                    lock_dir=tmp_path / "locks")
    value = TotalAssaultRunner(config, object(), object(), vision=object(),
                               wall_clock=lambda: NOW)
    value.context = CONTEXT
    value.important = lambda *args, **kwargs: None
    value.verify_real_owned = lambda formation, team: formation
    yield value
    value.journal.close()


def room(tickets=6):
    return frame("detail", boss=CONTEXT.boss, difficulty=CONTEXT.difficulty,
                 tickets=tickets, enter_target=(1010, 545))


def qualify(runner):
    assault_state.record_mock(runner.config, CONTEXT, TEAM, WIN, runner.run_dir.name, now=NOW)


def test_real_input_requires_fresh_mock_in_this_run(runner):
    sent = []
    runner.tap = lambda *args: sent.append(args)
    with pytest.raises(assault_state.AssaultStateError, match="fresh successful mock"):
        runner.real_clear(room(), TEAM)
    assert sent == []


def test_entry_intent_is_durable_before_device_input_and_not_replayed(runner):
    qualify(runner)
    calls = []

    def crash_on_input(*args):
        pending = assault_state.read_state(runner.config)["pending"]
        assert pending["kind"] == "entry" and pending["tickets_before"] == 6
        calls.append(args)
        raise RuntimeError("device disconnected after tap")

    runner.tap = crash_on_input
    with pytest.raises(RuntimeError, match="disconnected"):
        runner.real_clear(room(), TEAM)
    with pytest.raises(assault_state.AssaultStateError, match="unresolved"):
        runner.real_clear(room(), TEAM)
    assert len(calls) == 1


def test_formation_change_after_real_entry_prevents_mobilize(runner):
    qualify(runner)
    changed = (replace(TEAM[0], level=89), *TEAM[1:])
    inputs = []
    runner.tap = lambda _, target, __: inputs.append(target)
    runner.wait = lambda *args, **kwargs: frame("formation", team=changed)
    runner.fight = lambda *args, **kwargs: pytest.fail("Changed team must not mobilize")
    with pytest.raises(TaskError, match="differs from the mock"):
        runner.real_clear(room(), TEAM)
    assert inputs == [(1010, 545)]
    assert assault_state.read_state(runner.config)["pending"]["kind"] == "entry"


def test_real_win_also_requires_ticket_receipt(runner):
    qualify(runner)
    runner.tap = lambda *args: None
    runner.wait = lambda *args, **kwargs: frame("formation", team=TEAM)
    runner.fight = lambda *args, **kwargs: (WIN, frame("result"))
    runner.dismiss_result = lambda _: frame("menu", tickets=6, boss=CONTEXT.boss, event_period=CONTEXT.event_id)
    with pytest.raises(assault_state.AssaultStateError, match="decrement is uncertain"):
        runner.real_clear(room(), TEAM)
    state = assault_state.read_state(runner.config)
    assert state["pending"] and state["clear"] is None


def test_real_win_and_one_ticket_decrement_unlock_sweep(runner):
    qualify(runner)
    runner.tap = lambda *args: None
    runner.wait = lambda *args, **kwargs: frame("formation", team=TEAM)
    runner.fight = lambda *args, **kwargs: (WIN, frame("result"))
    runner.dismiss_result = lambda _: frame("menu", tickets=5, boss=CONTEXT.boss, event_period=CONTEXT.event_id)
    result = runner.real_clear(room(), TEAM)
    assert result.screen.tickets == 5
    state = assault_state.read_state(runner.config)
    assert state["pending"] is None and state["clear"]["tickets_after"] == 5


def test_real_assistant_is_restored_and_verified_before_mobilizing(runner):
    borrowed = TEAM[:1] + (replace(TEAM[1], assistant=True, assistant_id="mock-qualified-lender"),) + TEAM[2:]
    assault_state.record_mock(runner.config, CONTEXT, borrowed, WIN, runner.run_dir.name, now=NOW)
    # Real entry clears the borrowed slot, so the ordinary formation parser
    # cannot return a complete team until the exact offering is restored.
    empty_formation = frame("formation", team=())
    restored_formation = frame("formation", team=TEAM)
    events = []
    runner.tap = lambda *args: events.append("enter")
    runner.wait = lambda *args, **kwargs: empty_formation

    def verify(current, expected):
        assert current is empty_formation and expected == borrowed
        state = assault_state.read_state(runner.config)
        assert state["pending"]["kind"] == "entry" and state["proof"] is None
        events.append("verify exact lender")
        return restored_formation

    def fight(current, expected, *, mock):
        assert current is restored_formation and expected == borrowed and mock is False
        events.append("mobilize")
        return WIN, frame("result")

    runner.verify_real_assistant = verify
    runner.fight = fight
    runner.dismiss_result = lambda _: frame("menu", tickets=5, boss=CONTEXT.boss, event_period=CONTEXT.event_id)
    assert runner.real_clear(room(), borrowed).screen.tickets == 5
    assert events == ["enter", "verify exact lender", "mobilize"]
    assert assault_state.read_state(runner.config)["clear"]["tickets_after"] == 5


@pytest.mark.parametrize("verification", ["wrong lender", "changed team"])
def test_real_assistant_rejection_holds_intent_and_never_mobilizes_or_reenters(runner, verification):
    borrowed = TEAM[:1] + (replace(TEAM[1], assistant=True, assistant_id="mock-qualified-lender"),) + TEAM[2:]
    assault_state.record_mock(runner.config, CONTEXT, borrowed, WIN, runner.run_dir.name, now=NOW)
    events = []
    runner.tap = lambda *args: events.append("enter")
    runner.wait = lambda *args, **kwargs: frame("formation", team=TEAM)

    def verify(current, expected):
        assert expected[1].assistant_id == "mock-qualified-lender"
        events.append("verify exact lender")
        if verification == "wrong lender":
            runner.fail("The exact mock-tested assistant cannot be verified; the ticket intent is held")
        # An assistant verifier cannot bypass the runner's complete-team check.
        return frame("formation", team=(replace(TEAM[0], level=89), *TEAM[1:]))

    runner.verify_real_assistant = verify
    runner.fight = lambda *args, **kwargs: pytest.fail("An unverified assistant must never mobilize")
    runner.dismiss_result = lambda *args: pytest.fail("No battle result should be reconciled")
    with pytest.raises(TaskError, match="ticket intent is held"):
        runner.real_clear(room(), borrowed)
    state = assault_state.read_state(runner.config)
    assert state["pending"]["kind"] == "entry" and state["proof"] is None and state["clear"] is None
    with pytest.raises(assault_state.AssaultStateError, match="unresolved"):
        runner.real_clear(room(), borrowed)
    assert events == ["enter", "verify exact lender"]


def test_auto_formation_with_leftover_assistant_cannot_start_a_mock(runner):
    detail = replace(room().screen, mock_target=(803, 545))
    formation = frame("formation", quick_target=(1205, 182))
    quick = frame("quick", auto_target=(623, 593), confirm_target=(1168, 593))
    observations = iter((formation, quick, quick))
    sent = []
    runner.tap = lambda _, target, __: sent.append(target)
    runner.wait = lambda *args, **kwargs: next(observations)
    runner.verify_owned_quick = lambda _: runner.fail("Auto formation contains an assistant or unreadable slot")
    runner.fight = lambda *args, **kwargs: pytest.fail("An unverified Auto team cannot start a mock")
    with pytest.raises(TaskError, match="contains an assistant"):
        runner.qualify(SimpleNamespace(screen=detail))
    assert sent == [(803, 545), (1205, 182), (623, 593)]
    state = assault_state.read_state(runner.config)
    assert state["proof"] is None and state["pending"] is None


def test_real_owned_provenance_check_runs_before_mobilize_and_uses_rechecked_frame(runner):
    qualify(runner)
    initial, verified = frame("formation", team=TEAM), frame("formation", team=TEAM)
    runner.tap = lambda *args: None
    runner.wait = lambda *args, **kwargs: initial
    events = []

    def verify(current, expected):
        assert current is initial and expected == TEAM
        assert assault_state.read_state(runner.config)["pending"]["kind"] == "entry"
        events.append("check owned")
        return verified

    def fight(current, expected, *, mock):
        assert current is verified and expected == TEAM and mock is False
        events.append("fight")
        return WIN, frame("result")

    runner.verify_real_owned = verify
    runner.fight = fight
    runner.dismiss_result = lambda _: frame("menu", tickets=5, boss=CONTEXT.boss, event_period=CONTEXT.event_id)
    runner.real_clear(room(), TEAM)
    assert events == ["check owned", "fight"]


def test_real_owned_team_with_borrowed_badge_holds_intent_even_if_metadata_matches(runner):
    qualify(runner)
    sent = []
    runner.tap = lambda *args: sent.append(args)
    runner.wait = lambda *args, **kwargs: frame("formation", team=TEAM)
    runner.verify_real_owned = lambda *args: runner.fail("Real owned formation has an assistant badge")
    runner.fight = lambda *args, **kwargs: pytest.fail("Matching stats do not prove owned provenance")
    with pytest.raises(TaskError, match="assistant badge"):
        runner.real_clear(room(), TEAM)
    assert len(sent) == 1
    state = assault_state.read_state(runner.config)
    assert state["pending"] and state["proof"] is None and state["clear"] is None


def test_mock_failure_requires_second_assistant_mock_before_any_real_entry(runner):
    failed = replace(WIN, remaining_seconds=1)
    events = []
    runner.auto_team = lambda _: (frame("formation"), TEAM)
    runner.choose_assistant_team = lambda *args: (frame("formation"), TEAM)

    def fight(*args, **kwargs):
        events.append(kwargs["mock"])
        return failed, frame("result")

    runner.fight = fight
    runner.dismiss_result = lambda _: room()
    with pytest.raises(TaskError, match="assistant mock did not win comfortably"):
        runner.qualify(room())
    assert events == [True, True]
    state = assault_state.read_state(runner.config)
    assert state["proof"] is None and state["pending"] is None


def test_prerequisite_clear_is_resurveyed_before_target_then_swept(runner):
    visited = []
    surveys = iter([{"normal": False, "hard": True, "very_hard": True, "hardcore": True},
                    {"normal": False, "hard": False, "very_hard": False, "hardcore": False}])
    menu = frame("menu", tickets=6)
    runner.stage_menu = lambda: menu
    runner.survey = lambda _: ({key: AssaultStage(key, "Drumbarka", (1, 1), locked)
                               for key, locked in next(surveys).items()}, menu)

    def select(_, difficulty):
        visited.append(("select", difficulty))
        runner.context = replace(CONTEXT, difficulty=difficulty)
        return room()

    runner.select_stage = select
    runner.qualify = lambda detail: (detail, TEAM)
    runner.real_clear = lambda *args: menu
    runner.sweep_remaining = lambda value: visited.append(("sweep", runner.context.difficulty)) or value
    runner.tap = lambda *args: None
    runner.home = lambda: None
    runner.run()
    assert visited == [("select", "normal"), ("select", "hardcore"), ("sweep", "hardcore")]


def test_no_tickets_returns_home_without_mock_or_entry(runner):
    runner.stage_menu = lambda: frame("menu", tickets=0)
    runner.survey = lambda _: pytest.fail("No-ticket visit should not start planning")
    runner.tap = lambda *args: None
    runner.home = lambda: None
    assert runner.run().status == "success"


@pytest.mark.parametrize("boss,event", [("Other Boss", CONTEXT.event_id),
                                         (CONTEXT.boss, "new season")])
def test_event_change_after_real_win_holds_ticket_intent(runner, boss, event):
    qualify(runner)
    runner.tap = lambda *args: None
    runner.wait = lambda *args, **kwargs: frame("formation", team=TEAM)
    runner.fight = lambda *args, **kwargs: (WIN, frame("result"))
    runner.dismiss_result = lambda _: frame("menu", tickets=5, boss=boss, event_period=event)
    with pytest.raises(TaskError, match="event or game day changed"):
        runner.real_clear(room(), TEAM)
    state = assault_state.read_state(runner.config)
    assert state["pending"] and state["clear"] is None


def test_receipt_collector_inspects_the_supplied_first_frame_before_waiting(runner, monkeypatch):
    """The receipt already observed by a sweep must not be lost to a new wait."""
    receipt = frame("receipt", target=(620, 660),
                    items=({"name": "Total Assault Coin", "quantity": 100},))
    inspected = frame("receipt", target=(620, 660), items=receipt.screen.items)
    returned = frame("menu", tickets=5)
    events = []
    runner.journal.save_image = lambda name, png: events.append(("save", name))

    def inspect(current_runner, current, evidence):
        assert current_runner is runner and current is receipt
        assert events and events[0][0] == "save"
        events.append(("inspect", evidence))
        return inspected

    def tap(current, target, label):
        assert current is inspected and target == (620, 660)
        events.append(("dismiss", target))

    def wait(kinds, **kwargs):
        assert [entry[0] for entry in events] == ["save", "inspect", "dismiss"]
        assert "receipt" not in kinds
        events.append(("wait", kinds))
        return returned

    monkeypatch.setattr("ba_automator.total_assault.inspect_receipt", inspect)
    runner.tap, runner.wait = tap, wait
    assert runner.collect_receipts(receipt) is returned
    assert [entry[0] for entry in events] == ["save", "inspect", "dismiss", "wait"]


def test_receipt_that_stays_open_is_not_counted_or_dismissed_twice(runner, monkeypatch):
    receipt = frame("receipt", target=(620, 660),
                    items=({"name": "Credits", "quantity": 10_000},))
    inspected, sent, rewards = [], [], []

    def inspect(current_runner, current, evidence):
        # In production this call writes loot_received. A second call with a
        # new evidence path would count the same reward a second time.
        inspected.append(evidence)
        return current

    def stuck_wait(kinds, **kwargs):
        assert "receipt" not in kinds
        raise TaskError("receipt remained open until the bounded wait expired", runner.run_dir)

    monkeypatch.setattr("ba_automator.total_assault.inspect_receipt", inspect)
    runner.journal.save_image = lambda *args: None
    runner.important = lambda event, *args, **kwargs: rewards.append(event)
    runner.tap = lambda *args: sent.append(args)
    runner.wait = stuck_wait
    with pytest.raises(TaskError, match="bounded wait expired"):
        runner.collect_receipts(receipt)
    assert len(inspected) == len(sent) == 1
    assert rewards == ["total_assault_rewards_received"]


def test_sweep_action_uses_inspected_loot_names_when_classifier_has_none(runner, monkeypatch):
    receipt = frame("receipt", target=(640, 583), items=())
    items = [{"name": "Total Assault Coin", "quantity": 500},
             {"name": "Advanced Total Assault Coin", "quantity": 50}]
    actions = []

    def inspect(current_runner, current, evidence):
        evidence.with_suffix(".loot.json").write_text(json.dumps({"items": items}))
        return current

    monkeypatch.setattr("ba_automator.total_assault.inspect_receipt", inspect)
    runner.important = lambda kind, detail, **fields: actions.append((kind, detail, fields))
    runner.tap = lambda *args: None
    runner.wait = lambda *args, **kwargs: room(tickets=0)
    runner.collect_receipts(receipt)
    assert actions[0][1] == "Total Assault rewards: +500 Total Assault Coin, +50 Advanced Total Assault Coin"
    assert actions[0][2]["items"] == items


def test_distinct_outcome_notices_are_closed_once_before_the_reward_receipt(runner, monkeypatch):
    outcome = frame("outcome", confirm_target=(1000, 647))
    record = frame("season_record", confirm_target=(1040, 646))
    receipt = frame("receipt", target=(620, 660),
                    items=({"name": "Total Assault Coin", "quantity": 100},))
    menu = frame("menu", tickets=5)
    observations = iter((record, receipt, menu))
    closed, inspected, notices = [], [], []
    runner.journal.save_image = lambda *args: None
    runner.journal.record = lambda event, **details: notices.append((event, details))
    runner.tap = lambda current, *args: closed.append(current.screen.kind)

    def wait(kinds, **kwargs):
        observed = next(observations)
        assert observed.screen.kind in kinds
        assert closed[-1] not in kinds
        return observed

    def inspect(current_runner, current, evidence):
        inspected.append(current)
        return current

    monkeypatch.setattr("ba_automator.total_assault.inspect_receipt", inspect)
    runner.wait = wait
    assert runner.collect_receipts(outcome) is menu
    assert closed == ["outcome", "season_record", "receipt"]
    assert inspected == [receipt]
    assert [details["kind"] for event, details in notices
            if event == "total_assault_outcome_notice"] == ["outcome", "season_record"]


def test_reappearing_outcome_notice_is_not_acknowledged_again(runner, monkeypatch):
    outcome = frame("outcome", confirm_target=(1000, 647))
    record = frame("season_record", confirm_target=(1040, 646))
    observations = iter((record, outcome))
    closed = []
    runner.journal.save_image = lambda *args: None
    runner.tap = lambda current, *args: closed.append(current.screen.kind)
    runner.wait = lambda *args, **kwargs: next(observations)
    monkeypatch.setattr("ba_automator.total_assault.inspect_receipt",
                        lambda *args: pytest.fail("An outcome notice is not a reward receipt"))
    with pytest.raises(TaskError, match="repeated an outcome notice"):
        runner.collect_receipts(outcome)
    assert closed == ["outcome", "season_record"]


def record_real_clear(runner):
    qualify(runner)
    entry = assault_state.begin_entry(runner.config, CONTEXT, TEAM, runner.run_dir.name, 6, now=NOW)
    assault_state.complete_entry(runner.config, entry, tickets_after=5, won=True, now=NOW)


def sweep_intent(runner, count=5):
    record_real_clear(runner)
    return assault_state.begin_sweep(runner.config, CONTEXT, runner.run_dir.name, 5, count, now=NOW)


def sweep_notice(*, count=5, tickets=5, after_tickets=0, **kwargs):
    fields = dict(count=count, tickets=tickets, after_tickets=after_tickets,
                  difficulty=CONTEXT.difficulty, confirm_target=(811, 509))
    fields.update(kwargs)
    return frame("sweep_confirm", **fields)


def test_sweep_confirmation_matches_durable_budget_and_cannot_be_tapped_twice(runner):
    intent = sweep_intent(runner)
    notice = sweep_notice()
    before = assault_state.read_state(runner.config)
    sent = []
    runner.journal.save_image = lambda *args: None

    def tap(current, target, detail):
        assert current is notice and target == (811, 509)
        assert assault_state.read_state(runner.config) == before
        sent.append(target)

    runner.tap = tap
    runner.confirm_sweep(notice, intent)
    with pytest.raises(TaskError, match="already confirmed"):
        runner.confirm_sweep(notice, intent)
    assert len(sent) == 1
    # Sending a confirmation is not proof that rewards arrived or tickets fell.
    assert assault_state.read_state(runner.config) == before


@pytest.mark.parametrize("changed", [
    {"count": 4}, {"count": None}, {"tickets": 6}, {"tickets": None},
    {"after_tickets": 1}, {"after_tickets": None}, {"difficulty": "extreme"},
    {"difficulty": None}, {"confirm_target": None},
])
def test_mismatched_sweep_notice_preserves_pending_intent_without_input(runner, changed):
    intent = sweep_intent(runner)
    before = assault_state.read_state(runner.config)
    runner.tap = lambda *args: pytest.fail("An unverified sweep notice must not spend tickets")
    with pytest.raises(TaskError, match="differs from the persisted ticket budget"):
        runner.confirm_sweep(sweep_notice(**changed), intent)
    assert assault_state.read_state(runner.config) == before
    assert not getattr(runner, "_assault_sweep_confirmed_intents", set())


@pytest.mark.parametrize("mismatch", ["intent", "run", "event", "boss", "difficulty", "day", "hold"])
def test_sweep_confirmation_is_bound_to_run_context_intent_and_unblocked_state(runner, mismatch):
    intent = sweep_intent(runner)
    if mismatch == "intent":
        intent = "not-the-persisted-intent"
    elif mismatch == "run":
        # Both records remain internally valid, but belong to another visit.
        state = assault_state.read_state(runner.config)
        state["pending"]["run_id"] = state["clear"]["run_id"] = "other-visit"
        assault_state.write_state(runner.config, state)
    elif mismatch == "event":
        runner.context = replace(CONTEXT, event_id="different observed event")
    elif mismatch == "boss":
        runner.context = replace(CONTEXT, boss="Other Boss")
    elif mismatch == "difficulty":
        runner.context = replace(CONTEXT, difficulty="extreme")
    elif mismatch == "day":
        runner.wall_clock = lambda: NOW.replace(day=26)
    elif mismatch == "hold":
        assault_state.block(runner.config, "The previous receipt needs inspection", now=NOW)
    before = assault_state.read_state(runner.config)
    runner.tap = lambda *args: pytest.fail("A mismatched or held sweep must not be confirmed")
    with pytest.raises(TaskError, match="differs from the persisted ticket budget"):
        runner.confirm_sweep(sweep_notice(), intent)
    assert assault_state.read_state(runner.config) == before


@pytest.mark.parametrize("pending_kind", [None, "entry"])
def test_sweep_confirmation_requires_a_sweep_intent(runner, pending_kind):
    intent = "missing-intent"
    if pending_kind == "entry":
        qualify(runner)
        intent = assault_state.begin_entry(runner.config, CONTEXT, TEAM, runner.run_dir.name, 6, now=NOW)
    before = assault_state.read_state(runner.config)
    runner.tap = lambda *args: pytest.fail("An entry or absent intent cannot authorize a sweep")
    with pytest.raises(TaskError, match="differs from the persisted ticket budget"):
        runner.confirm_sweep(sweep_notice(), intent)
    assert assault_state.read_state(runner.config) == before


def test_uncertain_sweep_confirmation_input_keeps_intent_and_latch(runner):
    intent = sweep_intent(runner)
    before = assault_state.read_state(runner.config)
    sent = []
    runner.journal.save_image = lambda *args: None

    def disconnected(*args):
        sent.append(args)
        raise RuntimeError("device disconnected after the confirmation tap")

    runner.tap = disconnected
    with pytest.raises(RuntimeError, match="disconnected"):
        runner.confirm_sweep(sweep_notice(), intent)
    with pytest.raises(TaskError, match="already confirmed"):
        runner.confirm_sweep(sweep_notice(), intent)
    assert len(sent) == 1
    assert assault_state.read_state(runner.config) == before


def test_sweep_latch_allows_a_distinct_reconciled_sweep_in_the_same_visit(runner):
    first = sweep_intent(runner, count=1)
    sent = []
    runner.journal.save_image = lambda *args: None
    runner.tap = lambda *args: sent.append(args)
    runner.confirm_sweep(sweep_notice(count=1, after_tickets=4), first)
    assault_state.complete_sweep(runner.config, first, tickets_after=4, rewards_verified=True, now=NOW)
    second = assault_state.begin_sweep(runner.config, CONTEXT, runner.run_dir.name, 4, 1, now=NOW)
    runner.confirm_sweep(sweep_notice(count=1, tickets=4, after_tickets=3), second)
    assert len(sent) == 2 and first != second
    assert assault_state.read_state(runner.config)["pending"]["id"] == second


def max_sweep_room():
    return frame("detail", boss=CONTEXT.boss, difficulty=CONTEXT.difficulty,
                 tickets=5, count=1, after_tickets=4,
                 sweep_target=(1030, 398), sweep_max_target=(993, 282))


def test_max_sweep_rechecks_all_five_tickets_then_persists_confirms_and_reconciles(runner):
    record_real_clear(runner)
    initial = max_sweep_room()
    maximum = SimpleNamespace(screen=replace(initial.screen, count=5, after_tickets=0),
                              capture=initial.capture)
    notice, receipt = sweep_notice(), frame("receipt", target=(620, 660))
    finished = frame("menu", tickets=0, boss=CONTEXT.boss, event_period=CONTEXT.event_id)
    expected_waits = iter((("detail", maximum), ({"receipt", "sweep_confirm"}, notice), ("receipt", receipt)))
    events = []
    runner.room_after_result = lambda _: initial
    runner.journal.save_image = lambda *args: None

    def wait(kind, *, predicate=lambda screen: True, **kwargs):
        expected_kind, observed = next(expected_waits)
        assert kind == expected_kind and predicate(observed.screen)
        if kind == "detail":
            assert assault_state.read_state(runner.config)["pending"] is None
        return observed

    def tap(current, target, detail):
        pending = assault_state.read_state(runner.config)["pending"]
        if current is initial:
            assert target == initial.screen.sweep_max_target and pending is None
            events.append("max")
        else:
            assert pending["kind"] == "sweep" and pending["count"] == pending["tickets_before"] == 5
            if current is maximum:
                assert target == maximum.screen.sweep_target
                events.append("start")
            else:
                assert current is notice and target == notice.screen.confirm_target
                assert pending["id"] in runner._assault_sweep_confirmed_intents
                events.append("confirm")

    def collect(current):
        assert current is receipt
        assert assault_state.read_state(runner.config)["pending"]["count"] == 5
        events.append("receipt")
        return finished

    runner.wait, runner.tap, runner.collect_receipts = wait, tap, collect
    assert runner.sweep_remaining(initial) is finished
    assert events == ["max", "start", "confirm", "receipt"]
    state = assault_state.read_state(runner.config)
    assert state["pending"] is None and state["clear"]["tickets_after"] == 0


@pytest.mark.parametrize("changed", [
    {"tickets": 6, "count": 6, "after_tickets": 0},
    {"tickets": 5, "count": 4, "after_tickets": 0},
    {"tickets": 5, "count": 5, "after_tickets": 1},
])
def test_max_sweep_never_persists_or_spends_until_exact_projection_is_verified(runner, changed):
    record_real_clear(runner)
    initial = max_sweep_room()
    unreadied = replace(initial.screen, **changed)
    before = assault_state.read_state(runner.config)
    runner.room_after_result = lambda _: initial
    taps = []
    runner.tap = lambda current, target, detail: taps.append(target)

    def wait(kind, *, predicate, **kwargs):
        assert kind == "detail" and predicate(unreadied) is False
        runner.fail("Max did not produce the verified ticket projection before the timeout")

    runner.wait = wait
    with pytest.raises(TaskError, match="verified ticket projection"):
        runner.sweep_remaining(initial)
    assert taps == [initial.screen.sweep_max_target]
    assert assault_state.read_state(runner.config) == before
