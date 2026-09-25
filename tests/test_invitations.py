"""Offline evidence for exact rows and highest non-maxed Cafe invitations."""

import pytest

from ba_automator.invitations import (
    choose_invitation, normalized_identity, parse_invitation_rows, relationship_cap,
)
from ba_automator.vision import Word


def word(text, x, y, confidence=.99):
    return Word(text, confidence, (x - 15, y - 10, x + 15, y + 10))


def row(name="Aris (Maid)", rank=26, enabled=True, y=222):
    return {"name": name, "identity": normalized_identity(name), "rank": rank,
            "enabled": enabled, "target": (787, y)}


def row_words(name="Aris (Maid)", rank="26", *, y=222, control="Invite"):
    result = [word(name, 560, y - 10), word(control, 787, y)]
    if rank is not None:
        result.append(word(rank, 506, y + 20))
    return result


def test_live_rows_preserve_raw_names_and_bind_rank_to_own_control():
    # Sanitized OCR from invite-list.json: only in-game names, buttons, and
    # relationship digits; no account identifiers or balance HUD.
    records = [
        ("Aris (Maid)", (482, 195, 599, 224)), ("Invite", (737, 207, 806, 235)),
        ("26", (490, 228, 522, 251)),
        ("Tsubaki", (482, 274, 568, 300)), ("Invite", (737, 285, 806, 313)),
        ("24", (489, 304, 523, 332)),
        ("Eimi (Armed)", (484, 352, 619, 379)), ("Invite", (737, 363, 806, 391)),
        ("23", (488, 382, 522, 410)),
        ("Hina", (481, 427, 538, 458)), ("Invite", (734, 437, 807, 471)),
        ("23", (489, 460, 523, 488)),
        ("Michiru (Dress)", (484, 507, 642, 533)), ("Invite", (737, 518, 806, 547)),
        ("23", (489, 538, 523, 566)), ("Niko", (479, 589, 536, 606)),
    ]
    rows = parse_invitation_rows(Word(text, .99, box) for text, box in records)
    assert [r["name"] for r in rows] == ["Aris (Maid)", "Tsubaki", "Eimi (Armed)", "Hina", "Michiru (Dress)"]
    assert [r["rank"] for r in rows] == [26, 24, 23, 23, 23]
    assert rows[0] == {"name": "Aris (Maid)", "identity": "aris maid", "rank": 26,
                       "enabled": True, "target": (771, 221)}
    assert choose_invitation(rows).row == rows[0]


def test_wrapped_variant_stays_with_name_instead_of_heart_number():
    words = row_words("Hoshino", "20") + [word("(Swimsuit)", 570, 235)]
    words += row_words("Haruna", "19", y=300)
    rows = parse_invitation_rows(words)
    assert rows[0]["name"] == "Hoshino (Swimsuit)"
    assert rows[0]["identity"] == "hoshino swimsuit"
    assert [r["rank"] for r in rows] == [20, 19]


def test_expanded_search_moves_rows_but_does_not_become_student_name():
    words = [word("Enter Student Name", 639, 218)]
    words += row_words(y=288) + row_words("Tsubaki", None, y=366)
    rows = parse_invitation_rows(words)
    assert [r["name"] for r in rows] == ["Aris (Maid)", "Tsubaki"]
    assert [r["rank"] for r in rows] == [26, None]


@pytest.mark.parametrize("rank", ["2O", "20/30", "20.5", "20a", "Lv 20", "0", "101", "-1", "1,0"])
def test_malformed_rank_cannot_turn_into_identity_or_eligibility(rank):
    rows = parse_invitation_rows(row_words(rank=rank))
    assert rows[0]["identity"] == "aris maid"
    assert rows[0]["rank"] is None
    assert choose_invitation(rows).status == "unreadable"


def test_conflicting_rank_reads_stay_unknown_but_matching_crop_read_is_ok():
    words = row_words() + [word("26", 506, 242)]
    assert parse_invitation_rows(words)[0]["rank"] == 26
    words.append(word("28", 507, 240))
    assert parse_invitation_rows(words)[0]["rank"] is None


@pytest.mark.parametrize("index", [0, 1, 2])
def test_low_confidence_name_button_or_rank_blocks_automatic_selection(index):
    words = row_words()
    old = words[index]
    words[index] = Word(old.text, .7, old.box)
    rows = parse_invitation_rows(words)
    assert len(rows) == 1
    assert choose_invitation(rows).status == "unreadable"


