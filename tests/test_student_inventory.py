"""The student inventory reads each student's page once, steps with the next arrow, and only reads."""

from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_automator import student_inventory as inventory
from ba_automator.config import Config
from ba_automator.vision import Word

NOW = datetime(2026, 10, 14, 13, tzinfo=timezone.utc)


def card(name, level, column, top):
    """One roster card: level label above the name, as on the 1280x720 grid."""
    x = 140 + column * 196
    words = [Word(f"Lv.{level}", .99, (x - 40, top, x + 40, top + 24))]
    for line, part in enumerate(name.split("\n")):
        words.append(Word(part, .99, (x - 80, top + 30 + line * 26, x + 80, top + 52 + line * 26)))
    return words


def test_cards_give_name_level_and_portrait():
    words = (card("Aris", 90, 0, 300) + card("Aris\n(Maid)", 85, 1, 300)
             + card("Hoshino", 80, 2, 620))                        # faded bottom row
    entries = inventory.roster_entries(words)
    assert [(e["name"], e["identity"], e["level"]) for e in entries] == [
        ("Aris", "aris", 90), ("Aris (Maid)", "aris maid", 85)]
    assert entries[0]["target"] == (140, 237)


def test_a_card_with_an_unreadable_name_part_is_left_out():
    words = card("Aris\n(Maid)", 85, 1, 300)
    words[-1] = Word("(Maid)", .5, words[-1].box)
    assert inventory.roster_entries(words) == []


@pytest.fixture
def config(tmp_path):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                  run_dir=tmp_path / "runs", lock_dir=tmp_path / "locks", state_dir=tmp_path / "state")


def test_inventory_is_saved_as_json_and_csv(config):
    students = [{"name": "Aris (Maid)", "identity": "aris maid", "level": 85, "stars": 3,
                 "profile": "p.png", "text": ["Basic Info"]},
                {"name": "Hoshino", "identity": "hoshino", "level": 90, "stars": None,
                 "profile": None, "text": []}]
    path = inventory.write_inventory(config, students, captured_at=NOW, complete=True)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert (document["count"], document["complete"], document["students"][0]["name"]) == (2, True, "Aris (Maid)")
    rows = list(csv.DictReader(path.with_suffix(".csv").read_text(encoding="utf-8").splitlines()))
    assert [(row["name"], row["level"], row["stars"], row["profile"]) for row in rows] == [
        ("Aris (Maid)", "85", "3", "p.png"), ("Hoshino", "90", "", "")]


class Game:
    """Home, the All list's top row, and student pages stepped through with the next arrow.

    After each arrow tap the next ``lag`` captures show no settled page, as
    while the page slides in; ``misread`` names are read wrongly once.
    """

    def __init__(self, students, *, wraps=True, lag=1, misread=(), popup_at=None):
        self.students, self.wraps, self.lag = students, wraps, lag
        self.misread, self.popup_at = set(misread), popup_at
        self.screen, self.index, self.all, self.pending = "home", None, False, 0
        self.taps, self.scrolls = [], 0

    def entries(self):
        return [{"identity": name.lower(), "name": name, "level": 1, "target": (140 + 196 * column, 300)}
                for column, (name, owned) in enumerate(self.students[:6])]

    def tap(self, x, y, **kwargs):
        target = (x, y)
        self.taps.append(target)
        if target == inventory.HOME:
            self.screen, self.index = "home", None
        elif self.screen == "home" and target == inventory.STUDENTS_BUTTON:
            self.screen = "roster"
        elif self.screen == "roster" and target == inventory.ALL_TAB:
            self.all = True
        elif self.screen == "roster" and target == (140, 300):
            self.screen, self.index, self.pending = "page", 0, self.lag
        elif self.screen == "page" and target == inventory.NEXT:
            if self.index + 1 < len(self.students) or self.wraps:
                self.index, self.pending = (self.index + 1) % len(self.students), self.lag
                if self.index == self.popup_at:
                    self.screen = "popup"
        else:
            raise AssertionError((self.screen, target))
        return True

    def swipe(self, start, end, **kwargs):
        self.scrolls += 1
        return True

    def shown(self):
        if self.screen == "roster":
            return self.entries()
        if self.screen != "page":
            return None
        if self.pending:
            self.pending -= 1
            return None
        name, owned = self.students[self.index]
        if name in self.misread:
            self.misread.discard(name)
            name = name[:-1] + "x"
        page = {"owned": owned, "identity": name.lower(), "name": name}
        return page if owned else {**page, "role": "STRIKER", "elephs": [5, 120]}


