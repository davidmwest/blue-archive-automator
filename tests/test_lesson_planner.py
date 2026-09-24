"""Offline policy checks: spending order, fresh ranks, and incomplete evidence."""

from dataclasses import replace
from itertools import combinations

import pytest

from ba_automator.lesson_planner import (
    LessonLocation,
    LessonRoom,
    LessonStudent,
    choose_lesson,
)


def location(id="a", *, rank=3, xp=100, xp_to_next=450, **kwargs):
    return LessonLocation(id, id.upper(), rank, xp, xp_to_next, **kwargs)


def room(id="one", *, school="a", bonds=(10,), unowned=0, **kwargs):
    students = tuple(LessonStudent(str(index), True, bond) for index, bond in enumerate(bonds))
    students += tuple(LessonStudent(f"unowned-{index}", False) for index in range(unowned))
    return LessonRoom(school, id, id.title(), students, **kwargs)


def test_relationship_inspects_all_schools_instead_of_picking_first_room():
    rooms = [room("first", bonds=(80,)), room("largest", school="b", bonds=(2, 3, 4)),
             room("not-owned", bonds=(), unowned=3)]
    decision = choose_lesson([location(), location("b")], rooms)
    assert decision.status == "selected"
    assert decision.room.id == "largest"
    assert decision.owned_count == 3
    assert decision.bond_total == 9


def test_relationship_counts_owned_students_not_all_portraits():
    rooms = [room("crowded", bonds=(90,), unowned=2), room("owned", bonds=(1, 2))]
    assert choose_lesson([location()], rooms).room.id == "owned"


def test_relationship_breaks_count_ties_by_sum_of_owned_bond_ranks():
    rooms = [room("largest-single", bonds=(40, 1)), room("largest-sum", bonds=(22, 23))]
    assert choose_lesson([location()], rooms).room.id == "largest-sum"


def test_default_does_not_require_rank_or_xp():
    decision = choose_lesson([LessonLocation("a", "A")], [room()])
    assert decision.status == "selected"


def test_equal_scores_have_stable_order_independent_of_scan_order():
    schools = [location("b"), location("a")]
    rooms = [room("second", school="b"), room("second"), room("first")]
    first = choose_lesson(schools, rooms)
    second = choose_lesson(reversed(schools), reversed(rooms))
    assert (first.location.id, first.room.id) == ("a", "first")
    assert second == first


def test_completed_room_is_not_repeated_even_if_it_had_the_best_score():
    completed = room("completed", bonds=(30, 40, 50), available=False)
    available = room("available", bonds=(20,))
    decision = choose_lesson([location()], [completed, available])
    assert decision.room == available
    assert choose_lesson([location()], [completed]).status == "complete"


def test_relationship_zero_owned_students_leaves_tickets_unused():
    decision = choose_lesson([location()], [room(bonds=(), unowned=3)])
    assert decision.status == "complete"
    assert "leaving tickets unused" in decision.reason


def test_successive_relationship_choices_maximize_observed_owned_encounters():
    # Fixed one-use rooms reduce to selecting the highest counts. Compare all
    # possible three-ticket sets, rather than repeating the implementation sort.
    rooms = [room(str(index), bonds=(10,) * count)
             for index, count in enumerate((1, 2, 3, 0, 3, 1))]
    score = 0
    for _ in range(3):
        decision = choose_lesson([location()], rooms)
        assert decision.status == "selected"
        score += decision.owned_count
        rooms = [replace(item, available=False) if item.id == decision.room.id else item
                 for item in rooms]
    all_scores = [sum(len(item.students) for item in subset) for subset in combinations(rooms, 3)]
    assert score == max(all_scores) == 8


def test_rank_policy_prefers_lowest_rank_even_when_other_school_has_more_students():
    decision = choose_lesson([location(rank=2, xp=200, xp_to_next=250), location("b", rank=3, xp=0)],
                             [room(), room(school="b", bonds=(30, 40, 50))],
                             strategy="school_rank")
    assert decision.location.id == "a"
    assert "rank 2, XP 200/250" in decision.reason


def test_rank_policy_includes_xp_within_equal_ranks():
    decision = choose_lesson([location(xp=200), location("b", xp=100)],
                             [room(bonds=(30, 40, 50)), room(school="b")], strategy="school_rank")
    assert decision.location.id == "b"


def test_rank_policy_compares_fractional_progress_without_float_rounding():
    decision = choose_lesson([location(xp=1, xp_to_next=3), location("b", xp=2, xp_to_next=7)],
                             [room(), room(school="b")], strategy="school_rank")
    assert decision.location.id == "b"


