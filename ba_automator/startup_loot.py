"""Passive login receipts: preserve transient frames, read them after startup.

No tooltip taps, inferred subscription benefits, balance deltas, or future
calendar rewards. The caller holds the instance lock through capture and commit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
import re

import cv2

from .daily_schedule import due_day
from .loot_receipts import encode, page, read_crop, save_icon, save_result
from .vision import decode_frame, decode_native_frame, native_game_region, read_game_words

LOGGER = logging.getLogger(__name__)
MAX_FRAMES = 12
MAX_SEQUENCES = 12
SETTLE_SECONDS = 2.0
ASSETS = Path(__file__).with_name("assets")
ATTENDANCE_ICONS = {
    "credits": "Credits", "normal-report": "Normal Activity Report",
    "ap": "AP", "pyroxenes": "Pyroxenes", "advanced-report": "Advanced Activity Report",
}
# The stylized final 'a' is occasionally read as 'd'. The ten positioned day
# labels and stamp transition below still have to verify this specific board.
ATTENDANCE_HEADINGS = {"aronasattendance", "arondsattendance"}


def compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def candidate_kind(observation):
    labels = {compact(t) for t in observation.text}
    if any("expired" in t for t in labels):
        return None
    if labels & ATTENDANCE_HEADINGS and "daily" in labels:
        return "attendance"
    if labels & {"rewardacquired", "rewardsacquired"}:
        return "rewards"
    return None


def cell_box(index):
    return 479 + index % 5 * 154, 212 + index // 5 * 216, 117, 165


def attendance_state(png, vision):
    """Require the ten labeled cells and a contiguous prefix of red stamps."""
    words = read_game_words(png, vision)
    labels = {compact(w.text) for w in words if w.confidence >= .85}
    if "daily" not in labels or not labels & ATTENDANCE_HEADINGS:
        return None
    native = decode_native_frame(png)
    for i in range(10):
        x, y, w, h = cell_box(i)
        if not any(compact(word.text) == f"day{i + 1}" and word.confidence >= .85
                   and x <= word.center[0] <= x + w and y - 38 <= word.center[1] < y
                   for word in words):
            # Full-frame OCR occasionally drops a small header at 720p. Read
            # that exact cell's header again; its location is still required.
            header = read_crop(vision, native_game_region(native, (x, y - 38, x + w, y)), 3)
            if (len(header) != 1 or header[0].confidence < .95
                    or compact(header[0].text) != f"day{i + 1}"):
                return None
    image = decode_frame(png)
    stamps = []
    for i in range(10):
        x, y, w, _ = cell_box(i)
        b, g, r = image[y:y + 126, x:x + w].astype(float).transpose(2, 0, 1)
        fraction = float(((r > 140) & (r > g * 1.3) & (r > b * 1.3)).mean())
        if .14 < fraction < .24:  # A partially arriving stamp is not completion.
            return None
        stamps.append(fraction >= .24)
    count = sum(stamps)
    return count if stamps == [True] * count + [False] * (10 - count) else None


def attendance_item(png, index, vision):
    """Match unstamped artwork, then read the actual quantity twice."""
    image, native = decode_frame(png), decode_native_frame(png)
    x, y, w, h = cell_box(index)
    crop = image[y + 20:y + 112, x:x + w]
    matches = []
    for asset, name in ATTENDANCE_ICONS.items():
        template = cv2.imread(str(ASSETS / f"login-{asset}.png"))
        score = float(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)[0, 0])
        matches.append((score, name))
    matches.sort(reverse=True)
    if matches[0][0] < .94 or matches[0][0] - matches[1][0] < .08:
        return None
    # White single digits on the dark bar disappear in ordinary crop OCR.
    # Isolate the full numeric label, invert it, and require two agreeing reads.
    quantity = native_game_region(native, (x + 5, y + 134, x + 108, y + 160))
    gray = cv2.cvtColor(quantity, cv2.COLOR_BGR2GRAY)
    mask = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    mask = cv2.resize(mask, (206, 52), interpolation=cv2.INTER_CUBIC)
    quantities = []
    for scale in (1, 2):
        words = read_crop(vision, mask, scale)
        if len(words) != 1 or words[0].confidence < .95:
            return None
        text = words[0].text.strip()
        if not re.fullmatch(r"(?:[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)", text):
            return None
        quantities.append(int(text.replace(",", "")))
    if quantities[0] != quantities[1] or not 0 < quantities[0] <= 999999999:
        return None
    icon = encode(native_game_region(native, (x, y + 20, x + w, y + 112)))
    return {"name": matches[0][1], "quantity": quantities[0]}, icon


def parse_attendance(frames, vision):
    observations = [(frame, attendance_state(frame.read_bytes(), vision)) for frame in frames]
    valid = [(frame, count) for frame, count in observations if count is not None]
    if not valid:
        return [], False, frames[-1], "Attendance layout was unreadable; no reward inferred."
    first, before = valid[0]
    final, after = valid[-1]
    if after != before + 1 or any(count not in (before, after) for _, count in valid):
        return [], False, final, "No single new attendance stamp was captured; no past or future days counted."
    # Keep the latest unobstructed picture of the newly stamped cell.
    first = next(frame for frame, count in reversed(valid) if count == before)
    result = attendance_item(first.read_bytes(), before, vision)
    if result is None:
        return [], False, final, f"Attendance day {after} completed, but its item or quantity was unreadable."
    item, icon = result
    return [(item, icon)], True, final, f"Daily attendance: newly completed day {after}."


def parse_rewards(frames, vision):
    """Choose one best frame; never add repeated animation frames together."""
    best = None
    for frame in reversed(frames):
        receipt = page(frame.read_bytes(), vision)
        if receipt.kind != "reward" or not receipt.cards:
            continue
        items = [({"name": card.name, "quantity": card.quantity}, card.icon)
                 for card in receipt.cards]
        complete = not receipt.clipped and all(i["name"] and i["quantity"] for i, _ in items)
        quality = (complete, sum(bool(i["name"] and i["quantity"]) for i, _ in items), len(items))
        if best is None or quality > best[0]:
            best = quality, items, complete, frame
        if complete:
            break
    if best is None:
        return [], False, frames[-1], "Login reward entrance captured without readable cards."
    _, items, complete, frame = best
    return items, complete, frame, "Daily login rewards, including any visible subscription grants."


class StartupLoot:
    """Bounded, durable frame capture; optional OCR never sends device input."""
    def __init__(self, config, run_dir, vision, journal, *, now=None):
        self.config, self.root, self.vision, self.journal = config, run_dir / "login", vision, journal
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sequences = []
        self.active = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.finish()
        return False

    def observe(self, png, observation, captured_at):
        """Return whether the first dismissal should wait one settling interval."""
        kind = candidate_kind(observation)
        if kind is None:
            self.active = None
            return False
        try:
            if self.active is None or self.active["kind"] != kind:
                if len(self.sequences) >= MAX_SEQUENCES:
                    return False
                self.active = {"kind": kind, "time": self.now().isoformat(),
                               "since": captured_at, "frames": []}
                self.sequences.append(self.active)
            sequence = self.active
            frames = sequence["frames"]
            slot = min(len(frames), MAX_FRAMES - 1)
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{len(self.sequences):02d}-{kind}-{slot:02d}.png"
            path.write_bytes(png)
            if len(frames) < MAX_FRAMES:
                frames.append(path.name)
            # Manifest permits offline recovery after an interrupted startup.
            temporary = self.root / "frames.tmp"
            temporary.write_text(json.dumps(self.sequences, indent=2))
            temporary.replace(self.root / "frames.json")
            return captured_at - sequence["since"] < SETTLE_SECONDS
        except Exception:
            LOGGER.exception("Could not preserve optional login reward evidence")
            return False

    def _recorded(self):
        records = {}
        path = self.config.state_dir / "important-actions.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("login_reward_key"):
                    records[item["login_reward_key"]] = item
        return records

    def finish(self):
        if self.closed or not self.sequences:
            return
        self.closed = True
        try:
            recorded = self._recorded()
            # Same popup can briefly disappear during animation. Merge its captures
            # across that gap, and across the one allowed startup recovery.
            groups = {}
            for sequence in self.sequences:
                timestamp = datetime.fromisoformat(sequence["time"])
                day = due_day(timestamp, delay_minutes=0)
                groups.setdefault((day, sequence["kind"]), []).append(sequence)
            for (day, kind), sequences in groups.items():
                try:
                    self._finish_group(day, kind, sequences, recorded)
                except Exception:
                    LOGGER.exception("Could not read optional login rewards; saved frames remain available")
                    self.journal.record("login_loot_unreadable", kind=kind, game_day=day,
                                        evidence=str(self.root / "frames.json"))
        except Exception:
            LOGGER.exception("Could not finalize optional login rewards")

    def _finish_group(self, day, kind, sequences, recorded):
        key = sha256(f"{self.config.serial}\0{self.config.package}\0{day}\0{kind}".encode()).hexdigest()
        previous = recorded.get(key)
        if previous and previous.get("items_complete"):
            self.journal.record("login_loot_already_recorded", kind=kind, game_day=day)
            return
        frames = [self.root / name for sequence in sequences for name in sequence["frames"]]
        parse = parse_attendance if kind == "attendance" else parse_rewards
        items, complete, frame, note = parse(frames, self.vision)
        # Enrichment keeps the original receipt identity, including after Clear.
        # A weaker later observation must not replace any already known items.
        if previous and not complete:
            return
        evidence = (Path(previous["evidence"]) if previous
                    else self.root / f"{day}-{kind}.png")
        evidence.write_bytes(frame.read_bytes())
        entries = [dict(item, icon_id=save_icon(self.config, icon)) for item, icon in items]
        save_result(self.config, evidence, entries, complete, task="restart", note=note,
                    login_reward_key=key,
                    received_at=previous["time"] if previous else sequences[0]["time"])
        self.journal.record("login_loot_recorded", kind=kind, game_day=day,
                            items=entries, items_complete=complete, evidence=str(evidence), note=note)
