"""Tactical Challenge ticket, daily-history, ladder, and formation decisions."""

import random

import pytest

from ba_automator.tactical_battles import (
    BattleState,
    Opponent,
    Student,
    TacticalPlanningError,
    choose_opponent,
    eligible_opponents,
    estimated_team_levels,
    fill_formation,
    opponent_score,
    rank_opponents,
    record_result,
    state_for_day,
    ticket_budget,
)


DAY = "2026-09-26"


def opponents():
    return rank_opponents((Opponent("high-level", 800, 90), Opponent("low-level", 900, 65), Opponent("middle-level", 850, 80)), rng=random.Random(0))


@pytest.mark.parametrize("preserve,expected_count", [(1, 4), (0, 5)])
def test_each_ticket_uses_a_different_opponent_and_honors_reserve(preserve, expected_count):
    candidates = tuple(Opponent(str(i), 500 + i, 20 + i) for i in range(5))
    state = BattleState(DAY)
    choices = []
    for tickets in range(5, 0, -1):
        opponent = choose_opponent(candidates, 1000, state, tickets, preserve=preserve)
        if opponent is None:
            break
        choices.append(opponent.opponent_id)
        state = record_result(state, opponent.opponent_id, False)
    assert choices == [str(i) for i in range(expected_count)]
    assert state.attempts == {identity: 1 for identity in choices}


@pytest.mark.parametrize("tickets,preserve,expected", [(5, 1, 4), (1, 1, 0), (0, 1, 0), (3, 5, 0), (5, 0, 5), (8, 1, 7)])
def test_ticket_budget(tickets, preserve, expected):
    assert ticket_budget(tickets, preserve) == expected


@pytest.mark.parametrize("tickets,preserve", [(None, 1), (True, 1), (5, False), (5.0, 1), (5, -1), (-1, 0)])
def test_unreadable_or_invalid_ticket_budget_rejected(tickets, preserve):
    with pytest.raises(TacticalPlanningError):
        ticket_budget(tickets, preserve)


def test_opponents_must_all_be_ahead():
    for invalid_rank in (1000, 1001):
        with pytest.raises(TacticalPlanningError, match="all available higher ranks"):
            eligible_opponents((Opponent("a", 500, 50), Opponent("b", 600, 60), Opponent("c", invalid_rank, 10)), 1000)


@pytest.mark.parametrize("candidates", [
    (),
    (Opponent("a", 500, 50),),
    (Opponent("a", 500, 50), Opponent("b", 600, 60), Opponent("b", 700, 70)),
    (Opponent("a", 500, 50), Opponent("b", 500, 60), Opponent("c", 700, 70)),
    (Opponent("a", 500, 50), Opponent("b", 600, 60), None),
])
def test_partial_or_ambiguous_opponent_lists_rejected(candidates):
    with pytest.raises(TacticalPlanningError):
        eligible_opponents(candidates, 1000)


def test_account_level_weights_estimated_six_unit_total():
    high_account = Opponent("higher-account", 500, 90, (1, 1, 1, 1, 1, 1))
    low_account = Opponent("lower-account", 700, 60, (40, 40, 40))
    assert opponent_score(high_account) == 12
    assert estimated_team_levels(low_account) == 300
    assert opponent_score(low_account) == 200
    assert rank_opponents((low_account, high_account))[0] == high_account


def test_hidden_students_are_assumed_at_the_opponents_maximum_level():
    assert estimated_team_levels(Opponent("a", 500, 90, (40, 50, 60))) == 420
    assert estimated_team_levels(Opponent("b", 600, 60)) == 360
    assert estimated_team_levels(Opponent("c", 700, 60, (60,) * 6)) == 360


def test_tied_scores_randomize_once_without_account_level_or_rank_tiebreaks():
    candidates = (Opponent("a", 700, 90, (5,) * 6), Opponent("b", 500, 30), Opponent("c", 600, 60, (15,) * 6))
    result = rank_opponents(candidates, rng=random.Random(7))
    assert result == rank_opponents(candidates, rng=random.Random(7))
    first_choices = {rank_opponents(candidates, rng=random.Random(seed))[0].opponent_id for seed in range(20)}
    assert first_choices == {"a", "b", "c"}
    for _ in range(5):
        assert choose_opponent(result, 1000, BattleState(DAY), 5) == result[0]


def test_eligible_current_list_keeps_ui_order_for_later_survey_ranking():
    candidates = (Opponent("a", 700, 90), Opponent("b", 500, 30), Opponent("c", 600, 60))
    assert eligible_opponents(candidates, 1000) == candidates


