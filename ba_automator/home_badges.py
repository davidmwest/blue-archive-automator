"""Fixed home notification badges, accepted only on an unobstructed home screen."""

from dataclasses import dataclass
import cv2
from .vision import classify, decode_frame
from .crafting_vision import number

# Keep the fixed hitbox and badge separate: the dot is not itself a button.
BADGE_BOUNDS = {
    "free_pack": (178, 246, 194, 263),
    "club": (575, 671, 591, 688),
    "mail": (1186, 9, 1202, 26),
    "tasks": (66, 246, 82, 263),
}
PRIORITY = tuple(BADGE_BOUNDS)


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


def badges(frame):
    return tuple(
        task for task, bounds in BADGE_BOUNDS.items() if notification_dot(frame, bounds)
    )


@dataclass(frozen=True)
class BadgeScreen:
    kind: str
    badges: tuple = ()
    ap: int | None = None


class BadgeVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        home = (
            not billing and classify(words, self.startup.matches(frame)).state == "home"
        )
        if not home:
            return BadgeScreen("unknown")
        amount = number(words, (495, 0, 615, 52), r"(\d+)/(\d+)")
        return BadgeScreen("home", badges(frame), amount[0] if amount else None)
