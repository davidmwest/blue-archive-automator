"""Fixed English Tasks controls and the home notification dot; no runtime AI."""

from dataclasses import dataclass
import re
import cv2

from .crafting_vision import bright, has, yellow, within
from .shop_vision import text_in
from .vision import classify, decode_frame

HOME_TASKS = (50, 234)
ALL_TAB = (571, 111)
HOME_BUTTON = (1237, 23)


@dataclass(frozen=True)
class TaskRewardsScreen:
    kind: str
    target: tuple[int, int] | None = None
    red_dot: bool = False
    claim: str | None = None
    items: tuple = ()
    positions: tuple = ()


def notification_dot(frame, bounds=(66, 246, 82, 263)):
    """A compact saturated red/orange component at the Tasks badge position."""
    x1, y1, x2, y2 = bounds
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    mask = (
        ((hsv[:, :, 0] <= 18) | (hsv[:, :, 0] >= 172))
        & (hsv[:, :, 1] >= 180)
        & (hsv[:, :, 2] >= 190)
    ).astype("uint8")
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    return any(
        12 <= stats[i, cv2.CC_STAT_AREA] <= 90
        and 4 <= stats[i, cv2.CC_STAT_WIDTH] <= 11
        and 4 <= stats[i, cv2.CC_STAT_HEIGHT] <= 11
        and 4 <= centers[i][0] <= 11
        and 4 <= centers[i][1] <= 12
        for i in range(1, count)
    )


def classify_task_rewards(frame, words, *, home=False):
    if frame.shape[:2] != (720, 1280):
        return TaskRewardsScreen("unknown")
    if (
        has(words, "reward acquired", (300, 120, 980, 210))
        and has(words, "touch to continue", (380, 590, 900, 665))
        and yellow(frame, (375, 134, 901, 182))
    ):
        items, positions = [], []
        for word in sorted(
            within(words, (100, 427, 1180, 468)), key=lambda w: w.center[0]
        ):
            match = re.fullmatch(r"[x×]([\d,]+)", word.text.strip())
            x, _ = word.center
            # Discard cropped edge cards; a later overlapping pan reads them whole.
            if not match or not 175 <= x <= 1105:
                continue
            name = text_in(words, (x - 74, 249, x + 74, 306)).strip()
            if name:
                items.append({"name": name, "quantity": int(match[1].replace(",", ""))})
                positions.append(x)
        return TaskRewardsScreen(
            "receipt", (640, 631), items=tuple(items), positions=tuple(positions)
        )
    if home and has(words, "tasks", (20, 244, 85, 277)):
        return TaskRewardsScreen("home", red_dot=notification_dot(frame))
    tabs = (
        ("all", (520, 82, 620, 139)),
        ("daily", (660, 82, 785, 139)),
        ("weekly", (810, 82, 932, 139)),
        ("achievement", (953, 82, 1110, 139)),
        ("challenges", (1110, 82, 1260, 139)),
    )
    if not (
        has(words, "tasks", (90, 0, 230, 48))
        and bright(frame, (250, 7, 350, 30))
        and all(has(words, name, box) for name, box in tabs)
    ):
        return TaskRewardsScreen("unknown")
    # The selected tab has yellow text on navy. Require All before claiming.
    if not yellow(frame, (559, 102, 584, 117)):
        return TaskRewardsScreen("tasks_other", target=ALL_TAB)
    if has(words, "claim all", (1070, 641, 1230, 698)) and yellow(
        frame, (1070, 641, 1230, 698)
    ):
        return TaskRewardsScreen("tasks", (1150, 670), claim="all")
    if (
        has(words, "claim", (921, 638, 1020, 698))
        and has(words, "complete at least 8 daily task s", (495, 630, 765, 680))
        and yellow(frame, (935, 644, 1010, 690))
    ):
        return TaskRewardsScreen("tasks", (974, 671), claim="daily completion")
    # An unreadable or transitional footer is not proof that collection is done.
    if has(words, "claim all", (1070, 641, 1230, 698)):
        return TaskRewardsScreen("tasks_empty")
    return TaskRewardsScreen("unknown")


class TaskRewardsVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        home = classify(words, self.startup.matches(frame)).state == "home"
        return classify_task_rewards(frame, words, home=home)