def test_rank_policy_favors_total_students_then_owned_bond_sum():
    rooms = [room("owned", bonds=(80, 90)), room("crowded", bonds=(1,), unowned=2)]
    decision = choose_lesson([location()], rooms, strategy="school_rank")
    assert decision.room.id == "crowded"
    assert decision.student_count == 3
    assert decision.owned_count == 1
    tied = [room("low-bond", bonds=(1,), unowned=2), room("high-bond", bonds=(40,), unowned=2)]
    assert choose_lesson([location()], tied, strategy="school_rank").room.id == "high-bond"


def test_equal_location_progress_uses_best_room_across_tied_locations():
    decision = choose_lesson([location(), location("b")],
                             [room(), room(school="b", bonds=(2, 3, 4))], strategy="school_rank")
    assert decision.location.id == "b"


def test_fresh_xp_after_each_ticket_changes_school_without_predicting_it():
    schools = [location(xp=0), location("b", xp=50)]
    rooms = [room("first"), room("second"), room(school="b")]
    first = choose_lesson(schools, rooms, strategy="school_rank")
    assert first.location.id == "a"
    fresh_schools = [replace(schools[0], xp=100), schools[1]]
    fresh_rooms = [replace(item, available=False) if item == first.room else item for item in rooms]
    second = choose_lesson(fresh_schools, fresh_rooms, strategy="school_rank")
    assert second.location.id == "b"
    assert schools[0].xp == 0  # The planner did not mutate or simulate account state.


def test_confirmed_rank_up_changes_school_on_next_ticket():
    schools = [location(rank=1, xp=0, xp_to_next=100), location("b", rank=2, xp=0, xp_to_next=250)]
    rooms = [room("one"), room("two"), room(school="b", bonds=(20, 30))]
    assert choose_lesson(schools, rooms, strategy="school_rank").location.id == "a"
    refreshed = [replace(schools[0], rank=2, xp=0, xp_to_next=250), schools[1]]
    assert choose_lesson(refreshed, rooms, strategy="school_rank").location.id == "b"


def test_capped_locations_do_not_displace_uncapped_ones():
    schools = [location(rank=12, xp=None, xp_to_next=None, capped=True), location("b")]
    decision = choose_lesson(schools, [room(bonds=(30, 40, 50)), room(school="b")], strategy="school_rank")
    assert decision.location.id == "b"
    assert not decision.fallback


def test_all_capped_locations_fall_back_to_owned_count_and_explain_why():
    schools = [location(capped=True), location("b", capped=True)]
    rooms = [room(bonds=(40,), unowned=2), room(school="b", bonds=(20, 30))]
    decision = choose_lesson(schools, rooms, strategy="school_rank")
    assert decision.location.id == "b"
    assert decision.strategy == "school_rank"
    assert decision.fallback
    assert "maximum rank" in decision.reason


def test_all_capped_and_no_owned_students_completes_without_spending():
    decision = choose_lesson([location(capped=True)], [room(bonds=(), unowned=3)], strategy="school_rank")
    assert decision.status == "complete"
    assert decision.fallback


def test_exhausted_lowest_school_does_not_block_next_school():
    decision = choose_lesson([location(rank=1), location("b", rank=5)],
                             [room(available=False), room(school="b")], strategy="school_rank")
    assert decision.location.id == "b"


def test_rank_policy_can_gain_xp_without_owned_students():
    decision = choose_lesson([location()], [room(bonds=(), unowned=2)], strategy="school_rank")
    assert decision.status == "selected"
    assert decision.owned_count == 0


def test_allowlist_excludes_other_locations_from_selection_and_evidence_requirements():
    schools = [location(), location("b", rank=None, xp=None)]
    rooms = [room(), room(school="b", inspection_complete=False)]
    decision = choose_lesson(schools, rooms, strategy="school_rank", allowed_location_ids=("a",))
    assert decision.location.id == "a"


def test_unknown_allowlist_location_blocks_instead_of_silently_ignoring_typo():
    decision = choose_lesson([location()], [room()], allowed_location_ids=("missing",))
    assert decision.status == "blocked"
    assert "missing" in decision.reason


def test_locked_locations_do_not_need_room_scans_or_rank_readings():
    decision = choose_lesson([location(), location("locked", rank=None, unlocked=False)], [room()])
    assert decision.status == "selected"


