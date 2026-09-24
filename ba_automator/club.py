"""One verified Social → Club visit per game day, with mail handled afterward."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from uuid import uuid4

from .actions import record_action
from .crafting_vision import bright, cyan, has
from .home_badges import badges
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .home_badges import notification_dot
from .vision import classify, decode_frame

RESET_HOUR_UTC = 19


def game_day(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Club game day requires a timezone-aware timestamp")
    return (
        (now.astimezone(timezone.utc) - timedelta(hours=RESET_HOUR_UTC))
        .date()
        .isoformat()
    )


def state_path(config):
    identity = hashlib.sha256(
        f"{config.serial}\0{config.package}".encode()
    ).hexdigest()[:24]
    return config.state_dir / f"club-{identity}.json"


def last_visit(config):
    try:
        value = json.loads(state_path(config).read_text())
    except FileNotFoundError:
        return None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("game_day"), str)
    ):
        raise RuntimeError(
            "Invalid Club attendance state; inspect the local state file"
        )
    return value["game_day"]


def save_visit(config, day, reward_seen):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w") as stream:
            json.dump(
                {"version": 1, "game_day": day, "reward_notice_seen": reward_seen},
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ClubScreen:
    kind: str
    target: tuple | None = None
    red_dot: bool = False


def classify_club(frame, words, *, home=False):
    if frame.shape[:2] != (720, 1280):
        return ClubScreen("unknown")
    if (
        has(words, "club attendance reward", (400, 130, 850, 190))
        and has(words, "claim rewards from mailbox", (440, 380, 840, 435))
        and has(words, "x10", (590, 315, 680, 365))
        and has(words, "confirm", (530, 455, 750, 530))
        and cyan(frame, (535, 460, 745, 520))
    ):
        return ClubScreen("club_reward", (640, 490))
    if (
        has(words, "social", (590, 170, 800, 245))
        and has(words, "club", (180, 300, 340, 355))
        and has(words, "friends", (500, 300, 720, 355))
        and has(words, "assistant", (860, 300, 1090, 355))
        and bright(frame, (200, 365, 410, 410))
    ):
        return ClubScreen(
            "social", (310, 383), notification_dot(frame, (459, 292, 475, 309))
        )
    if (
        has(words, "club", (95, 0, 200, 50))
        and has(words, "chat", (645, 75, 750, 135))
        and has(words, "member list", (970, 75, 1145, 135))
        and has(words, "club id", (45, 230, 150, 285))
        and bright(frame, (250, 7, 350, 30))
    ):
        return ClubScreen("club", (1237, 23))
    if home:
        return ClubScreen("home", (548, 659), "club" in badges(frame))
    return ClubScreen("unknown")


class ClubVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        home = (
            not billing and classify(words, self.startup.matches(frame)).state == "home"
        )
        return classify_club(frame, words, home=home)


class ClubRunner(ShopRunner):
    task = "club"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", ClubVision(startup))
        super().__init__(config, device, startup, **kwargs)

    def finish_visit(self, frame, day):
        reward_seen = frame.screen.kind == "club_reward"
        if reward_seen:
            self.journal.save_image("attendance.png", frame.capture.png)
            record_action(
                self.config,
                "club_attendance",
                "Club attendance verified: 10 AP sent to mail",
                task="club",
                game_day=day,
                evidence=str(self.run_dir / "attendance.png"),
            )
            self.tap(frame, frame.screen.target, "Acknowledge Club attendance reward")
        frame = self.wait("club")
        self.sleep(1)
        frame = self.wait("club")
        self.journal.save_image("club.png", frame.capture.png)
        # A visit proves attendance was checked, not that a reward was received.
        save_visit(self.config, day, reward_seen)
        if not reward_seen:
            record_action(
                self.config,
                "club_checked",
                "Club visited; no new attendance receipt shown",
                task="club",
                game_day=day,
            )
        self.tap(frame, frame.screen.target, "Return home from Club")
        self.home()
        return self.finish()

    def run(self):
        day = game_day(self.wall_clock())
        frame = self.wait("home")
        if last_visit(self.config) == day:
            self.phase("Club already checked this game day")
            return self.finish()
        if not frame.screen.red_dot:
            self.sleep(1)
            frame = self.wait("home")
            if not frame.screen.red_dot:
                self.phase("No Social notification; Club skipped")
                return self.finish()
        frame = self.navigate("home", "social", (548, 659))
        if not frame.screen.red_dot:
            self.tap(frame, (640, 575), "Dismiss Social: Club has no notification")
            self.home()
            self.phase("Social notification belongs to another feature; Club skipped")
            return self.finish()
        self.tap(frame, frame.screen.target, "Open Club attendance")
        return self.finish_visit(self.wait({"club", "club_reward"}), day)


def run_club(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = ClubRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
