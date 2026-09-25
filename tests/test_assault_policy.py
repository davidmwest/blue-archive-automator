from dataclasses import replace

import pytest

from ba_automator.assault_policy import (
    AssaultContext, AssaultPlanningError, MockResult, TeamMember, choose_assistant,
    comfortable_win, least_damage_striker, next_difficulty, replace_striker,
    team_fingerprint,
)


def team():
    return tuple(TeamMember(f"student-{slot}", slot, "striker" if slot < 4 else "special",
                            "mystic", 3, 80) for slot in range(6))


def result(**changes):
    return replace(MockResult(True, 60, tuple(f"student-{slot}" for slot in range(4)),
                              {f"student-{slot}": (slot + 1) * 100 for slot in range(6)}), **changes)


def assistant(identity="loan", stars=5, level=90, **changes):
    return replace(TeamMember(identity, 0, "striker", "mystic", stars, level,
                              True, f"lender:{identity}"), **changes)


def test_comfort_requires_observed_victory_time_margin_and_no_known_retirement():
    assert comfortable_win(team(), result())
    assert comfortable_win(team(), result(remaining_seconds=30))
    assert not comfortable_win(team(), result(remaining_seconds=29))
    assert comfortable_win(team(), result(remaining_seconds=29), 20)
    assert comfortable_win(team(), result(surviving_striker_ids=None))


@pytest.mark.parametrize("changes", [
    {"won": False}, {"won": 1}, {"remaining_seconds": None},
    {"remaining_seconds": True}, {"remaining_seconds": float("nan")},
    {"remaining_seconds": float("inf")},
    {"surviving_striker_ids": ("student-0", "student-1", "student-2")},
    {"surviving_striker_ids": ("student-0", "student-1", "student-2", "student-4")},
    {"surviving_striker_ids": ("student-0", "student-1", "student-2", "student-2")},
])
def test_incomplete_failed_or_ambiguous_mock_cannot_qualify(changes):
    assert not comfortable_win(team(), result(**changes))


def test_least_damage_uses_strikers_even_if_special_did_less():
    damages = {"student-0": 5, "student-1": 9, "student-2": 100,
               "student-3": 5, "student-4": 0, "student-5": 0}
    assert least_damage_striker(team(), result(won=False, damage_by_student=damages)).slot == 0
    # Damage is useful after a loss or timeout, without inventing survivor proof.
    observed = result(won=False, remaining_seconds=None, surviving_striker_ids=None,
                      damage_by_student=damages)
    assert least_damage_striker(team(), observed).slot == 0
    assert not comfortable_win(team(), observed)


@pytest.mark.parametrize("damage", [None, -1, 1.5, True, "0"])
def test_missing_or_unread_damage_is_not_treated_as_zero(damage):
    damages = dict(result().damage_by_student)
    damages["student-0"] = damage
    assert least_damage_striker(team(), result(damage_by_student=damages)) is None


def test_assistant_uses_stars_then_level_and_matches_auto_damage_type():
    candidates = [assistant("a", 4, 99), assistant("b", 5, 70),
                  assistant("wrong-type", 5, 99, damage_type="explosive"),
                  assistant("wrong-role", 5, 99, role="special", slot=4),
                  assistant("c", 5, 90), assistant("d", 5, 90),
                  assistant("student-1", 5, 99)]
    selected = choose_assistant(candidates, "mystic", team()[1:])
    assert selected.student_id == "c"
    changed = replace_striker(team(), "student-0", selected)
    assert changed[0].assistant and changed[0].slot == 0
    assert changed[1:] == team()[1:]
    assert team_fingerprint(changed) != team_fingerprint(team())


def test_no_matching_assistant_is_an_explicit_no_choice():
    assert choose_assistant([], "mystic", team()[1:]) is None
    assert choose_assistant([assistant(damage_type="piercing")], "mystic", team()[1:]) is None
    changed = replace_striker(team(), "student-0", assistant())
    assert choose_assistant([assistant("other")], "mystic", changed[1:] + changed[:1]) is None


def test_assistant_mock_must_report_the_new_student_surviving():
    changed = replace_striker(team(), "student-0", assistant())
    assert not comfortable_win(changed, result())
    assert comfortable_win(changed, result(surviving_striker_ids=("loan", "student-1", "student-2", "student-3")))
    other_lender = replace(changed[0], assistant_id="different-lender")
    assert team_fingerprint(changed) != team_fingerprint((other_lender,) + changed[1:])


@pytest.mark.parametrize("change", [
    {"event_id": ""}, {"boss": None}, {"difficulty": "guess"},
    {"day_key": "2026-9-25"}, {"day_key": "2026-09-25T00:00:00Z"},
])
def test_context_requires_identified_event_boss_and_game_day(change):
    with pytest.raises((ValueError, TypeError)):
        replace(AssaultContext("season-1", "Binah", "hardcore", "2026-09-25"), **change)


def test_team_fingerprint_has_stable_slot_order_but_includes_progression():
    assert team_fingerprint(team()) == team_fingerprint(tuple(reversed(team())))
    assert team_fingerprint(team()) != team_fingerprint((replace(team()[0], level=81),) + team()[1:])
    with pytest.raises(ValueError):
        team_fingerprint(team()[:-1])
    with pytest.raises(ValueError):
        team_fingerprint((team()[1],) + team()[1:])
    with pytest.raises(ValueError):
        replace_striker(team(), "student-4", assistant())
    with pytest.raises(ValueError):
        assistant(assistant_id=None)


def test_unlocked_target_does_not_need_inferences_about_other_difficulties():
    assert next_difficulty("hardcore", {"hardcore": False}) == "hardcore"
    assert next_difficulty("normal", {"normal": False}) == "normal"


def test_locked_target_chooses_nearest_explicitly_accessible_prerequisite():
    observed = {"hardcore": True, "very_hard": True, "hard": False, "normal": False}
    assert next_difficulty("hardcore", observed) == "hard"
    # A win does not itself authorize going harder: resurvey controls each step.
    observed["very_hard"] = False
    assert next_difficulty("hardcore", observed) == "very_hard"
    observed["hardcore"] = False
    assert next_difficulty("hardcore", observed) == "hardcore"


@pytest.mark.parametrize("observed", [
    {}, {"hardcore": None}, {"hardcore": 0}, {"hardcore": "unlocked"},
    {"hardcore": True, "hard": False},
    {"hardcore": True, "very_hard": None, "hard": False},
    {"hardcore": True, "very_hard": True, "hard": True},
])
def test_unreadable_or_missing_tier_cannot_be_skipped_for_a_lower_battle(observed):
    with pytest.raises(AssaultPlanningError, match="unreadable"):
        next_difficulty("hardcore", observed)


def test_no_verified_accessible_tier_has_no_speculative_fallback():
    with pytest.raises(AssaultPlanningError, match="No verified accessible"):
        next_difficulty("hardcore", dict.fromkeys(("normal", "hard", "very_hard", "hardcore"), True))
    with pytest.raises(AssaultPlanningError, match="Unknown"):
        next_difficulty("impossible", {"normal": False})
