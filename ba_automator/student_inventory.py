"""Inventory every owned student's Basic Info.

Read-only: from home it opens Students, shows All, opens the first card, and
steps through the students with the page's next arrow. Live, the arrow visits
owned students only, in the list's order, and wraps from the last back to the
first, which ends the walk. Each Basic Info is read into fields (see
``student_profile``); should a student not yet recruited be shown, it is
recorded with its eleph count. Every page is kept as a screenshot with its
text, no upgrade control is ever opened, and it returns home at the end.

The result is written to the state directory as ``students-<id>.json`` and a
``.csv`` beside it. A walk that stops early keeps what it read only when no
complete inventory would be replaced.
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
MAX_STUDENTS = 400
NEXT = (1253, 384)          # the arrow at the right edge of every student's page
NEXT_ATTEMPTS = 3
FIRST_CARD_X = 140
STUDENTS_BUTTON = (323, 659)
ALL_TAB = (445, 115)
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


def owned_identity(words):
    """An owned student's settled Basic Info, by the name beside the bond heart."""
    identity = profile_identity(words)
    return identity if identity and profile_settled(words) else None


def unowned_page(words):
    """A student not yet recruited: Basic Info and Profile tabs and an eleph count.

    Live, the All list holds every student in the game; the ones not owned show
    as dimmed Lv.1 cards and open this page, its name large at the bottom left
    under a "School | ROLE" line.
    """
    if not any(word.normalized == "basic info" and abs(word.center[0] - 800) <= 4
               and abs(word.center[1] - 150) <= 4 for word in words):
        return None
    if not any(word.normalized == "profile" and 1000 <= word.center[0] <= 1150
               and 130 <= word.center[1] <= 170 for word in words):
        return None
    parts = _name_parts(words, (40, 640, 420, 690))
    if not parts:
        return None
    count = re.fullmatch(r"(\d+)\s*/\s*(\d+)", " ".join(word.text.strip() for word in _words(words, (740, 575, 900, 615))))
    role = re.search(r"(STRIKER|SPECIAL)$", " ".join(word.text.strip() for word in _words(words, (30, 592, 430, 622))), re.I)
    return {"name": " ".join(word.text.strip() for word in parts),
            "identity": " ".join(word.normalized for word in parts),
            "role": role[1].upper() if role else None,
            "elephs": [int(count[1]), int(count[2])] if count else None}


def shown_student(words):
    """The student whose page is at rest, owned or not yet recruited, else None."""
    identity = owned_identity(words)
    if identity:
        # The bond heart beside the name is sometimes read as a stray symbol.
        name = re.sub(r"^[^A-Za-z]+", "", " ".join(word.text.strip()
                                                   for word in _name_parts(words, (70, 545, 255, 592))))
        return {"owned": True, "identity": identity, "name": name or identity}
    unowned = unowned_page(words)
    return {"owned": False, **unowned} if unowned else None


COLUMNS = ("name", "owned", "level", "stars", "bond", "role", "class", "attack", "position", "armor",
           "ex", "basic", "enhanced", "sub", "weapon_type", "weapon_level", "weapon_stars",
           "gear1_tier", "gear1_level", "gear2_tier", "gear2_level", "gear3_tier", "gear3_level",
           "exclusive_gear_tier", "hp", "atk", "def", "healing", "exp", "elephs", "profile")


def flat(student):
    row = {key: student.get(key) for key in ("name", "owned", "level", "stars", "bond", "role", "class",
                                              "attack", "position", "armor", "exclusive_gear_tier",
                                              "profile")}
    elephs = student.get("elephs")
    row["elephs"] = f"{elephs[0]}/{elephs[1]}" if elephs else None
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