def test_top_three_ranks_do_not_wait_for_impossible_full_lists():
    assert eligible_opponents((), 1) == ()
    candidates = (Opponent("a", 1, 90), Opponent("b", 3, 90), Opponent("c", 4, 90))
    assert eligible_opponents(candidates, 2) == (candidates[0],)
    candidates = (Opponent("a", 1, 90), Opponent("b", 2, 90), Opponent("c", 4, 90))
    assert eligible_opponents(candidates, 3) == candidates[:2]
    assert choose_opponent((), 1, BattleState(DAY), 5) is None
    assert choose_opponent(candidates[:1], 2, BattleState(DAY), 5) == candidates[0]


def test_top_three_still_require_every_available_higher_rank():
    with pytest.raises(TacticalPlanningError, match="all available higher ranks"):
        eligible_opponents((Opponent("a", 1, 90), Opponent("b", 4, 90), Opponent("c", 5, 90)), 3)


@pytest.mark.parametrize("levels", [(None,), (True,), (0,), (91,), (20,) * 7, "unknown"])
def test_unknown_or_impossible_visible_levels_are_rejected(levels):
    with pytest.raises(TacticalPlanningError):
        Opponent("a", 500, 90, levels)


def test_aggregate_survey_can_contain_more_than_three_opponents():
    candidates = tuple(Opponent(str(i), 500 + i, 90, (i,) * 6) for i in range(1, 7))
    survey = rank_opponents(reversed(candidates))
    assert len(survey) == 6
    assert choose_opponent(survey, 1000, BattleState(DAY), 5) == candidates[0]


def test_survey_rejects_duplicate_opponent_identities():
    candidate = Opponent("a", 500, 90)
    with pytest.raises(TacticalPlanningError, match="Deduplicate"):
        rank_opponents((candidate, candidate))


@pytest.mark.parametrize("won", [False, True])
def test_fought_identity_is_excluded_even_when_rows_levels_and_ranks_change(won):
    state = record_result(BattleState(DAY), "low-level", won)
    refreshed = (Opponent("low-level", 820, 66), Opponent("new-lower", 700, 1), Opponent("new-middle", 750, 40))
    assert choose_opponent(refreshed, 1000, state, 4).opponent_id == "new-lower"
    assert state.retry_opponent_id is None
    with pytest.raises(TacticalPlanningError, match="already been fought"):
        record_result(state, "low-level", not won)


def test_missing_previously_fought_opponent_does_not_block_new_choice():
    state = record_result(BattleState(DAY), "disappeared", False)
    assert choose_opponent(opponents(), 1000, state, 4).opponent_id == "low-level"


def test_legacy_attempt_cap_and_retry_do_not_authorize_another_battle():
    state = BattleState(DAY, {"low-level": 2, "middle-level": 1}, "middle-level")
    assert state.retry_opponent_id is None
    assert choose_opponent(opponents(), 1000, state, 3).opponent_id == "high-level"
    with pytest.raises(TacticalPlanningError, match="already been fought"):
        record_result(state, "low-level", False)


def test_exhausted_full_list_does_not_reuse_opponents():
    state = BattleState(DAY, {p.opponent_id: 1 for p in opponents()})
    assert choose_opponent(opponents(), 1000, state, 5) is None


def test_result_does_not_mutate_previous_history():
    original = BattleState(DAY)
    loss = record_result(original, "low-level", False)
    won = record_result(loss, "middle-level", True)
    assert original.attempts == {} and original.retry_opponent_id is None
    assert loss.attempts == {"low-level": 1} and loss.retry_opponent_id is None
    assert won.attempts == {"low-level": 1, "middle-level": 1} and won.retry_opponent_id is None


def test_unknown_result_does_not_become_defeat():
    state = BattleState(DAY)
    with pytest.raises(TacticalPlanningError, match="must be observed"):
        record_result(state, "low-level", None)
    assert state.attempts == {}


def test_new_game_day_forgets_all_attempts_and_retry():
    state = record_result(BattleState(DAY), "low-level", False)
    assert state_for_day(state, DAY) is state
    assert state_for_day(state, "2026-09-27") == BattleState("2026-09-27")
    assert state_for_day(None, DAY) == BattleState(DAY)


@pytest.mark.parametrize("day", [None, "2026-02-30", "20260926", "2026-9-26", True])
def test_bad_game_day_rejected(day):
    with pytest.raises(TacticalPlanningError):
        state_for_day(None, day)


def test_history_does_not_share_the_callers_dictionary():
    attempts = {"a": 1}
    state = BattleState(DAY, attempts, "a")
    attempts["a"] = 2
    assert state.attempts == {"a": 1}