def runner_for(game, config, monkeypatch):
    runner = inventory.StudentInventoryRunner(
        config, SimpleNamespace(tap=game.tap, swipe=game.swipe, foreground_package=lambda: config.package),
        None, wall_clock=lambda: NOW)
    now = [0.0]
    runner.clock = lambda: now[0]
    runner.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    runner.started = 0.0

    def capture(*, ocr=False):
        now[0] += .2
        return now[0], b"png", {"screen": game.screen}, game.shown()
    runner.capture = capture
    runner.journal.save_image = lambda name, png: None
    monkeypatch.setattr(inventory, "roster_screen", lambda frame, words: frame["screen"] == "roster")
    monkeypatch.setattr(inventory, "_all_selected", lambda frame: game.all)
    monkeypatch.setattr(inventory, "_home", lambda runner, cap: cap[2]["screen"] == "home")
    monkeypatch.setattr(inventory, "roster_entries", lambda words: words)
    monkeypatch.setattr(inventory, "shown_student", lambda words: words if isinstance(words, dict) else None)
    monkeypatch.setattr(inventory, "profile_rarity", lambda frame, words, student: 3)
    monkeypatch.setattr(inventory, "page_text", lambda words: ["Basic Info"])
    monkeypatch.setattr(inventory, "parse_basic_info", lambda png, startup, words: {
        "name": words["name"], "stars": None, "level": 80, "bond": 20, "skills": {"ex": "MAX"}})
    monkeypatch.setattr(inventory, "_stationary", lambda before, after: True)
    monkeypatch.setattr(inventory, "_scroll", lambda runner, cap, *, down: runner.capture(ocr=True))
    monkeypatch.setattr(inventory, "HOME_STABLE_SECONDS", 0)
    return runner


def run(runner):
    try:
        return runner.run()
    finally:
        runner.journal.close()


STUDENTS = [(f"Student{n:02d}", n <= 9) for n in range(1, 15)]     # five not yet recruited


@pytest.mark.parametrize("wraps", [True, False])
def test_every_student_is_read_once_and_the_job_returns_home(config, monkeypatch, wraps):
    game = Game(STUDENTS, wraps=wraps, lag=2, misread={"Student05", "Student12"})
    result = run(runner_for(game, config, monkeypatch))
    assert result.status == "success" and game.screen == "home"
    document = json.loads(inventory.state_path(config).read_text(encoding="utf-8"))
    # A name misread once is never taken for another student.
    assert [student["name"] for student in document["students"]] == [name for name, _ in STUDENTS]
    assert document["complete"] is True
    owned = [student for student in document["students"] if student["owned"]]
    assert len(owned) == 9 and all(student["stars"] == 3 and student["bond"] == 20 for student in owned)
    assert document["students"][-1] == {
        "name": "Student14", "identity": "student14", "owned": False, "role": "STRIKER", "elephs": [5, 120],
        "profile": document["students"][-1]["profile"], "text": ["Basic Info"]}
    # Read only: Students, All, the first card, the next arrow, and home. Nothing else.
    allowed = {inventory.STUDENTS_BUTTON, inventory.ALL_TAB, (140, 300), inventory.NEXT, inventory.HOME}
    assert set(game.taps) <= allowed and game.taps.count((140, 300)) == 1


