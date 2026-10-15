"""Basic Info fields are read from fixed places; anything unread stays None."""

from types import SimpleNamespace

import cv2
import numpy as np

from ba_automator import student_profile
from ba_automator.student_inventory import flat, profile_settled
from ba_automator.vision import Word


def blank_png():
    ok, png = cv2.imencode(".png", np.full((720, 1280, 3), 240, np.uint8))
    return png.tobytes()


# Words as read live from Aris (Maid)'s page (2026-10-14), top bar removed.
ARIS = [Word("Basic Info", 1.0, (700, 129, 810, 153)), Word("MaxHP", 1.0, (682, 232, 752, 252)),
        Word("26013", 1.0, (745, 232, 800, 252)), Word("ATK 8667", 1.0, (888, 231, 971, 252)),
        Word("DEF 394", 1.0, (681, 267, 753, 288)), Word("Healing 5311", .97, (888, 265, 1000, 292)),
        Word("MAX", .98, (702, 401, 750, 425)), Word("MAX", .98, (810, 401, 858, 424)),
        Word("Lv.4", 1.0, (919, 402, 958, 423)), Word("Lv.4", 1.0, (1025, 402, 1063, 423)),
        Word("Lv.40", .97, (777, 453, 831, 478)), Word("RG", 1.0, (701, 483, 734, 505)),
        Word("⑩Aris (Maid)", 1.0, (70, 556, 215, 592)), Word("LV.60", .91, (690, 558, 732, 577)),
        Word("Lv.45", .89, (783, 558, 825, 577)), Word("LV:45", .95, (877, 558, 920, 577)),
        Word("STRIKER", 1.0, (342, 566, 411, 588)), Word("Lv.80", 1.0, (35, 594, 79, 615)),
        Word("35/39385", 1.0, (213, 595, 280, 614)), Word("T5", 1.0, (778, 617, 798, 633)),
        Word("Q", .87, (58, 626, 92, 660)), Word("Dealer", 1.0, (88, 633, 142, 653)),
        Word("Mystic", .99, (210, 630, 271, 656)), Word("BACK", 1.0, (69, 667, 123, 690)),
        Word("Light", 1.0, (209, 666, 260, 693))]


def test_page_fields_are_read_from_their_places():
    startup = SimpleNamespace(read=lambda image: [])     # enlarged reads find nothing here
    fields = student_profile.parse_basic_info(blank_png(), startup, ARIS)
    assert fields["name"] == "Aris (Maid)"                # the heart's stray symbol is dropped
    assert (fields["level"], fields["exp"], fields["role"]) == (80, [35, 39385], "STRIKER")
    assert (fields["class"], fields["attack"], fields["position"], fields["armor"]) == (
        "Dealer", "Mystic", "BACK", "Light")             # an icon read as "Q" is ignored
    assert fields["stats"] == {"hp": 26013, "atk": 8667, "def": 394, "healing": 5311}
    assert fields["skills"] == {"ex": "MAX", "basic": "MAX", "enhanced": 4, "sub": 4}
    assert fields["weapon"] == {"type": "RG", "level": 40, "stars": None}
    assert [gear["level"] for gear in fields["gear"]] == [60, 45, 45]
    assert [gear["tier"] for gear in fields["gear"]] == [None, 5, None]
    assert fields["bond"] is None and fields["stars"] is None   # blank pixels: not guessed


def test_weapon_stars_are_counted_by_area_because_they_touch():
    frame = np.full((720, 1280, 3), 240, np.uint8)
    assert student_profile.weapon_stars(frame) is None
    blue = (250, 200, 90)
    frame[505:517, 1068:1080] = blue                      # one star: 144 blue pixels
    assert student_profile.weapon_stars(frame) == 1
    frame[505:517, 1056:1068] = blue                      # a touching second star
    assert student_profile.weapon_stars(frame) == 2


def test_tier_templates_are_packaged():
    templates = student_profile.tier_templates()
    assert {1, 2, 3, 4, 5, 6, 7, 8} <= templates.keys()
    assert len(templates[7]) >= 2                          # italic 7s differ from item to item


def png_of(frame):
    ok, png = cv2.imencode(".png", frame)
    return png.tobytes()


def test_the_bond_heart_is_read_without_the_name_beside_it():
    frame = np.full((720, 1280, 3), 240, np.uint8)
    frame[552:600, 28:80] = (120, 60, 20)                  # the name panel's dark blue
    frame[560:590, 70:74] = 255                            # the first letter of the name
    seen = []
    startup = SimpleNamespace(read=lambda image: seen.append(image) or [])
    assert student_profile.bond_rank(png_of(frame), startup) is None
    first = seen[0]                                        # 2x crop, letter from x=68 on
    assert (first[:, (68 - 28) * 2:] == (120, 60, 20)).all()


def test_a_weak_bond_read_counts_only_when_another_agrees():
    def startup(*reads):
        queue = list(reads)
        return SimpleNamespace(read=lambda image: [Word(text, conf, (0, 0, 10, 10)) for text, conf in queue.pop(0)])
    png = blank_png()
    assert student_profile.bond_rank(png, startup([("7", .95)])) == 7
    assert student_profile.bond_rank(png, startup([("7", .72)], [], [("7", .76)])) == 7
    assert student_profile.bond_rank(png, startup([("7", .72)], [("1", .74)], [])) is None
    assert student_profile.bond_rank(png, startup([("71", .6)], [], [])) is None


def test_a_profile_is_read_only_once_its_tab_is_at_rest():
    assert profile_settled([Word("Basic Info", 1.0, (700, 129, 810, 153))])
    assert not profile_settled([Word("Basic Info", 1.0, (728, 129, 838, 153))])   # still sliding in


def test_csv_rows_flatten_every_field():
    row = flat({"name": "Aris (Maid)", "level": 80, "stars": 5, "bond": 29, "skills": {"ex": "MAX", "sub": 4},
                "stats": {"hp": 26013}, "weapon": {"type": "RG", "level": 40, "stars": 2},
                "gear": [{"tier": 8, "level": 60}], "exclusive_gear_tier": 2, "exp": [35, 39385]})
    from ba_automator.student_inventory import COLUMNS
    values = dict(zip(COLUMNS, row))
    assert (values["ex"], values["sub"], values["weapon_stars"], values["gear1_tier"], values["exp"]) == (
        "MAX", 4, 2, 8, "35/39385")
    assert values["gear2_tier"] == "" and values["hp"] == 26013