def test_missing_name_row_is_retained_and_blocks_lower_student():
    words = row_words()[1:] + row_words("Tsubaki", "24", y=300)
    rows = parse_invitation_rows(words)
    assert rows[0]["name"] == ""
    assert choose_invitation(rows).status == "unreadable"


def test_disabled_rows_are_preserved_but_not_selected():
    rows = parse_invitation_rows(row_words(control="Visiting") + row_words("Tsubaki", "24", y=300))
    assert not rows[0]["enabled"]
    assert choose_invitation(rows).row == rows[1]


def test_rank_cannot_be_borrowed_from_name_column_or_another_row():
    words = row_words(rank=None) + [word("29", 650, 242)]
    words += row_words("Tsubaki", "24", y=300)
    rows = parse_invitation_rows(words)
    assert [r["rank"] for r in rows] == [None, 24]
    assert rows[0]["identity"] == "aris maid"
    assert choose_invitation(rows).status == "unreadable"


def test_unknown_first_rank_is_not_skipped_in_favor_of_readable_lower_one():
    result = choose_invitation([row(rank=None), row("Tsubaki", 24, y=300)])
    assert result.status == "unreadable"
    assert result.row["identity"] == "aris maid"


@pytest.mark.parametrize("stars,cap", [(1, 10), (2, 10), (3, 20), (4, 30), (5, 100)])
def test_current_relationship_caps(stars, cap):
    assert relationship_cap(stars) == cap


@pytest.mark.parametrize("stars", [None, 0, 6, -1, True, 4.0, "4"])
def test_invalid_rarity_is_unknown(stars):
    assert relationship_cap(stars) is None


@pytest.mark.parametrize("rank", [1, 9, 11, 19, 21, 26, 29, 31, 99])
def test_nonboundary_rank_is_provably_not_maxed_without_rarity(rank):
    assert choose_invitation([row(rank=rank)]).status == "selected"


@pytest.mark.parametrize("rank", [10, 20, 30])
def test_boundary_requests_current_rarity_before_considering_lower_students(rank):
    rows = [row(rank=rank), row("Hina", rank - 1, y=300)]
    result = choose_invitation(rows)
    assert result.status == "need_rarity"
    assert result.row == rows[0]


@pytest.mark.parametrize("rank,stars", [(10, 3), (10, 5), (20, 4), (20, 5), (30, 5)])
def test_verified_current_rarity_allows_nonmaxed_boundary(rank, stars):
    result = choose_invitation([row(rank=rank)], {"aris maid": stars})
    assert result.status == "selected"


@pytest.mark.parametrize("rank,stars", [(10, 1), (10, 2), (20, 3), (30, 4)])
def test_verified_cap_skips_maxed_student(rank, stars):
    rows = [row(rank=rank), row("Hina", rank - 1, y=300)]
    result = choose_invitation(rows, {"aris maid": stars})
    assert result.status == "selected"
    assert result.row == rows[1]


def test_rank100_always_skips_without_lookup():
    rows = [row(rank=100), row("Hina", 99, y=300)]
    assert choose_invitation(rows).row == rows[1]
    assert choose_invitation(rows[:1]).status == "none"


def test_conflicting_current_rarity_does_not_allow_wasteful_selection():
    assert choose_invitation([row(rank=26)], {"aris maid": 3}).status == "unreadable"


def test_disabled_unknown_rank_does_not_block_next_enabled_student():
    rows = [row(rank=None, enabled=False), row("Hina", 19, y=300)]
    assert choose_invitation(rows).row == rows[1]


@pytest.mark.parametrize("rank", [None, True, "26", 0, 101])
def test_invalid_policy_rank_never_selects_student(rank):
    assert choose_invitation([row(rank=rank)]).status == "unreadable"


def test_no_rows_or_only_unavailable_rows_returns_none():
    assert choose_invitation([]).status == "none"
    assert choose_invitation([row(enabled=False)]).status == "none"


def test_exact_variant_identity_is_not_a_substring_match():
    rows = [row("Hoshino (Swimsuit)", 20)]
    assert choose_invitation(rows, {"hoshino": 5}).status == "need_rarity"
    assert choose_invitation(rows, {"hoshino swimsuit": 5}).status == "selected"
