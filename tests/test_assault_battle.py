"""Combat driver guards: no ticket inputs, no guessed victory, no repeated Auto."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from ba_automator.assault_battle import AssaultBattleMixin
from ba_automator import assault_state
from ba_automator.assault_battle_vision import BattleScreen
from ba_automator.assault_policy import AssaultContext, MockResult, TeamMember, least_damage_striker
from ba_automator.assault_vision import AssaultScreen
from ba_automator.club import game_day
from ba_automator.config import Config
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame, ShopRunner


TEAM = tuple(TeamMember(name, slot, "striker" if slot < 4 else "special", "mystic", 3, 79)
             for slot, name in enumerate(("Aris (Maid)", "Kayoko (New Year)", "Koyuki",
                                          "Izuna (Swimsuit)", "Ako", "Yuzu (Armed)")))
FORMATION = AssaultScreen("formation", team=TEAM, target=(1177, 659))
RESULT = BattleScreen("result", won=True, elapsed_seconds=180, damage_target=(1055, 665), target=(1170, 665))
DAMAGE = BattleScreen("damage", target=(932, 122),
                      damage_by_student={member.student_id: member.slot * 500 for member in TEAM})


def battle(hp=1000, seconds=269, auto=True, **kwargs):
    return BattleScreen("battle", boss="Drumbarka", boss_hp=hp, boss_max_hp=2400000,
                        remaining_seconds=seconds, auto_on=auto,
                        auto_target=(1214, 677) if auto is False else None, **kwargs)


class Driver(AssaultBattleMixin, ShopRunner):
    task = "total_assault"

    def important(self, kind, detail, **fields):
        self.journal.record("important", kind=kind, detail=detail, **fields)

    def budget(self):
        pass


@pytest.fixture
def harness(tmp_path):
    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    run_dir=tmp_path / "runs", state_dir=tmp_path / "state", lock_dir=tmp_path / "locks")
    now = [0.0]
    screens = [AssaultScreen("unknown")]
    taps = []
    def sleep(seconds): now[0] += seconds
    def analyze(png, **kwargs): return screens.pop(0) if len(screens) > 1 else screens[0]
    def tap(x, y, *, deadline, monotonic):
        if now[0] > deadline:
            return False
        taps.append((x, y))
        return True
    device = SimpleNamespace(foreground_package=lambda: config.package, screenshot=lambda: b"frame", tap=tap)
    runner = Driver(config, device, None, vision=SimpleNamespace(analyze=analyze),
                    monotonic=lambda: now[0], sleep=sleep,
                    wall_clock=lambda: datetime(2026, 9, 25, tzinfo=timezone.utc) + timedelta(seconds=now[0]))
    runner.context = AssaultContext("09/21–09/28", "Drumbarka", "hardcore", "2026-09-25")
    def frame(screen): return ShopFrame(Capture(b"frame", now[0], config.package), screen)
    yield SimpleNamespace(runner=runner, screens=screens, now=now, taps=taps, frame=frame)
    runner.journal.close()


def test_observed_zero_hp_then_result_records_damage_and_leaves_result_open(harness):
    h = harness
    h.screens[:] = [battle(auto=False), battle(hp=0, seconds=90), RESULT, DAMAGE, RESULT]
    result, frame = h.runner.fight(h.frame(FORMATION), TEAM)
    assert result.won is True and result.remaining_seconds == 90
    assert result.damage_by_student == DAMAGE.damage_by_student
    assert result.surviving_striker_ids is None
    assert frame.screen.kind == "result"
    # No Enter, result Confirm, assistant purchase, or sweep input is sent here.
    assert h.taps == [(1177, 659), (1214, 677), (1055, 665), (932, 122)]
    events = [json.loads(line) for line in (h.runner.run_dir / "events.jsonl").read_text().splitlines()]
    assert any(event.get("boss_zero_observed") is True for event in events)
    assert (h.runner.run_dir / "mock-damage.png").exists()


def test_completion_without_boss_defeat_never_invents_victory(harness):
    h = harness
    unknown = replace(RESULT, won=None)
    h.screens[:] = [battle(), unknown, DAMAGE, unknown]
    with pytest.raises(TaskError, match="outcome was not verified"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert h.taps == [(1177, 659)]


def test_auto_is_not_toggled_repeatedly_while_waiting_for_acknowledgment(harness):
    h = harness
    h.screens[:] = [battle(auto=False), battle(auto=False), battle(auto=False),
                    battle(hp=0), RESULT, DAMAGE, RESULT]
    h.runner.fight(h.frame(FORMATION), TEAM)
    assert h.taps.count((1214, 677)) == 1


def test_unfamiliar_boss_timer_uses_only_an_observed_lower_bound(harness):
    h = harness
    h.runner.context = replace(h.runner.context, boss="Other Boss")
    h.screens[:] = [replace(battle(seconds=210), boss="Other Boss"),
                    replace(battle(hp=0, seconds=40), boss="Other Boss"), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.fight(h.frame(FORMATION), TEAM)
    assert result.remaining_seconds == 30  # 210 observed, minus 180 elapsed.


def test_close_win_has_no_invented_time_margin(harness):
    h = harness
    close = replace(RESULT, elapsed_seconds=269.667)
    h.screens[:] = [battle(hp=0), close, DAMAGE, close]
    result, _ = h.runner.fight(h.frame(FORMATION), TEAM)
    assert result.remaining_seconds == pytest.approx(.333)


def test_altered_team_stops_before_mobilize(harness):
    h = harness
    changed = (replace(TEAM[0], level=80), *TEAM[1:])
    with pytest.raises(TaskError, match="formation changed"):
        h.runner.fight(h.frame(replace(FORMATION, team=changed)), TEAM)
    assert h.taps == []


def test_assistant_lender_provenance_survives_observable_formation_match(harness):
    h = harness
    expected = (replace(TEAM[0], assistant=True, assistant_id="verified-lender"), *TEAM[1:])
    h.screens[:] = [battle(hp=0), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.fight(h.frame(FORMATION), expected)
    assert result.won is True
    event = json.loads((h.runner.run_dir / "events.jsonl").read_text().splitlines()[0])
    assert event["team"][0]["assistant_id"] == "verified-lender"


@pytest.mark.parametrize("bad", [replace(battle(), boss="Different Boss"), battle(mock=True)])
def test_wrong_boss_or_mock_mode_stops_real_combat(harness, bad):
    h = harness
    h.screens[:] = [bad]
    with pytest.raises(TaskError, match="boss changed|Expected real"):
        h.runner.fight(h.frame(FORMATION), TEAM, mock=False)
    assert h.taps == [(1177, 659)]


def test_missing_damage_fails_without_dismissing_evidence(harness):
    h = harness
    h.screens[:] = [battle(hp=0), RESULT, replace(DAMAGE, damage_by_student={})]
    with pytest.raises(TaskError, match="did not reach damage"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert h.taps == [(1177, 659), (1055, 665)]
    assert h.now[0] < 20


def test_animated_damage_report_is_observed_until_all_six_values_are_readable(harness):
    h = harness
    h.screens[:] = [battle(), RESULT, replace(DAMAGE, damage_by_student={}),
                   replace(DAMAGE, damage_by_student={TEAM[0].student_id: 100}), DAMAGE, RESULT]
    result, _ = h.runner.fight(h.frame(FORMATION), TEAM)
    assert result.damage_by_student == DAMAGE.damage_by_student
    assert h.taps == [(1177, 659), (1055, 665), (932, 122)]


def test_unknown_cutin_is_bounded_and_never_sends_blind_inputs(harness, monkeypatch):
    h = harness
    monkeypatch.setattr("ba_automator.assault_battle.BATTLE_TIMEOUT_SECONDS", 6)
    with pytest.raises(TaskError, match="30-minute"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert h.taps == [(1177, 659)]


def test_expired_formation_is_reobserved_before_mobilize(harness):
    h = harness
    old = h.frame(FORMATION)
    h.now[0] = 10
    h.screens[:] = [FORMATION, battle(hp=0), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.fight(old, TEAM)
    assert result.won is True and h.taps[0] == (1177, 659)


def test_zero_hp_never_overrides_an_unverified_or_losing_result(harness):
    h = harness
    failed = replace(RESULT, won=False)
    h.screens[:] = [battle(hp=0), failed, DAMAGE, failed]
    result, _ = h.runner.fight(h.frame(FORMATION), TEAM)
    assert result.won is False


def test_recognized_defeat_reads_damage_for_free_assistant_fallback(harness):
    h = harness
    failed = replace(RESULT, won=False, elapsed_seconds=None,
                     target=(638, 659), confirm_target=(638, 659), damage_target=(537, 667))
    h.screens[:] = [battle(), failed, DAMAGE, failed]
    result, frame = h.runner.fight(h.frame(FORMATION), TEAM)
    assert result.won is False and result.remaining_seconds is None
    assert result.damage_by_student == DAMAGE.damage_by_student
    assert least_damage_striker(TEAM, result) == TEAM[0]
    assert frame.screen is failed
    assert h.taps == [(1177, 659), (537, 667), (932, 122)]
    assert (638, 659) not in h.taps  # Parent handles cleanup before another mock.


@pytest.mark.parametrize("observed_auto", [None, False])
def test_winning_result_without_auto_acknowledgment_cannot_qualify(harness, observed_auto):
    h = harness
    h.screens[:] = [battle(auto=observed_auto), RESULT, DAMAGE, RESULT]
    with pytest.raises(TaskError, match="Auto was not visually verified enabled"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert (h.runner.run_dir / "mock-result.png").exists()
    assert (h.runner.run_dir / "mock-damage.png").exists()
    assert h.taps[-2:] == [(1055, 665), (932, 122)]
    assert (1170, 665) not in h.taps  # Result remains open; no proof was returned.
    events = [json.loads(line) for line in (h.runner.run_dir / "events.jsonl").read_text().splitlines()]
    assert any(event.get("event") == "total_assault_battle_result" and event["auto_verified"] is False
               for event in events)


def test_auto_turning_off_after_verified_on_stops_without_retoggling(harness):
    h = harness
    h.screens[:] = [battle(auto=True), battle(auto=False)]
    with pytest.raises(TaskError, match="Auto unexpectedly turned off"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert h.taps == [(1177, 659)]


def test_one_frame_boss_ocr_error_is_rechecked_without_input_or_progress_proof(harness):
    h = harness
    error = replace(battle(hp=0, seconds=999, auto=False), boss="Druinbarka")
    h.screens[:] = [battle(), error, battle(), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.fight(h.frame(FORMATION), TEAM)
    assert result.won is True
    assert h.taps == [(1177, 659), (1055, 665), (932, 122)]
    events = [json.loads(line) for line in (h.runner.run_dir / "events.jsonl").read_text().splitlines()]
    assert sum(event.get("event") == "total_assault_boss_ambiguous" for event in events) == 1
    assert not any(event.get("boss_zero_observed") is True for event in events)


def test_consistent_wrong_boss_requires_repeated_observation(harness):
    h = harness
    wrong = replace(battle(), boss="Another Boss")
    h.screens[:] = [wrong, wrong, wrong]
    with pytest.raises(TaskError, match="boss changed"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert 3 <= h.now[0] < 5  # Three observations plus the Mobilize settling delay.
    assert h.taps == [(1177, 659)]


def test_fluctuating_boss_ocr_ambiguity_is_bounded(harness):
    h = harness
    h.screens[:] = [replace(battle(), boss=f"Unreadable {i}") for i in range(20)]
    with pytest.raises(TaskError, match="remained unreadable"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert 15 <= h.now[0] < 17
    assert h.taps == [(1177, 659)]


def test_result_after_unresolved_boss_mismatch_never_qualifies(harness):
    h = harness
    h.screens[:] = [battle(), replace(battle(), boss="Druinbarka"), RESULT]
    with pytest.raises(TaskError, match="boss could be re-verified"):
        h.runner.fight(h.frame(FORMATION), TEAM)
    assert h.taps == [(1177, 659)]


ENTRY_NOTICE = AssaultScreen("entry_confirm", count=1, difficulty="hardcore", tickets=6, after_tickets=5,
                             target=(767, 498), confirm_target=(767, 498))


def prepare_entry(h):
    now = h.runner.wall_clock()
    h.runner.context = replace(h.runner.context, day_key=game_day(now))
    assault_state.record_mock(h.runner.config, h.runner.context, TEAM,
                              MockResult(True, 90, None, DAMAGE.damage_by_student),
                              h.runner.run_dir.name, now=now)
    return assault_state.begin_entry(h.runner.config, h.runner.context, TEAM,
                                      h.runner.run_dir.name, 6, now=now)


def test_resume_observation_does_not_replay_mobilize(harness):
    h = harness
    h.screens[:] = [battle(), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.observe_battle(TEAM)
    assert result.won is True
    assert h.taps == [(1055, 665), (932, 122)]


def test_real_ticket_notice_requires_the_same_persisted_entry(harness):
    h = harness
    intent = prepare_entry(h)
    h.screens[:] = [ENTRY_NOTICE, battle(), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.observe_battle(TEAM, mock=False)
    assert result.won is True
    assert h.taps == [(767, 498), (1055, 665), (932, 122)]
    assert assault_state.read_state(h.runner.config)["pending"]["id"] == intent
    assert (h.runner.run_dir / "real-ticket-confirmation.png").exists()


@pytest.mark.parametrize("mismatch", ["missing", "context", "team", "run", "day", "count", "target"])
def test_ticket_notice_cannot_bypass_entry_intent_or_observed_control(harness, mismatch):
    h = harness
    team = TEAM
    notice = ENTRY_NOTICE
    if mismatch != "missing":
        prepare_entry(h)
    if mismatch == "context":
        h.runner.context = replace(h.runner.context, boss="Different Boss")
    elif mismatch == "team":
        team = (replace(TEAM[0], level=80), *TEAM[1:])
    elif mismatch == "run":
        h.runner.run_dir = h.runner.run_dir.with_name("different-run")
    elif mismatch == "day":
        h.now[0] = 86400
    elif mismatch == "count":
        notice = replace(notice, count=2)
    elif mismatch == "target":
        notice = replace(notice, confirm_target=None)
    with pytest.raises(TaskError, match="ticket confirmation"):
        h.runner.confirm_entry_notice(h.frame(notice), team, mock=False)
    assert h.taps == []


def test_mock_never_confirms_a_ticket_even_with_matching_persisted_entry(harness):
    h = harness
    prepare_entry(h)
    h.screens[:] = [ENTRY_NOTICE]
    with pytest.raises(TaskError, match="free Total Assault mock"):
        h.runner.observe_battle(TEAM, mock=True)
    assert h.taps == []


def test_ticket_notice_is_never_confirmed_twice(harness):
    h = harness
    prepare_entry(h)
    h.screens[:] = [ENTRY_NOTICE, ENTRY_NOTICE]
    with pytest.raises(TaskError, match="repeated its ticket confirmation"):
        h.runner.observe_battle(TEAM, mock=False)
    assert h.taps == [(767, 498)]


def test_empty_formation_warning_is_never_dismissed_as_ticket_confirmation(harness):
    h = harness
    prepare_entry(h)
    h.screens[:] = [AssaultScreen("empty_formation_notice", confirm_target=(767, 498))]
    with pytest.raises(TaskError, match="Unexpected Total Assault battle screen"):
        h.runner.observe_battle(TEAM, mock=False)
    assert h.taps == []


ASSISTANT_TEAM = (replace(TEAM[0], stars=5, level=90, assistant=True, assistant_id="verified-lender"), *TEAM[1:])
ASSISTANT_NOTICE = AssaultScreen("assistant_confirm", confirm_target=(768, 510),
                                 credit_fee=40000, assistant_level=90, assistant_stars=5)


def prepare_assistant_entry(h):
    now = h.runner.wall_clock()
    h.runner.context = replace(h.runner.context, day_key=game_day(now))
    assault_state.record_mock(h.runner.config, h.runner.context, ASSISTANT_TEAM,
                              MockResult(True, 90, None, DAMAGE.damage_by_student),
                              h.runner.run_dir.name, now=now)
    return assault_state.begin_entry(h.runner.config, h.runner.context, ASSISTANT_TEAM,
                                      h.runner.run_dir.name, 6, now=now)


def test_qualified_assistant_fee_is_confirmed_once_and_logged_separately_from_loot(harness):
    h = harness
    prepare_assistant_entry(h)
    h.screens[:] = [ASSISTANT_NOTICE, battle(), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.observe_battle(ASSISTANT_TEAM, mock=False)
    assert result.won is True and h.taps == [(768, 510), (1055, 665), (932, 122)]
    events = [json.loads(line) for line in (h.runner.run_dir / "events.jsonl").read_text().splitlines()]
    fee = next(event for event in events if event.get("kind") == "total_assault_assistant_fee")
    assert fee["credit_cost"] == 40000 and fee["status"] == "confirmation_sent"
    assert "items" not in fee
    with pytest.raises(TaskError, match="repeated its assistant fee"):
        h.runner.confirm_assistant_notice(h.frame(ASSISTANT_NOTICE), ASSISTANT_TEAM, mock=False)
    assert h.taps.count((768, 510)) == 1


@pytest.mark.parametrize("bad", ["mock", "fee", "level", "stars", "target", "owned", "lender", "missing_intent"])
def test_assistant_fee_cannot_bypass_mock_team_cost_or_entry_guards(harness, bad):
    h = harness
    if bad != "missing_intent":
        prepare_assistant_entry(h)
    team = ASSISTANT_TEAM
    notice = ASSISTANT_NOTICE
    if bad == "fee": notice = replace(notice, credit_fee=400000)
    if bad == "level": notice = replace(notice, assistant_level=89)
    if bad == "stars": notice = replace(notice, assistant_stars=4)
    if bad == "target": notice = replace(notice, confirm_target=None)
    if bad == "owned": team = TEAM
    if bad == "lender": team = (replace(team[0], assistant_id="different-lender"), *team[1:])
    with pytest.raises(TaskError, match="assistant|persisted entry"):
        h.runner.confirm_assistant_notice(h.frame(notice), team, mock=bad == "mock")
    assert not h.taps


def test_ticket_confirmation_cannot_be_replayed_by_resuming_observation(harness):
    h = harness
    prepare_entry(h)
    h.runner.confirm_entry_notice(h.frame(ENTRY_NOTICE), TEAM, mock=False)
    h.screens[:] = [ENTRY_NOTICE]
    with pytest.raises(TaskError, match="repeated its ticket confirmation"):
        h.runner.observe_battle(TEAM, mock=False)
    assert h.taps == [(767, 498)]


def test_distinct_qualified_entry_can_confirm_its_own_assistant_fee(harness):
    h = harness
    first = prepare_assistant_entry(h)
    h.runner.confirm_assistant_notice(h.frame(ASSISTANT_NOTICE), ASSISTANT_TEAM, mock=False)
    assault_state.complete_entry(h.runner.config, first, tickets_after=5, won=True,
                                 now=h.runner.wall_clock())
    second = prepare_assistant_entry(h)
    assert first != second
    h.runner.confirm_assistant_notice(h.frame(ASSISTANT_NOTICE), ASSISTANT_TEAM, mock=False)
    assert h.taps == [(768, 510), (768, 510)]


def test_paid_notices_settle_without_duplicate_inputs(harness):
    h = harness
    prepare_assistant_entry(h)
    h.screens[:] = [ASSISTANT_NOTICE, ASSISTANT_NOTICE, ENTRY_NOTICE, ENTRY_NOTICE,
                   battle(), RESULT, DAMAGE, RESULT]
    result, _ = h.runner.observe_battle(ASSISTANT_TEAM, mock=False)
    assert result.won is True
    assert h.taps == [(768, 510), (767, 498), (1055, 665), (932, 122)]


@pytest.mark.parametrize("change", [dict(tickets=5, after_tickets=4), dict(difficulty="extreme"), dict(after_tickets=4)])
def test_ticket_notice_must_match_pending_tier_and_ticket_budget(harness, change):
    h = harness
    prepare_entry(h)
    with pytest.raises(TaskError, match="difficulty or ticket budget"):
        h.runner.confirm_entry_notice(h.frame(replace(ENTRY_NOTICE, **change)), TEAM, mock=False)
    assert not h.taps