def test_a_walk_that_stops_keeps_what_it_read_unless_a_complete_inventory_exists(config, monkeypatch):
    game = Game(STUDENTS, popup_at=6)
    with pytest.raises(inventory.TaskError, match="no longer showing"):
        run(runner_for(game, config, monkeypatch))
    document = json.loads(inventory.state_path(config).read_text(encoding="utf-8"))
    assert document["complete"] is False and [s["name"] for s in document["students"]] == [
        name for name, _ in STUDENTS[:6]]

    inventory.write_inventory(config, [{"name": "Kept", "owned": True}], captured_at=NOW, complete=True)
    with pytest.raises(inventory.TaskError):
        run(runner_for(Game(STUDENTS, popup_at=3), config, monkeypatch))
    document = json.loads(inventory.state_path(config).read_text(encoding="utf-8"))
    assert document["complete"] is True and document["students"] == [{"name": "Kept", "owned": True}]


# Words as read live from Saki (Swimsuit)'s page (2026-10-14), not yet recruited.
SAKI = [Word("Basic Info", 1.0, (748, 138, 854, 162)), Word("Profile", 1.0, (1037, 136, 1113, 163)),
        Word("Stats", 1.0, (687, 203, 741, 228)), Word("View MAX Level Info", .98, (1020, 204, 1189, 226)),
        Word("0/120", .92, (759, 575, 843, 614)), Word("Exchange Eleph", 1.0, (977, 576, 1176, 611)),
        Word("SRT Academy | STRIKER", .95, (34, 595, 263, 620)),
        Word("Saki (Swimsuit)", 1.0, (44, 637, 310, 686))]


def test_a_student_not_yet_recruited_is_read_from_its_own_page():
    assert inventory.shown_student(SAKI) == {
        "owned": False, "name": "Saki (Swimsuit)", "identity": "saki swimsuit", "role": "STRIKER",
        "elephs": [0, 120]}
    sliding = [Word(w.text, w.confidence, (w.box[0] + 30, w.box[1], w.box[2] + 30, w.box[3]))
               if w.text == "Basic Info" else w for w in SAKI]
    assert inventory.shown_student(sliding) is None


def test_an_owned_student_is_read_by_the_name_beside_the_bond_heart():
    words = [Word("Student", 1.0, (130, 12, 215, 40)), Word("Basic Info", 1.0, (700, 129, 810, 153)),
             Word("Stats", 1.0, (690, 190, 745, 212)), Word("⑩Aris (Maid)", 1.0, (70, 556, 215, 592))]
    assert inventory.shown_student(words) == {"owned": True, "identity": "aris maid", "name": "Aris (Maid)"}


def test_manager_exports_the_inventory_as_a_json_download(tmp_path):
    from ba_automator.server import ApiError, DashboardController
    from test_server import ProcessFactory
    path = tmp_path / "config" / "local.toml"
    path.parent.mkdir()
    path.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                    'adb_path="../tools/adb"\n[checkin]\nschedule_enabled=false\n'
                    '[storage]\nrun_dir="../data/runs"\nlock_dir="../data/locks"\n', encoding="utf-8")
    controller = DashboardController(path, process_factory=ProcessFactory(), wall_clock=lambda: NOW)
    try:
        with pytest.raises(ApiError, match="No student inventory yet"):
            controller.students_export()
        inventory.write_inventory(Config.from_file(path), [
            {"name": "Aris (Maid)", "identity": "aris maid", "owned": True, "level": 85, "stars": 3,
             "profile": "C:/private/aris.png", "text": ["Basic Info"]},
            {"name": "Saki", "identity": "saki", "owned": False, "role": "SPECIAL", "elephs": [22, 120],
             "profile": "C:/private/saki.png", "text": ["Basic Info"]}], captured_at=NOW, complete=True)
        payload, filename = controller.students_export()
    finally:
        controller.close()
    export = json.loads(payload)
    assert filename == "students-2026-10-14.json"
    assert (export["count"], export["owned"], export["complete"], export["captured_at"]) == (
        2, 1, True, NOW.isoformat())
    # The download carries each student's data, never private paths or raw OCR.
    assert export["students"] == [{"name": "Aris (Maid)", "owned": True, "level": 85, "stars": 3},
                                  {"name": "Saki", "owned": False, "role": "SPECIAL", "elephs": [22, 120]}]