def reparse_inventory(config, startup):
    """Re-read every saved Basic Info screenshot with the current parser.

    Sends no game input: it only updates the saved inventory, so reading
    improvements reach an existing inventory without another roster walk.
    """
    from pathlib import Path
    path = state_path(config)
    document = json.loads(path.read_text(encoding="utf-8"))
    for student in document["students"]:
        profile = student.get("profile")
        if student.get("owned") is False or not profile or not Path(profile).is_file():
            continue
        fields = parse_basic_info(Path(profile).read_bytes(), startup)
        roster_level = student.get("level")
        student.update({key: value for key, value in fields.items() if key != "name"})
        student["level"] = fields["level"] or roster_level
    return write_inventory(config, document["students"],
                           captured_at=datetime.fromisoformat(document["captured_at"]),
                           complete=document["complete"])


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

    def student_page(self, previous, timeout=20):
        """The settled page of a student other than ``previous``, read alike twice running.

        Two matching reads keep a page caught mid-transition, or a name misread
        once, from being taken for another student.
        """
        end, last = self.clock() + timeout, None
        while self.clock() < end:
            cap = self.capture(ocr=True)
            page = shown_student(cap[3]) if self.clock() - cap[0] < FRAME_MAX_AGE - 1 else None
            if page and page["identity"] != previous:
                if last == page["identity"]:
                    return page, cap
                last = page["identity"]
            else:
                last = None
            self.sleep(.4)
        return None, None

    def showing(self, identity, timeout=12):
        """A fresh frame of ``identity``'s page to tap from, or a failure if it is gone."""
        end = self.clock() + timeout
        while self.clock() < end:
            cap = self.capture(ocr=True)
            page = shown_student(cap[3])
            if page and page["identity"] == identity and self.clock() - cap[0] < FRAME_MAX_AGE - 1.5:
                return cap
            self.sleep(.4)
        self.fail(f"{identity}'s page was no longer showing before the next student")

    def record(self, page, cap, index):
        image = f"students/{index:03d}-{re.sub(r'[^a-z0-9]+', '-', page['identity']).strip('-')}.png"
        self.journal.save_image(image, cap[1])
        if page["owned"]:
            fields = parse_basic_info(cap[1], self.startup, cap[3])
            record = {"identity": page["identity"], "owned": True, **fields}
            record["name"] = fields["name"] or page["identity"]
            if record["stars"] is None:
                record["stars"] = profile_rarity(cap[2], cap[3], page["identity"])
        else:
            record = {"name": page["name"], "identity": page["identity"], "owned": False,
                      "role": page["role"], "elephs": page["elephs"]}
        record["profile"] = str(self.run_dir / image)
        record["text"] = page_text(cap[3])
        return record

    def next_student(self, cap, previous):
        """Tap the page's next arrow; None when the arrow no longer moves on.

        The tap is sent from a frame that still shows ``previous``: the one just
        read when it is fresh enough, otherwise a new one.
        """
        for attempt in range(NEXT_ATTEMPTS):
            if cap is None or self.clock() - cap[0] >= FRAME_MAX_AGE - 1.5:
                cap = self.showing(previous)
            self.tap(cap, NEXT, "Show the next student")
            page, after = self.student_page(previous)
            if page is not None:
                return page, after
            cap = None
            self.journal.record("student_next_unchanged", student=previous, attempt=attempt + 1)
        self.showing(previous)
        return None, None

    def keep_partial(self, students):
        """Save what a stopped walk read, without replacing a complete inventory."""
        if not students:
            return
        try:
            kept = json.loads(state_path(self.config).read_text(encoding="utf-8")).get("complete")
        except (OSError, ValueError, AttributeError):
            kept = False
        if kept:
            self.journal.record("student_inventory_partial_not_saved", count=len(students))
            return
        write_inventory(self.config, students, captured_at=self.wall_clock(), complete=False)
        self.journal.record("student_inventory_partial_saved", count=len(students))

    def run(self):
        cap = _wait(self, lambda value: _home(self, value), "Student inventory requires an unobstructed home screen")
        self.phase("Open Students")
        self.tap(cap, STUDENTS_BUTTON, "Open Students from verified home")
        cap = self.roster()
        self.tap(cap, ALL_TAB, "Show All students")
        cap = _wait(self, lambda value: roster_screen(value[2], value[3]) and _all_selected(value[2]),
                    "The All students tab could not be verified")
        cap = self.top_of_roster(cap)
        entries = roster_entries(cap[3])
        if not entries:
            self.fail("No student card could be read at the top of the roster")
        # The first card is the top row's leftmost, even when its own level is misread.
        first = (FIRST_CARD_X, min(entry["target"][1] for entry in entries))
        self.tap(cap, first, "Open the first student in the All list")
        page, cap = self.student_page(None)
        if page is None:
            self.fail("The first student's page did not open")
        self.phase(f"Student 1: {page['name']}" + ("" if page["owned"] else " (not recruited)"))
        # The page's arrow steps through the students in the list's order, so the
        # walk never scrolls or returns to the list. It ends back at the first
        # student (live, 96 owned students in 16 minutes) or where it stops moving on.
        students, seen, complete = [self.record(page, cap, 1)], {page["identity"]}, False
        try:
            for _ in range(MAX_STUDENTS):
                page, after = self.next_student(cap, students[-1]["identity"])
                if page is None:
                    complete = True
                    self.journal.record("student_list_end", student=students[-1]["identity"])
                    break
                if page["identity"] in seen:
                    complete = page["identity"] == students[0]["identity"]
                    if not complete:
                        self.journal.record("student_order_repeated", student=page["identity"])
                    break
                seen.add(page["identity"])
                self.phase(f"Student {len(students) + 1}: {page['name']}"
                           + ("" if page["owned"] else " (not recruited)"))
                students.append(self.record(page, after, len(students) + 1))
                cap = after
        except Exception:
            try:
                self.keep_partial(students)
            except Exception:
                LOGGER.exception("The partial student inventory could not be saved")
            raise
        owned = sum(1 for student in students if student["owned"])
        path = write_inventory(self.config, students, captured_at=self.wall_clock(), complete=complete)
        summary = f"Inventoried {owned} owned students" + (
            f" and {len(students) - owned} not yet recruited" if owned < len(students) else "")
        record_action(self.config, "students_inventoried", summary + ".",
                      task=self.task, count=len(students), owned=owned, complete=complete, path=str(path))
        self.phase(summary)
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
