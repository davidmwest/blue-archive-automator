"""Inventory every owned student: roster card, Basic Info, and evidence.

Read-only: from home it opens Students, shows All, and walks the roster from
its first card to its last. Each card gives a name and level; each student's
Basic Info adds the star rarity, a screenshot, and the page's text for later
reading. It never opens an upgrade control, and returns home at the end.

The result is written to the state directory as ``students-<id>.json`` and a
``.csv`` beside it, replacing the previous inventory only when a whole pass
finished.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import logging
import os
import re
import time
from uuid import uuid4

from .actions import record_action
from .ap_state import state_path as ap_path
from .fileops import replace_file
from .invitation_roster import (_all_selected, _home, _name_parts, _scroll, _stationary, _wait, _words,
                                profile_identity, profile_rarity, roster_screen)
from .locking import InstanceLock
from .runtime import FRAME_MAX_AGE, HOME_STABLE_SECONDS, Journal, RunResult, TaskError
from .student_profile import parse_basic_info
from .vision import decode_frame, read_game_words

LOGGER = logging.getLogger(__name__)
VISIT_SECONDS = 5400
VISIT_INPUTS = 2000
MAX_PAGES = 60
STUDENTS_BUTTON = (323, 659)
ALL_TAB = (445, 115)
BACK = (55, 25)
HOME = (1237, 24)


def state_path(config):
    return ap_path(config).with_name("students-" + ap_path(config).name[3:])


def roster_entries(words):
    """Fully visible owned cards: name, level and the portrait to tap.

    The geometry is the invitation roster's: each card's level anchors the
    name printed under it, and the faded bottom row is left for the next page.
    """
    entries = []
    for level in _words(words, (50, 220, 1220, 610)):
        match = re.fullmatch(r"lv\.?\s*(\d{1,2})", level.text.strip(), re.I)
        if not match:
            continue
        column = round((level.center[0] - 140) / 196)
        if column not in range(6):
            continue
        left, right = 42 + column * 196, 238 + column * 196
        if not left < level.center[0] < right:
            continue
        parts = _name_parts(words, (left, level.box[3] + 4, right, min(level.box[3] + 83, 660)))
        if not parts or any(re.match(r"^(?:lv|level)(?: |$)", word.normalized) for word in parts):
            continue
        identity = " ".join(word.normalized for word in parts)
        if identity:
            entries.append({"identity": identity, "name": " ".join(word.text.strip() for word in parts),
                            "level": int(match[1]), "target": (140 + column * 196, level.center[1] - 75)})
    return entries


def page_text(words):
    """The profile's own text in reading order, without the account's top bar."""
    body = [word for word in words if word.center[1] > 48 and word.text.strip()]
    return [word.text.strip() for word in sorted(body, key=lambda w: (round(w.center[1] / 12), w.center[0]))]


def profile_settled(words):
    """Basic Info slides and zooms in; read it only once its tab is at rest.

    Live, three of 29 profiles were captured mid-animation, every field offset.
    """
    return any(word.normalized == "basic info" and abs(word.center[0] - 755) <= 4
               and abs(word.center[1] - 141) <= 4 for word in words)


COLUMNS = ("name", "level", "stars", "bond", "role", "class", "attack", "position", "armor",
           "ex", "basic", "enhanced", "sub", "weapon_type", "weapon_level", "weapon_stars",
           "gear1_tier", "gear1_level", "gear2_tier", "gear2_level", "gear3_tier", "gear3_level",
           "exclusive_gear_tier", "hp", "atk", "def", "healing", "exp", "profile")


def flat(student):
    row = {key: student.get(key) for key in ("name", "level", "stars", "bond", "role", "class",
                                              "attack", "position", "armor", "exclusive_gear_tier",
                                              "profile")}
    row.update((student.get("skills") or {}))
    row.update((student.get("stats") or {}))
    weapon = student.get("weapon") or {}
    row.update(weapon_type=weapon.get("type"), weapon_level=weapon.get("level"), weapon_stars=weapon.get("stars"))
    for slot, gear in enumerate(student.get("gear") or [], 1):
        row[f"gear{slot}_tier"], row[f"gear{slot}_level"] = gear.get("tier"), gear.get("level")
    exp = student.get("exp")
    row["exp"] = f"{exp[0]}/{exp[1]}" if exp else None
    return ["" if row.get(column) is None else row[column] for column in COLUMNS]


def write_inventory(config, students, *, captured_at, complete):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "captured_at": captured_at.isoformat(), "complete": complete,
                "count": len(students), "students": students}
    stream = io.StringIO()
    table = csv.writer(stream, lineterminator="\n")
    table.writerow(COLUMNS)
    for student in students:
        table.writerow(flat(student))
    for target, content in ((path, json.dumps(document, indent=2, ensure_ascii=False) + "\n"),
                            (path.with_suffix(".csv"), stream.getvalue())):
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            replace_file(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    return path


class StudentInventoryRunner:
    task = "students"

    def __init__(self, config, device, startup, *, monotonic=time.monotonic, sleep=time.sleep,
                 wall_clock=lambda: datetime.now(timezone.utc)):
        self.config, self.device, self.startup = config, device, startup
        self.clock, self.sleep, self.wall_clock = monotonic, sleep, wall_clock
        self.started, self.actions, self.last_frame = monotonic(), 0, None
        stamp = wall_clock().strftime("%Y%m%dT%H%M%S")
        self.run_dir = config.run_dir / f"students-{stamp}-{uuid4().hex[:8]}"
        self.journal = Journal(self.run_dir, monotonic, self.started)

    def fail(self, message):
        raise TaskError(message, self.run_dir)

    def phase(self, detail):
        LOGGER.info("students: %s", detail)
        self.journal.record("state", state="students", detail=detail, frame=self.last_frame)

    def capture(self, *, ocr=False):
        if self.clock() - self.started > VISIT_SECONDS or self.actions >= VISIT_INPUTS:
            self.fail("Student inventory reached its time or input limit")
        captured = self.clock()
        png = self.device.screenshot()
        frame = decode_frame(png)
        self.last_frame = self.journal.screenshot(png)
        if self.device.foreground_package() != self.config.package:
            self.fail("Blue Archive left the foreground during the student inventory; no input sent")
        return captured, png, frame, read_game_words(png, self.startup) if ocr else []

    def tap(self, capture, target, detail):
        captured = capture[0]
        self.journal.record("intent", operation="tap", task=self.task, detail=detail,
                            target=list(target), frame=self.last_frame)
        sent = self.device.tap(*target, deadline=captured + FRAME_MAX_AGE, monotonic=self.clock)
        self.journal.record("outcome", operation="tap", result="ok" if sent else "skipped_stale")
        if not sent:
            self.fail("Student inventory frame expired before input")
        self.actions += 1
        self.sleep(.7)

    def roster(self, timeout=30):
        return _wait(self, lambda cap: roster_screen(cap[2], cap[3]), "Student roster was not recognized", timeout)

    def top_of_roster(self, cap):
        stationary = 0
        for _ in range(MAX_PAGES):
            after = _scroll(self, cap, down=False)
            stationary = stationary + 1 if _stationary(cap, after) else 0
            cap = after
            if stationary >= 2:
                return cap
        self.fail("Student roster beginning was not reached within the bounded scan")

    def page_with(self, identity):
        """Return to the roster page showing ``identity`` after a profile visit."""
        cap = self.roster()
        if any(entry["identity"] == identity for entry in roster_entries(cap[3])):
            return cap
        self.journal.record("student_roster_position_lost", student=identity)
        cap = self.top_of_roster(cap)
        stationary = 0
        for _ in range(MAX_PAGES):
            if any(entry["identity"] == identity for entry in roster_entries(cap[3])):
                return cap
            after = _scroll(self, cap, down=True)
            stationary = stationary + 1 if _stationary(cap, after) else 0
            cap = after
            if stationary >= 2:
                break
        self.fail(f"{identity} could not be found again in the roster")

    def inspect(self, cap, entry, index):
        """Open one Basic Info, keep its evidence and stars, and come back."""
        record = {"name": entry["name"], "identity": entry["identity"], "level": entry["level"],
                  "stars": None, "profile": None, "text": []}
        self.tap(cap, entry["target"], f"Open {entry['name']}'s Basic Info")
        try:
            profile = _wait(self, lambda value: (profile_identity(value[3]) == entry["identity"]
                                                 and profile_settled(value[3])),
                            f"{entry['name']}'s Basic Info did not open", 20)
        except TaskError as error:
            record["error"] = str(error)
        else:
            fields = parse_basic_info(profile[1], self.startup, profile[3])
            record.update({key: value for key, value in fields.items() if key != "name"})
            record["level"] = fields["level"] or entry["level"]
            if record["stars"] is None:
                record["stars"] = profile_rarity(profile[2], profile[3], entry["identity"])
            name = f"students/{index:03d}-{re.sub(r'[^a-z0-9]+', '-', entry['identity']).strip('-')}.png"
            self.journal.save_image(name, profile[1])
            record["profile"] = str(self.run_dir / name)
            record["text"] = page_text(profile[3])
        back = self.capture()
        self.tap(back, BACK, "Return to the student roster")
        return record, self.page_with(entry["identity"])

    def run(self):
        cap = _wait(self, lambda value: _home(self, value), "Student inventory requires an unobstructed home screen")
        self.phase("Open Students")
        self.tap(cap, STUDENTS_BUTTON, "Open Students from verified home")
        cap = self.roster()
        self.tap(cap, ALL_TAB, "Show All owned students")
        cap = _wait(self, lambda value: roster_screen(value[2], value[3]) and _all_selected(value[2]),
                    "The All students tab could not be verified")
        cap = self.top_of_roster(cap)
        students, seen, stationary = [], set(), 0
        for _ in range(MAX_PAGES):
            for entry in roster_entries(cap[3]):
                if entry["identity"] in seen:
                    continue
                seen.add(entry["identity"])
                self.phase(f"Student {len(students) + 1}: {entry['name']}, Lv.{entry['level']}")
                record, cap = self.inspect(cap, entry, len(students) + 1)
                students.append(record)
            after = _scroll(self, cap, down=True)
            stationary = stationary + 1 if _stationary(cap, after) else 0
            cap = after
            if stationary >= 2:
                break
        else:
            self.fail("Student roster end was not reached within the bounded scan")
        if not students:
            self.fail("No owned students were read from the roster")
        path = write_inventory(self.config, students, captured_at=self.wall_clock(), complete=True)
        stars = sum(1 for student in students if student["stars"])
        record_action(self.config, "students_inventoried",
                      f"Inventoried {len(students)} students; star rarity read for {stars}.",
                      task=self.task, count=len(students), path=str(path))
        self.phase(f"Inventoried {len(students)} students")
        self.tap(self.capture(), HOME, "Return home after the student inventory")
        end, since, count = self.clock() + 45, None, 0
        while self.clock() < end:
            cap = self.capture(ocr=True)
            if _home(self, cap) and self.clock() - cap[0] < FRAME_MAX_AGE - 1:
                since = self.clock() if since is None else since
                count += 1
                if self.clock() - since >= HOME_STABLE_SECONDS and count >= self.config.home_confirmations:
                    self.journal.record("finished", status="success", actions=self.actions)
                    return RunResult("success", self.run_dir, self.clock() - self.started, self.actions)
            else:
                since, count = None, 0
            self.sleep(.6)
        self.fail("Home was not stable after the student inventory")


def run_student_inventory(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = StudentInventoryRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except Exception as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
