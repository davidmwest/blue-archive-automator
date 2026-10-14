"""The student inventory reads each roster card and Basic Info once, and only reads."""

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
    """Home, a roster of pages of up to six cards, and Basic Info pages."""

    def __init__(self, names, *, per_page=6, keeps_position=True):
        self.names, self.per_page, self.keeps_position = names, per_page, keeps_position
        self.screen, self.page, self.profile, self.all = "home", 0, None, False
        self.taps, self.scrolls = [], 0

    def entries(self):
        start = self.page * 3                     # each drag moves half a page: overlap
        shown = self.names[start:start + self.per_page]
        return [{"identity": name.lower(), "name": name, "level": 80 + index,
                 "target": (140 + 196 * (index % 6), 300)} for index, name in enumerate(shown, start)]

    def last_page(self):
        return max(0, (len(self.names) - self.per_page + 2) // 3)

    def tap(self, x, y, **kwargs):
        target = (x, y)
        self.taps.append(target)
        if target == inventory.HOME:
            self.screen = "home"
        elif self.screen == "home" and target == inventory.STUDENTS_BUTTON:
            self.screen = "roster"
        elif self.screen == "roster" and target == inventory.ALL_TAB:
            self.all = True
        elif self.screen == "roster":
            entry = next(e for e in self.entries() if e["target"] == target)
            self.screen, self.profile = "profile", entry["identity"]
        elif self.screen == "profile" and target == inventory.BACK:
            self.screen, self.profile = "roster", None
            if not self.keeps_position:
                self.page = 0
        else:
            raise AssertionError((self.screen, target))
        return True

    def swipe(self, start, end, **kwargs):
        self.scrolls += 1
        if end[1] < start[1]:
            self.page = min(self.last_page(), self.page + 1)
        else:
            self.page = max(0, self.page - 1)
        return True


def runner_for(game, config, monkeypatch):
    runner = inventory.StudentInventoryRunner(
        config, SimpleNamespace(tap=game.tap, swipe=game.swipe, foreground_package=lambda: config.package),
        None, wall_clock=lambda: NOW)
    now = [0.0]
    runner.clock = lambda: now[0]
    runner.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    runner.started = 0.0

    def capture(*, ocr=False):
        return (now[0], b"png", {"screen": game.screen, "page": game.page, "profile": game.profile},
                game.entries() if game.screen == "roster" else [])
    runner.capture = capture
    runner.journal.save_image = lambda name, png: None
    monkeypatch.setattr(inventory, "roster_screen", lambda frame, words: frame["screen"] == "roster")
    monkeypatch.setattr(inventory, "_all_selected", lambda frame: game.all)
    monkeypatch.setattr(inventory, "_home", lambda runner, cap: cap[2]["screen"] == "home")
    monkeypatch.setattr(inventory, "roster_entries", lambda words: words)
    monkeypatch.setattr(inventory, "profile_identity", lambda words: game.profile)
    monkeypatch.setattr(inventory, "profile_rarity", lambda frame, words, student: 3)
    monkeypatch.setattr(inventory, "page_text", lambda words: ["Basic Info"])
    monkeypatch.setattr(inventory, "profile_settled", lambda words: True)
    monkeypatch.setattr(inventory, "parse_basic_info", lambda png, startup, words: {
        "name": game.profile, "stars": 3, "level": None, "bond": 20, "skills": {"ex": "MAX"}})
    monkeypatch.setattr(inventory, "_stationary", lambda before, after: before[2]["page"] == after[2]["page"])

    def scroll(runner, cap, *, down):
        game.swipe((630, 595) if down else (630, 290), (630, 335) if down else (630, 600))
        return runner.capture(ocr=True)
    monkeypatch.setattr(inventory, "_scroll", scroll)
    monkeypatch.setattr(inventory, "_wait", lambda runner, predicate, message, timeout=30: _wait(runner, predicate, message))
    return runner


def _wait(runner, predicate, message):
    for _ in range(5):
        cap = runner.capture(ocr=True)
        if predicate(cap):
            return cap
    runner.fail(message)


NAMES = [f"Student{n:02d}" for n in range(1, 15)]


@pytest.mark.parametrize("keeps_position", [True, False])
def test_every_student_is_read_once_and_the_job_returns_home(config, monkeypatch, keeps_position):
    game = Game(NAMES, keeps_position=keeps_position)
    runner = runner_for(game, config, monkeypatch)
    monkeypatch.setattr(inventory, "HOME_STABLE_SECONDS", 0)
    try:
        result = runner.run()
    finally:
        runner.journal.close()
    assert result.status == "success" and game.screen == "home"
    document = json.loads(inventory.state_path(config).read_text(encoding="utf-8"))
    assert [student["name"] for student in document["students"]] == NAMES
    assert all(student["stars"] == 3 for student in document["students"])
    assert document["students"][0]["level"] == 80
    # Read only: Students, All, each card, back, and finally home. Nothing else.
    allowed = {inventory.STUDENTS_BUTTON, inventory.ALL_TAB, inventory.BACK, inventory.HOME}
    assert all(target in allowed or target[1] == 300 for target in game.taps)


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
            {"name": "Aris (Maid)", "identity": "aris maid", "level": 85, "stars": 3,
             "profile": "C:/private/aris.png", "text": ["Basic Info"]}], captured_at=NOW, complete=True)
        payload, filename = controller.students_export()
    finally:
        controller.close()
    export = json.loads(payload)
    assert filename == "students-2026-10-14.json"
    assert (export["count"], export["complete"], export["captured_at"]) == (1, True, NOW.isoformat())
    # The download carries each student's data, never private paths or raw OCR.
    assert export["students"] == [{"name": "Aris (Maid)", "level": 85, "stars": 3}]