@pytest.mark.parametrize("kwargs", [{"inspection_complete": False}, {"inspection_complete": None}])
def test_incomplete_full_inspection_blocks_spending(kwargs):
    assert choose_lesson([location()], [room()], **kwargs).status == "blocked"


def test_missing_school_room_scan_is_not_an_empty_school():
    decision = choose_lesson([location(), location("b")], [room()])
    assert decision.status == "blocked"
    assert "B" in decision.reason


def test_unknown_ownership_is_not_counted_as_unowned():
    unknown = replace(room("unknown"), students=(LessonStudent("portrait", None),))
    decision = choose_lesson([location()], [room("known", bonds=(20,)), unknown])
    assert decision.status == "blocked"
    assert "ownership" in decision.reason


def test_unknown_bonds_do_not_block_a_unique_count_winner():
    decision = choose_lesson([location()], [room("winner", bonds=(None, None)), room("other", bonds=(80,))])
    assert decision.status == "selected"
    assert decision.bond_total is None


@pytest.mark.parametrize("strategy", ["relationship", "school_rank"])
def test_unknown_bonds_block_needed_tiebreaks_in_either_strategy(strategy):
    decision = choose_lesson([location()], [room("known"), room("unknown", bonds=(None,))], strategy=strategy)
    assert decision.status == "blocked"
    assert "tie" in decision.reason


def test_unknown_bond_in_losing_room_does_not_block_selected_room():
    decision = choose_lesson([location()], [room("winner", bonds=(20, 30)), room("other", bonds=(None,))])
    assert decision.room.id == "winner"


def test_unowned_students_do_not_need_bond_readings_for_tiebreaks():
    decision = choose_lesson([location()], [room("one", bonds=(10,), unowned=2),
                                          room("two", bonds=(20,), unowned=2)])
    assert decision.room.id == "two"


def test_rank_unique_count_winner_can_use_xp_policy_without_known_ownership():
    unknown = replace(room(), students=(LessonStudent("portrait", None),))
    decision = choose_lesson([location()], [unknown], strategy="school_rank")
    assert decision.status == "selected"
    assert decision.owned_count is None
    assert decision.bond_total is None


def test_rank_tie_does_require_ownership_to_compare_bond_sum():
    unknown = replace(room("unknown"), students=(LessonStudent("portrait", None),))
    decision = choose_lesson([location()], [room(), unknown], strategy="school_rank")
    assert decision.status == "blocked"


@pytest.mark.parametrize("change", [
    {"rank": None}, {"rank": 0}, {"rank": True}, {"xp": None}, {"xp": -1},
    {"xp_to_next": None}, {"xp_to_next": 0}, {"xp": 450}, {"capped": None},
])
def test_invalid_rank_evidence_blocks_rank_strategy(change):
    decision = choose_lesson([replace(location(), **change)], [room()], strategy="school_rank")
    assert decision.status == "blocked"


@pytest.mark.parametrize("change", [{"available": None}, {"inspection_complete": False}])
def test_unknown_room_evidence_blocks(change):
    assert choose_lesson([location()], [replace(room(), **change)]).status == "blocked"


def test_unknown_location_unlock_state_blocks():
    assert choose_lesson([location(unlocked=None)], [room()]).status == "blocked"


@pytest.mark.parametrize("student", [LessonStudent("one", 1, 5), LessonStudent("one", True, -1),
                                     LessonStudent("one", True, True), LessonStudent("", True, 1)])
def test_invalid_student_evidence_does_not_become_a_score(student):
    assert choose_lesson([location()], [replace(room(), students=(student,))]).status == "blocked"


def test_duplicate_portraits_cannot_inflate_a_room_score():
    student = LessonStudent("same", True, 10)
    decision = choose_lesson([location()], [replace(room(), students=(student, student))])
    assert decision.status == "blocked"


def test_duplicate_room_ids_cannot_duplicate_an_opportunity():
    assert choose_lesson([location()], [room(), room()]).status == "blocked"


def test_room_ids_are_scoped_to_location():
    decision = choose_lesson([location(), location("b")], [room(), room(school="b")])
    assert decision.status == "selected"


def test_duplicate_location_ids_block():
    assert choose_lesson([location(), location()], [room()]).status == "blocked"


def test_room_from_unknown_location_blocks():
    assert choose_lesson([location()], [room(school="missing")]).status == "blocked"


def test_no_unlocked_locations_has_no_work():
    assert choose_lesson([location(unlocked=False)], []).status == "complete"


def test_unknown_strategy_is_a_configuration_error():
    with pytest.raises(ValueError, match="strategy"):
        choose_lesson([location()], [room()], strategy="guess")