@pytest.mark.parametrize("attempts,retry", [({"a": 0}, None), ({"a": 3}, None), ({"a": True}, None), ({}, ""), ({}, True)])
def test_corrupt_history_rejected(attempts, retry):
    with pytest.raises(TacticalPlanningError):
        BattleState(DAY, attempts, retry)


def student(name, level, role="striker"):
    return Student(name, role, level)


def test_formation_fills_only_blanks_with_highest_compatible_students():
    existing = (student("existing-low", 1), None, student("existing-high", 90), None, None, student("existing-special", 5, "special"))
    owned = (student("weak", 10), student("best-special", 90, "special"), student("runner-up", 80), student("best", 89), student("other-special", 80, "special"), existing[2])
    result = fill_formation(existing, owned)
    assert [p.student_id for p in result] == ["existing-low", "best", "existing-high", "runner-up", "best-special", "existing-special"]
    assert existing[1] is None and existing[3] is None


def test_empty_formation_gets_four_strikers_and_two_specials():
    owned = tuple(student(f"s{i}", i + 1) for i in range(6)) + tuple(student(f"p{i}", i + 1, "special") for i in range(3))
    result = fill_formation((None,) * 6, owned)
    assert [p.student_id for p in result] == ["s5", "s4", "s3", "s2", "p2", "p1"]


def test_full_formation_needs_no_roster_survey():
    existing = tuple(student(f"s{i}", 1) for i in range(4)) + tuple(student(f"p{i}", 1, "special") for i in range(2))
    def unread_roster():
        pytest.fail("A complete formation must remain untouched")
        yield
    assert fill_formation(existing, unread_roster()) == existing


def test_overlapping_roster_pages_cannot_duplicate_students():
    existing = (None, student("b", 10), student("c", 10), student("d", 10), student("e", 10, "special"), student("f", 10, "special"))
    same = student("a", 90)
    assert fill_formation(existing, (same, same))[0] == same
    with pytest.raises(TacticalPlanningError, match="conflicting"):
        fill_formation(existing, (same, student("a", 89)))


def test_equal_level_roster_ties_keep_observed_order():
    owned = tuple(student(name, 80) for name in ("d", "b", "c", "a")) + (student("q", 80, "special"), student("p", 80, "special"))
    assert fill_formation((None,) * 6, owned) == owned


def test_insufficient_roster_fails_instead_of_partial_formation():
    with pytest.raises(TacticalPlanningError, match="Not enough"):
        fill_formation((None,) * 6, (student("a", 90),))


@pytest.mark.parametrize("existing", [
    (None,) * 5,
    (student("wrong-role", 90, "special"),) + (None,) * 5,
    (student("duplicate", 90), student("duplicate", 90)) + (None,) * 4,
    ("unreadable",) + (None,) * 5,
])
def test_invalid_existing_formation_rejected(existing):
    with pytest.raises(TacticalPlanningError):
        fill_formation(existing, ())


def test_reward_route_has_no_battle_targets():
    from pathlib import Path
    from ba_automator.tactical_rewards import TacticalVision
    from ba_automator.vision import StartupVision

    path = Path(__file__).parent / "fixtures" / "ap-campaign-stable.png"
    result = TacticalVision(StartupVision()).analyze(path.read_bytes())
    assert result.kind == "campaign" and result.target == (868, 581)


@pytest.mark.parametrize("account,expected", [(90, 720), (89, 356), (80, 320), (70, 280), (91, 720)])
def test_account_level_multiplier_matches_requested_examples(account, expected):
    opponent = Opponent("a", 100, account, (60,) * 6)
    assert estimated_team_levels(opponent) == 360
    assert opponent_score(opponent) == expected


def test_weighting_can_change_the_preferred_opponent():
    higher_account = Opponent("high", 500, 90, (50,) * 6)
    lower_account = Opponent("low", 600, 70, (60,) * 6)
    assert estimated_team_levels(higher_account) < estimated_team_levels(lower_account)
    assert opponent_score(lower_account) < opponent_score(higher_account)
    assert rank_opponents((higher_account, lower_account))[0] == lower_account


def test_level_ninety_jump_and_hidden_unit_examples():
    below = Opponent("below", 500, 89)
    capped = Opponent("capped", 500, 90)
    eighty = Opponent("eighty", 500, 80)
    assert opponent_score(below) == pytest.approx(534 * 89 / 90)
    assert opponent_score(capped) == 1080
    assert opponent_score(eighty) == pytest.approx(426.6666666667)
