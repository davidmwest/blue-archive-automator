"""Read real reward cards and their tooltips using bounded, local OCR only.

Only the Final row of Sweep Complete is loot. Inventory totals in an Owned
label are never a quantity. Captures survive failure after a successful claim.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

import cv2
import numpy as np

from .actions import record_action
from .loot_icon_mask import isolate_compact_card
from .runtime import Capture, TaskError
from .vision import decode_frame

RECEIPT_TIMEOUT = 240
REWARD_RECEIPT_TIMEOUT = 900
TASK_NOTICE_TIMEOUT = 12
GRID_ENTRY_TIMEOUT = 8
GRID_RETURN_TIMEOUT = 8
TOOLTIP_RETURN_TIMEOUT = 8
# The horizontal receipt viewport ends at x=100/1180. Its four-pixel fade
# bands can make a clipped card look almost full and change width each frame.
# Discover only within the opaque interior; anything touching it is partial.
REWARD_LEFT, REWARD_RIGHT = 104, 1176


@dataclass(frozen=True)
class Card:
    box: tuple
    quantity: int | None
    name: str | None
    icon: bytes
    tier: str | None = None

    @property
    def target(self):
        x, y, w, h = self.box
        return x + w // 2, y + (24 if h < 100 else h // 2)


@dataclass(frozen=True)
class Page:
    kind: str
    cards: tuple = ()
    clipped: bool = False
    expand_target: tuple | None = None
    leading_clipped: bool = False
    trailing_clipped: bool = False


def encode(image):
    ok, png = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Could not encode reward icon")
    return png.tobytes()


def card_icon(image, box, kind):
    """Keep the complete framed artwork at its native aspect ratio.

    Tall reward cards have a separate central frame, name and quantity strip.
    Compact cards overlay their quantity on the artwork: retain that text rather
    than cut off the lower artwork to hide it. The dashboard fits without zoom.
    """
    x, y, w, h = box
    if kind == "reward":
        top, bottom = int(h * .26), int(h * .74)
        left, right = int(w * .07), int(w * .93)
    else:
        top, bottom, left, right = 0, h, 0, w
    crop = image[y + top:y + bottom, x + left:x + right]
    if kind != "reward":
        crop = isolate_compact_card(crop)
    return encode(crop)


def read_crop(vision, image, scale=3):
    large = cv2.resize(image, None, fx=scale, fy=scale)
    large = cv2.copyMakeBorder(
        large, 25, 25, 25, 25, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )
    return vision.read(large)


def amount(words):
    values = set()
    for word in words:
        # An explicit x prefix is required: never turn arbitrary icon text into loot.
        match = re.fullmatch(r"[-—_\s]*[xX×]\s*([\d,]+)", word.text.strip())
        if match and word.confidence >= 0.65:
            value = int(match[1].replace(",", ""))
            if 0 < value <= 999999999:
                values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def grid_tier(words):
    """Read only an exact, confident tier label in a compact card's badge.

    Coordinates belong to read_crop's 3x image with its 25px border. The
    artwork can contain unrelated lettering, so only the lower-left badge
    supplies this identity; neither a guessed tier nor a quantity qualifies.
    """
    labels = [w.text.strip() for w in words
              if w.confidence >= .95 and re.fullmatch(r"T[1-9][0-9]?", w.text.strip())
              and 25 <= w.box[0] < w.box[2] <= 145
              and 195 <= w.box[1] < w.box[3] <= 305]
    return labels[0] if len(labels) == 1 else None


def complete_name(name):
    """Only a complete high-confidence label may stand in for animated artwork."""
    return bool(
        name
        and 1 < len(name) <= 160
        and not name.rstrip().endswith(("-", "—"))
        and "..." not in name
        and "…" not in name
        and name.count("(") == name.count(")")
        and name.count("[") == name.count("]")
    )


def reward_name(vision, image, box):
    """Read the complete label in a full, already located reward card."""
    x, y, w, h = box
    titles = read_crop(vision, image[y + 6:y + int(h * .24), x + 5:x + w - 5])
    if not titles or any(a.confidence < .85 for a in titles):
        return None
    # Separate words on one baseline can differ by a pixel in their top edge.
    # Group by substantial vertical overlap before sorting each line left/right;
    # otherwise "Broken Quimbaya" can become "Quimbaya Broken" after a scroll.
    lines = []
    for word in sorted(titles, key=lambda a: (a.box[1], a.box[0])):
        for line in lines:
            first = line[0]
            overlap = min(word.box[3], first.box[3]) - max(word.box[1], first.box[1])
            if overlap >= .5 * min(word.box[3] - word.box[1], first.box[3] - first.box[1]):
                line.append(word)
                break
        else:
            lines.append([word])
    name = " ".join(
        word.text.strip() for line in lines for word in sorted(line, key=lambda a: a.box[0])
    )
    return name if complete_name(name) else None


def reward_quantity(vision, image, box, *, minimum_confidence=.65):
    x, y, w, h = box
    words = read_crop(vision, image[y + h - 48:y + h - 9, x + 8:x + w - 8], 2)
    return amount([word for word in words if word.confidence >= minimum_confidence])


def tooltip_boxes(image):
    boxes = []
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (75, 100, 180), (105, 255, 255))
    for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[
        0
    ]:
        x, y, w, h = cv2.boundingRect(contour)
        if not (170 <= w <= 700 and 70 <= h <= 420):
            continue
        # A tap glow can disconnect the pointer from this cyan contour. Bind
        # identity to the rectangular body, whose long bottom border stays put,
        # rather than allowing the decorative pointer to change its height.
        borders = np.flatnonzero(
            np.count_nonzero(mask[y + 65:y + h, x:x + w], axis=1) >= .9 * w
        )
        if not len(borders):
            continue
        h = 65 + int(borders[-1]) + 1
        # Side-pointing tooltips include their arrow in the contour bounds.
        # Locate the long vertical borders before checking the title stripe;
        # otherwise a left arrow shifts that stripe outside the narrow crop.
        sides = np.flatnonzero(
            np.count_nonzero(mask[y:y + h, x:x + w], axis=0) >= .85 * h
        )
        if len(sides) < 2:
            continue
        x, w = x + int(sides[0]), int(sides[-1] - sides[0]) + 1
        if not 170 <= w <= 700:
            continue
        if image[y + 8 : y + min(h - 8, 45), x + 8 : x + w - 8].mean() < 180:
            continue
        stripe = mask[y + 8 : y + h - 12, x + 10 : x + 31]
        if any(
            2 <= bw <= 9 and 12 <= bh <= 150
            for c in cv2.findContours(
                stripe, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )[0]
            for _, _, bw, bh in [cv2.boundingRect(c)]
        ):
            boxes.append((x, y, w, h))
    return tuple(sorted(boxes))


def has_tooltip(image):
    return bool(tooltip_boxes(image))


def tooltip(image, words):
    """Extract a wrapped name only inside the bright cyan-bordered item tooltip."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (75, 100, 180), (105, 255, 255))
    for x, y, w, h in tooltip_boxes(image):
        inside = [
            a for a in words if x < a.center[0] < x + w and y < a.center[1] < y + h
        ]
        owned = [a for a in inside if re.match(r"^owned\s*:", a.text, re.I)]
        if image[y + 8 : y + min(h - 8, 45), x + 8 : x + w - 8].mean() < 180:
            continue
        if len(owned) == 1:
            title_end = owned[0].box[1]
        elif not owned:
            # Currency tooltips have no inventory total. The short cyan title
            # bar stops above the dashed separator and description text.
            stripe = mask[y + 8 : y + h - 12, x + 10 : x + 31]
            bars = [
                cv2.boundingRect(c)
                for c in cv2.findContours(
                    stripe, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )[0]
            ]
            bars = [b for b in bars if 2 <= b[2] <= 9 and 12 <= b[3] <= 100]
            if len(bars) != 1:
                continue
            title_end = y + 8 + bars[0][1] + bars[0][3] + 3
        else:
            continue
        title = [a for a in inside if a.center[1] < title_end]
        if not title or any(a.confidence < 0.85 for a in title):
            continue
        name = " ".join(
            a.text.strip() for a in sorted(title, key=lambda a: (a.box[1], a.box[0]))
        )
        if 1 < len(name) <= 160:
            return name
    return None


def read_tooltip(image, vision):
    # The cyan title divider can be OCR'd as an I joined to the item name.
    # Remove decoration only from the OCR input; retain the original image for
    # tooltip boundaries and the title/description separator.
    cleaned = image.copy()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    decoration = cv2.inRange(hsv, (75, 100, 180), (105, 255, 255))
    cleaned[decoration != 0] = (255, 255, 255)
    return tooltip(image, vision.read(cleaned))


def page(png, vision):
    image = decode_frame(png)
    words = vision.read(image)
    labels = {w.normalized.replace(" ", "") for w in words}
    if "fulllist" in labels and "okay" in labels:
        kind = "grid"
    elif "sweepcomplete" in labels and "final" in labels and "confirm" in labels:
        kind, row, left, right = "sweep", 512, 370, 1087
    elif "lessonreport" in labels and "lessonreward" in labels and "confirm" in labels:
        kind, row, left, right = "lesson", 493, 430, 850
    elif "rewardacquired" in labels and (
        "touchtocontinue" in labels or image[130:185, 375:900].mean() > 70
    ):
        kind = "reward"
    else:
        return Page("unknown")
    # A tooltip must be dismissed before discovering cards underneath its shading.
    if has_tooltip(image):
        return Page("tooltip")
    cards = []
    clipped = False
    leading_clipped = trailing_clipped = False
    expand_target = None
    if kind == "sweep":
        roi = image[445:516, 1010:1074]
        ink = ((roi[:, :, 0] > roi[:, :, 2] * 1.15) & (roi.mean(axis=2) < 150)).astype(
            np.uint8
        ) * 255
        glyphs = [
            cv2.boundingRect(c)
            for c in cv2.findContours(ink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[
                0
            ]
            if 150 < cv2.contourArea(c) < 500
        ]
        glyphs = [g for g in glyphs if 26 <= g[2] <= 34 and 16 <= g[3] <= 34]
        if len(glyphs) == 1:
            x, y, w, h = glyphs[0]
            expand_target = (1010 + x + w // 2, 445 + y + h // 2)
            # Compact Final variants hide additional cards behind this control.
            return Page(kind, (), True, expand_target)
    if kind == "grid":
        roi = image[178:459, 321:959]
        mask = (np.min(roi, axis=2) > 225).astype(np.uint8) * 255
        rects = [
            cv2.boundingRect(c)
            for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[
                0
            ]
        ]
        full = [
            (x, y, w, h)
            for x, y, w, h in rects
            if 95 <= w <= 115 and 83 <= h <= 94 and y > 1 and y + h < 280
        ]
        if full:
            top = min(r[1] for r in full)
            full.sort(key=lambda r: (round((r[1] - top) / 20), r[0]))
        for x, y, w, h in full:
            x += 321
            y += 178
            crop = image[y : y + h, x : x + w]
            words = read_crop(vision, crop)
            qty = amount(words)
            if qty is None:
                qty = amount(
                    read_crop(vision, image[y + h - 27 : y + h, x + 6 : x + w - 2], 4)
                )
            cards.append(
                Card(
                    (x, y, w, h),
                    qty,
                    None,
                    card_icon(image, (x, y, w, h), kind),
                    grid_tier(words),
                )
            )
        clipped = any(
            w > 60 and h > 8 and (y <= 1 or y + h >= 280) for x, y, w, h in rects
        )
    elif kind in {"sweep", "lesson"}:
        # Standard slanted small cards have separate dark bottom shadows. Their
        # artwork is arbitrary; the border determines the hitbox, not the sprite.
        def shadow_score(y):
            line = (image[y, left:right].mean(axis=1) < 170).astype(np.uint8)[None, :]
            return sum(
                40 <= cv2.boundingRect(c)[2] <= 65
                for c in cv2.findContours(
                    line, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )[0]
            )

        candidates = range(row - 3, row + 3)
        row = max(candidates, key=lambda y: (shadow_score(y), -abs(y - row)))
        line = (image[row, left:right].mean(axis=1) < 170).astype(np.uint8)[None, :]
        segments = sorted(
            cv2.boundingRect(c)
            for c in cv2.findContours(line, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[
                0
            ]
        )
        for start, _, width, _ in segments:
            if not 40 <= width <= 65:
                continue
            x, y, w, h = left + start - 3, row - 62, width + 16, 64
            if x < left or x + w > right:
                clipped = True
                continue
            crop = image[y : y + h + 3, x : x + w]
            qty = amount(read_crop(vision, crop))
            if qty is None:
                qty = amount(
                    read_crop(vision, image[row - 22 : row + 1, x + 5 : x + w - 2], 4)
                )
            cards.append(
                Card(
                    (x, y, w, h),
                    qty,
                    None,
                    card_icon(image, (x, y, w, h), kind),
                )
            )
        # Any card intersecting the viewport edge needs another overlapping page.
        clipped |= any(
            8 < width < 40 and (start < 5 or start + width > right - left - 5)
            for start, _, width, _ in segments
        )
    else:
        # Labeled reward cards (mail, Cafe, crafting, Tasks, Tactical Challenge).
        roi = image[250:490, REWARD_LEFT:REWARD_RIGHT]
        viewport_width = REWARD_RIGHT - REWARD_LEFT
        mask = (np.min(roi, axis=2) > 180).astype(np.uint8) * 255
        rects = sorted(
            cv2.boundingRect(c)
            for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[
                0
            ]
        )
        def full_card(w, h):
            # Cards scale briefly on arrival. A clipped card retains the full
            # height but loses its portrait aspect ratio, unlike that animation.
            return 90 <= w <= 160 and 175 <= h <= 230 and .62 <= w / h <= .71

        for rx, ry, w, h in rects:
            if not full_card(w, h):
                continue
            x, y = rx + REWARD_LEFT, ry + 250
            if rx <= 1 or rx + w >= viewport_width - 1:
                clipped = True
                continue
            qty = reward_quantity(vision, image, (x, y, w, h))
            name = reward_name(vision, image, (x, y, w, h))
            cards.append(Card((x, y, w, h), qty, name, card_icon(image, (x, y, w, h), kind)))
        # A partial bright card at either edge must not be silently counted complete.
        partials = [(x, w) for x, y, w, h in rects
                    if 175 <= h <= 230 and w > 8
                    and (not full_card(w, h) or x <= 1 or x + w >= viewport_width - 1)]
        leading_clipped = any(x + w / 2 < viewport_width / 2 for x, w in partials)
        trailing_clipped = any(x + w / 2 >= viewport_width / 2 for x, w in partials)
        clipped |= bool(partials)
    return Page(kind, tuple(cards), clipped, expand_target,
                leading_clipped, trailing_clipped)


def same_icon(a, b):
    if a == b:
        return True
    first = cv2.imdecode(np.frombuffer(a, np.uint8), cv2.IMREAD_COLOR)
    second = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
    if first is None or second is None or first.shape != second.shape:
        return False
    return same_pixels(first, second)


def same_pixels(first, second):
    if first.shape != second.shape or not first.size:
        return False
    delta = np.abs(first.astype(np.float32) - second)
    # A few edge pixels shimmer after a no-op scroll. This very tight bound
    # accepts that observed raster noise, not similar artwork or translated icons.
    return float(delta.mean()) <= 0.1 and float(np.mean(delta * delta)) <= 1.0


def task_notice_visible(image):
    """Detect the transient task-progress banner only to wait without input.

    Its dark panel and gold progress strip overlap Sweep Complete's heading.
    No text or receipt identity is inferred here: once the banner leaves, the
    normal receipt parser and strict input guard still have to pass.
    """
    if getattr(image, "shape", None) != (720, 1280, 3):
        return False
    if image[5:49, 420:930].mean() >= 90:
        return False
    strip = cv2.cvtColor(image[54:76, 487:794], cv2.COLOR_BGR2HSV)
    gold = ((strip[:, :, 0] >= 16) & (strip[:, :, 0] <= 38)
            & (strip[:, :, 1] > 130) & (strip[:, :, 2] > 170))
    return float(gold.mean()) > .45


def same_grid_icon(a, b):
    """Keep compact artwork exact while a separately read tier badge pulses.

    A full white card outline supplies the existing alpha mask. Pixels outside
    that outline belong to neighboring cards and cannot identify this card.
    Every visible pixel remains strict except a badge whose Tn label was read
    independently in both captures. The quantity lies outside that badge.
    """
    first, second = [cv2.imdecode(np.frombuffer(c.icon, np.uint8), cv2.IMREAD_UNCHANGED)
                     for c in (a, b)]
    if (first is None or second is None or first.shape != second.shape
            or first.ndim != 3 or first.shape[2] != 4
            or not (83 <= first.shape[0] <= 94 and 95 <= first.shape[1] <= 115)
            or not np.array_equal(first[:, :, 3], second[:, :, 3])):
        return False
    visible = first[:, :, 3] != 0
    if a.tier is not None and a.tier == b.tier:
        visible[-30:, :40] = False
    return same_pixels(first[:, :, :3][visible], second[:, :, :3][visible])


def same_grid_view(first, second, expected, vision):
    # The neutral tooltip-dismiss point (350,545) can leave a fading pulse.
    # Keep the title/close control, entire item viewport, and Okay button.
    for x, y, w, h in ((314, 114, 650, 64), (515, 490, 255, 85)):
        if not same_pixels(first[y:y+h, x:x+w], second[y:y+h, x:x+w]):
            return False
    old, new = first[178:459, 321:959].copy(), second[178:459, 321:959].copy()
    if same_pixels(old, new):
        return True
    if vision is None:
        return False
    for card in expected.cards:
        x, y, w, h = card.box
        if card.tier is None or not (83 <= h <= 94 and 95 <= w <= 115):
            continue
        # Never carry an old tier across a changed badge. Fresh local OCR must
        # read exactly the same label before its decorative outline is ignored.
        if grid_tier(read_crop(vision, second[y:y+h, x:x+w])) != card.tier:
            return False
        rx, ry = x - 321, y + h - 30 - 178
        old[ry:ry+30, rx:rx+40] = new[ry:ry+30, rx:rx+40] = 0
    return same_pixels(old, new)


def reward_card_boxes(image):
    roi = image[250:490, REWARD_LEFT:REWARD_RIGHT]
    mask = (np.min(roi, axis=2) > 180).astype(np.uint8) * 255
    return sorted(
        (x + REWARD_LEFT, y + 250, w, h)
        for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        for x, y, w, h in [cv2.boundingRect(c)]
        if w > 8 and h >= 175
    )


def reward_layout_stable(before, after):
    """Wait for scroll rebound to stop; this never authorizes device input.

    Names, quantities, and card borders must be stationary before the expensive
    OCR pass starts. Sparkling artwork is irrelevant to motion detection. The
    parsed receipt and normal freshness guards still authorize every input.
    """
    first, second = decode_frame(before), decode_frame(after)
    boxes = reward_card_boxes(first)
    if not boxes or boxes != reward_card_boxes(second):
        return False
    for x, y, w, h in boxes:
        for margin, top, bottom in ((5, 6, int(h * .24)), (8, h - 48, h - 9)):
            margin = min(margin, w // 4)
            if not same_pixels(
                first[y + top:y + bottom, x + margin:x + w - margin],
                second[y + top:y + bottom, x + margin:x + w - margin],
            ):
                return False
    return True


def same_receipt_view(before, after, expected, *, vision=None):
    """Revalidate recognized foreground with pixels and exact local OCR.

    Reward Acquired overlays leave the game visible behind them. Its timers and
    portraits may change while OCR runs, so only the receipt's own pixels can
    authorize a fresh input. Any uncertainty fails closed.
    """
    if before == after:
        return True
    first, second = decode_frame(before), decode_frame(after)
    if (
        getattr(first, "shape", None) != (720, 1280, 3)
        or getattr(second, "shape", None) != first.shape
    ):
        return False
    old_tips, new_tips = tooltip_boxes(first), tooltip_boxes(second)
    if old_tips != new_tips:
        return False
    if old_tips:
        # A neutral-area input can only dismiss this same recognized tooltip.
        # Its original receipt is parsed again immediately after dismissal.
        return all(
            same_pixels(
                first[y + 4 : y + h - 4, x + 4 : x + w - 4],
                second[y + 4 : y + h - 4, x + 4 : x + w - 4],
            )
            for x, y, w, h in old_tips
        )
    if expected is None:
        return False
    if expected.kind == "grid":
        return same_grid_view(first, second, expected, vision)
    panels = {
        # Keep the complete Final card row and Confirm button. The decorative
        # label column contains the neutral tooltip-dismiss click pulse, which
        # can fade while the receipt itself remains unchanged.
        "sweep": ((170, 70, 940, 65), (370, 440, 718, 190)),
        "lesson": ((416, 112, 448, 514),),
    }
    if expected.kind in panels:
        return all(
            same_pixels(first[y : y + h, x : x + w], second[y : y + h, x : x + w])
            for x, y, w, h in panels[expected.kind]
        )
    if expected.kind != "reward" or not expected.cards:
        return False

    def title(image):
        crop = image[125:190, 365:915]
        b, g, r = cv2.split(crop)
        mask = (r > 180) & (g > 180) & (b < 120)
        return mask, crop

    old_title, new_title = title(first), title(second)
    title_matches = not (
        np.count_nonzero(old_title[0]) < 2000
        # Tiny particles can cross a few heading edge pixels; the yellow text
        # itself must remain in place and retain the same color.
        or np.count_nonzero(old_title[0] != new_title[0]) > 32
        or not same_pixels(
            old_title[1][old_title[0] & new_title[0]],
            new_title[1][old_title[0] & new_title[0]],
        )
    )

    old_boxes = reward_card_boxes(first)
    if old_boxes != reward_card_boxes(second):
        return False
    named = {
        card.box: card
        for card in expected.cards
        if complete_name(card.name) and card.quantity is not None
    }
    for box in old_boxes:
        x, y, w, h = box
        card = named.get(box)
        if card:
            # Full OCR-read names and quantities identify these cards. Their
            # artwork can sparkle (notably Pyroxenes) without changing the loot.
            regions = [
                (x + 5, y + 6, w - 10, int(h * 0.24) - 6, "name"),
                (x + 8, y + h - 48, w - 16, 39, "quantity"),
            ]
        else:
            regions = [(x, y, w, h, None)]
        for rx, ry, rw, rh, field in regions:
            if not same_pixels(
                first[ry : ry + rh, rx : rx + rw],
                second[ry : ry + rh, rx : rx + rw],
            ):
                # Subpixel text rasterization and passing particles can change
                # otherwise identical labels. Re-read only the changed field,
                # requiring an exact complete value with high confidence. No
                # fuzzy text, similar artwork, or inferred quantity is accepted.
                # Unknown/partial cards retain the strict whole-card pixel guard.
                if vision is None or field is None:
                    return False
                if field == "name":
                    if reward_name(vision, second, box) != card.name:
                        return False
                elif reward_quantity(vision, second, box, minimum_confidence=.85) != card.quantity:
                    return False
    if not old_boxes:
        return False
    if title_matches:
        return True
    # A large star can pass directly over the heading while all cards remain
    # unchanged. Verify the same complete heading in the same position. Keep
    # the full-frame OCR scale: cropping this slanted heading clips its corners
    # and has produced a false cedilla even though full-frame OCR reads it well.
    # Only words inside the heading region are evidence; background labels are
    # never accepted. The caller still enforces the fresh capture's deadline.
    if vision is None or min(np.count_nonzero(t[0]) for t in (old_title, new_title)) < 2000:
        return False
    headings = [sorted(
        (word for word in vision.read(image)
         if 340 <= word.box[0] < word.box[2] <= 940
         and 105 <= word.box[1] < word.box[3] <= 210),
        key=lambda word: word.box[0],
    ) for image in (first, second)]
    for words in headings:
        if (not words or any(w.confidence < .95 for w in words)
                or "".join(w.normalized.replace(" ", "") for w in words) != "rewardacquired"):
            return False
    # OCR may merge the two words, including their intervening whitespace,
    # when a star crosses their shared edge.
    # The exact phrase and its total occupied rectangle still identify the
    # heading, independent of that segmentation choice.
    bounds = [(min(w.box[0] for w in words), min(w.box[1] for w in words),
               max(w.box[2] for w in words), max(w.box[3] for w in words))
              for words in headings]
    return all(abs(a - b) <= 3 for a, b in zip(*bounds))


def same_card(a, b):
    if a.tier is not None and b.tier is not None and a.tier != b.tier:
        return False
    if a.quantity != b.quantity:
        return False
    if a.quantity is not None and complete_name(a.name) and complete_name(b.name):
        # Named cards can animate. Compare complete labels and dimensions;
        # callers verify position for a tap/stable page, while ordered overlap
        # deliberately permits the same card to move after a scroll. A border
        # can rasterize one pixel wider at its new subpixel position; input
        # revalidation still requires the exact currently observed rectangle.
        return a.name == b.name and all(
            abs(first - second) <= 1 for first, second in zip(a.box[2:], b.box[2:])
        )
    return same_icon(a.icon, b.icon) or (
        a.box[2:] == b.box[2:] and same_grid_icon(a, b)
    )


def same_scrolled_grid_card(a, b):
    """Compare ordered loot across a scroll, never authorize a device input.

    Compact Full List cards are rendered at fractional scroll positions. Even
    a settled 15px move changes antialiasing inside their artwork and a handful
    of border pixels. Keep the quantity, tier, column and dimensions exact;
    admit only the small, observed rendering difference inside the card frame.
    ``same_card`` and ``same_receipt_view`` remain strict for every input.
    """
    if (a.quantity is None or a.quantity != b.quantity or a.tier != b.tier
            or a.box[2:] != b.box[2:] or a.box[0] != b.box[0]
            or not (83 <= a.box[3] <= 94 and 95 <= a.box[2] <= 115)):
        return False
    if complete_name(a.name) and complete_name(b.name) and a.name != b.name:
        return False
    if same_card(a, b):
        return True
    first, second = [cv2.imdecode(np.frombuffer(c.icon, np.uint8), cv2.IMREAD_UNCHANGED)
                     for c in (a, b)]
    if (first is None or second is None or first.shape != second.shape
            or first.shape != (a.box[3], a.box[2], 4)):
        return False
    # Alpha comes from the full white card outline. Neighboring cards are not
    # evidence, and clipping or a changed silhouette cannot establish overlap.
    masks = [image[:, :, 3] != 0 for image in (first, second)]
    if np.count_nonzero(masks[0] != masks[1]) > 16:
        return False
    visible = cv2.erode((masks[0] & masks[1]).astype(np.uint8),
                        np.ones((5, 5), np.uint8)).astype(bool)
    if np.count_nonzero(visible) < 4000:
        return False
    softened = [cv2.GaussianBlur(image[:, :, :3], (5, 5), 1).astype(np.float32)
                for image in (first, second)]
    delta = np.abs(softened[0] - softened[1])[visible]
    # These bounds cover the saved subpixel scroll (mean <= 1.41, MSE <= 7.62,
    # max <= 25), not similar artwork, a changed badge, or another item variant.
    return (float(delta.mean()) <= 1.6 and float(np.mean(delta * delta)) <= 10
            and float(delta.max()) <= 32)


def scrolled_grid_overlap(previous, current):
    """Return one proven suffix/prefix overlap, or zero for ambiguity.

    The tolerant comparison needs at least two consecutive cards and a common
    upward translation. It is only a fallback after exact overlap fails; a
    single similar icon never proves that a receipt page was fully inspected.
    """
    matches = []
    for count in range(2, min(len(previous), len(current)) + 1):
        pairs = list(zip(previous[-count:], current[:count]))
        shifts = [a.box[1] - b.box[1] for a, b in pairs]
        if (min(shifts) < 1 or max(shifts) > 280 or max(shifts) - min(shifts) > 1):
            continue
        if all(same_scrolled_grid_card(a, b) for a, b in pairs):
            matches.append(count)
    return matches[0] if len(matches) == 1 else 0


def save_icon(config, png):
    identifier = sha256(png).hexdigest()
    root = config.state_dir / "loot-icons"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (identifier + ".png")
    if not path.exists():
        path.write_bytes(png)
    return identifier


def learn(config, name, icon_id):
    root = config.state_dir / "loot-icons"
    if name:
        (root / (icon_id + ".json")).write_text(json.dumps({"name": name}))


def known_name(config, icon_id):
    path = config.state_dir / "loot-icons" / (icon_id + ".json")
    if path.is_file() and not path.is_symlink():
        try:
            return json.loads(path.read_text()).get("name")
        except (ValueError, OSError):
            pass
    return None


def save_result(config, evidence, items, complete, *, task, note=None):
    """Sidecar enriches historical receipts; receipt identity prevents double counting."""
    for item in items:
        learn(config, item.get("name"), item.get("icon_id"))
    value = {
        "version": 1,
        "items": items,
        "items_complete": bool(complete),
        "note": note,
    }
    target = Path(evidence).with_suffix(".loot.json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(target)
    label = ", ".join(
        f"+{i['quantity']:,} {i.get('name') or 'unidentified item'}"
        for i in items
        if i.get("quantity")
    )
    record_action(
        config,
        "loot_received",
        label or "Reward receipt saved for inspection",
        task=task,
        evidence=str(evidence),
        **value,
    )
    return value


class ReceiptReader:
    """Bounded inspection; callers already hold the device's InstanceLock."""

    def __init__(self, runner, vision, evidence):
        self.r, self.vision, self.evidence = runner, vision, Path(evidence)
        self.started, self.inputs = runner.clock(), 0
        self.sequence = 0
        self.observed = None
        self.tooltip_origin = None
        self.timeout = RECEIPT_TIMEOUT

    def capture(self):
        r = self.r
        notice_started = None
        while True:
            if r.clock() - self.started > self.timeout or self.inputs >= 160:
                r.fail("Reward inspection reached its bounded limit; receipt saved")
            if r.device.foreground_package() != r.config.package:
                r.fail("Foreground changed during reward inspection")
            at = r.clock()
            cap = Capture(r.device.screenshot(), at, r.config.package)
            # Timestamp must precede capture: the caller's freshness rules still apply.
            if not task_notice_visible(decode_frame(cap.png)):
                if notice_started is not None:
                    r.journal.record("receipt_task_notice_cleared",
                                     waited=r.clock() - notice_started)
                return cap
            if notice_started is None:
                notice_started = at
                self.evidence_frame(cap, "task-notice")
                r.journal.record("receipt_waiting_for_task_notice")
            if r.clock() - notice_started >= TASK_NOTICE_TIMEOUT:
                r.fail("Task progress notice did not clear before reward inspection")
            r.sleep(.25)

    def read(self, cap):
        result = page(cap.png, self.vision)
        self.observed = (cap.png, result)
        if result.kind == "reward":
            # Season receipts can contain dozens of named cards. Reading every
            # tooltip and proving page overlap takes longer than a sweep panel.
            self.timeout = REWARD_RECEIPT_TIMEOUT
        return result

    def revalidate(self, cap, *, force=False):
        """Refresh pixel-identical evidence without repeating full card OCR."""
        r = self.r
        if force or cap.deadline - r.clock() < 1.0:
            expected = (
                self.observed[1]
                if self.observed is not None and self.observed[0] == cap.png
                else None
            )
            attempts = 3 if expected and expected.kind == "reward" else 1
            for attempt in range(attempts):
                fresh = self.capture()
                fresh_in_time = fresh.is_fresh(r.clock())
                matches = fresh_in_time and same_receipt_view(
                    cap.png, fresh.png, expected, vision=self.vision
                )
                if matches:
                    break
                self.evidence_frame(cap, "refresh-before")
                self.evidence_frame(fresh, "refresh-after")
                r.journal.record(
                    "receipt_revalidation_failed",
                    reason="changed_receipt" if fresh_in_time else "expired_capture",
                    fresh_age=r.clock() - fresh.captured_at,
                    attempt=attempt + 1,
                )
                if not fresh_in_time or attempt + 1 == attempts:
                    break
                # A passing particle may obscure an otherwise stationary
                # heading. Wait without input, then compare another fresh frame
                # to the original recognized receipt using every same guard.
                self.evidence_frame(fresh, f"refresh-rejected-{attempt + 1}")
                r.sleep(.25)
            if not matches:
                r.fail(
                    "Reward receipt changed or capture expired before inspection input"
                )
            r.journal.record(
                "receipt_revalidated", previous_age=r.clock() - cap.captured_at,
                fresh_age=r.clock() - fresh.captured_at,
            )
            cap = fresh
            if expected is not None:
                self.observed = (cap.png, expected)
        if (
            not cap.is_fresh(r.clock())
            or r.device.foreground_package() != r.config.package
        ):
            r.fail("Reward inspection input expired or foreground changed")
        return cap

    def input(self, cap, target, *, end=None):
        r = self.r
        cap = self.revalidate(cap)
        r.journal.record(
            "intent",
            operation="loot_swipe" if end else "loot_tooltip",
            target=target,
            end=end,
        )
        if end:
            sent = r.device.swipe(
                target, end, duration_ms=800, deadline=cap.deadline, monotonic=r.clock
            )
        else:
            sent = r.device.tap(*target, deadline=cap.deadline, monotonic=r.clock)
        if not sent:
            r.fail("Reward inspection input expired during device preflight")
        self.inputs += 1
        r.actions += 1
        r.sleep(0.8)

    def evidence_frame(self, cap, suffix):
        self.r.journal.save_image(f"{self.evidence.stem}-{suffix}.png", cap.png)

    def dismiss_tooltip(self, cap, kind):
        # Never tap a neutral area unless a tooltip is positively recognized:
        # doing so on a bare Reward Acquired page could dismiss the whole receipt.
        if not has_tooltip(decode_frame(cap.png)):
            self.r.fail("No item tooltip to dismiss; receipt left untouched")
        point = {
            "sweep": (270, 550),
            "lesson": (450, 510),
            "reward": (110, 530),
            "grid": (350, 545),
        }[kind]
        for attempt in range(3):
            self.input(cap, point)
            cap = self.capture()
            deadline = self.r.clock() + TOOLTIP_RETURN_TIMEOUT
            while True:
                p = self.read(cap)
                if p.kind == kind and p.cards:
                    self.tooltip_origin = None
                    return cap
                # Reward cards scale back into place after the tooltip closes.
                # An empty intermediate page is a wait, never another tap on
                # the bare receipt (which would dismiss the earned rewards).
                if self.r.clock() >= deadline:
                    break
                self.r.sleep(0.5)
                cap = self.capture()
            if p.kind != "tooltip":
                break
        self.r.fail("Reward receipt did not return after tooltip dismissal")

    def inspect_card(self, cap, card, kind):
        name = None
        for attempt in range(3):
            self.input(cap, card.target, end=card.target if attempt == 1 else None)
            cap, name, observed_kind = self.item_detail()
            if observed_kind != kind:
                break
        self.evidence_frame(cap, f"item-{self.sequence:03d}")
        self.sequence += 1
        if has_tooltip(decode_frame(cap.png)):
            # Provenance for optional recovery: this reader opened this exact
            # tooltip through a guarded input on a recognized receipt card.
            self.tooltip_origin = (cap.png, kind)
            cap = self.dismiss_tooltip(cap, kind)
        elif observed_kind != kind:
            self.r.fail("Unrecognized item detail; receipt evidence saved for review")
        return cap, name or card.name

    def item_detail(self):
        """Observe an input's delayed tooltip before considering another input.

        The game can render the tooltip after the first post-tap screenshot.
        This is an expected transition, not authority to reuse the old receipt
        for another tap. Poll its distinctive border without input, then check
        once more after any expensive page OCR. Dismissal still requires the
        same fresh, recognized tooltip through the usual input guard.
        """
        for attempt in range(12):
            cap = self.capture()
            image = decode_frame(cap.png)
            if has_tooltip(image):
                return cap, read_tooltip(image, self.vision), "tooltip"
            if attempt < 11:
                self.r.sleep(.25)
        p = self.read(cap)
        fresh = self.capture()
        image = decode_frame(fresh.png)
        if has_tooltip(image):
            return fresh, read_tooltip(image, self.vision), "tooltip"
        # A retry, if needed, must revalidate the parsed capture normally. A
        # changed/unknown screen is never accepted as the old receipt here.
        return cap, None, p.kind

    def pan(self, cap, kind, left):
        # Overlap is required to prove no cards were skipped by momentum.
        if kind == "grid":
            start, end = ((650, 255), (650, 375)) if left else ((650, 375), (650, 255))
        else:
            start, end = ((480, 367), (875, 367)) if left else ((875, 367), (480, 367))
        self.input(cap, start, end=end)
        cap = self.capture()
        if kind == "reward":
            return self.settled_reward_page(cap)
        p = self.read(cap)
        if p.kind != kind:
            self.r.fail("Receipt changed while scrolling rewards")
        return cap, p

    def settled_reward_page(self, cap):
        """Observe through OCR before using a row that can resume scrolling.

        Two adjacent screenshots can briefly agree during an elastic pause.
        A fresh frame must also retain the parsed identities and geometry after
        OCR. Movement starts another bounded observation cycle, never an input;
        the caller still requires ordered overlap before adding any rewards.
        """
        for attempt in range(4):
            # Dragging at either end causes a subpixel elastic rebound that can
            # outlast the normal input delay. Do not OCR a frame still moving.
            stable = 0
            for _ in range(12):
                self.r.sleep(.25)
                following = self.capture()
                stable = stable + 1 if reward_layout_stable(cap.png, following.png) else 0
                cap = following
                if stable >= 2:
                    break
            else:
                self.evidence_frame(cap, "scroll-unsettled")
                self.r.fail("Reward receipt did not settle after scrolling")
            p = self.read(cap)
            if p.kind != "reward" or not p.cards:
                self.evidence_frame(cap, "scroll-rejected")
                self.r.journal.record(
                    "receipt_scroll_rejected", observed_kind=p.kind,
                    card_count=len(p.cards),
                    frame=f"{self.evidence.stem}-scroll-rejected.png",
                )
                self.r.fail("Receipt changed while scrolling rewards")
            fresh = self.capture()
            if (same_receipt_view(cap.png, fresh.png, p, vision=self.vision)
                    and fresh.is_fresh(self.r.clock())):
                self.observed = (fresh.png, p)
                return fresh, p
            self.evidence_frame(cap, f"scroll-read-{attempt + 1}")
            self.evidence_frame(fresh, f"scroll-refresh-{attempt + 1}")
            self.r.journal.record("receipt_scroll_reobserved", attempt=attempt + 1)
            cap = fresh
        self.r.fail("Reward receipt did not remain stable through OCR; receipt saved")

    @staticmethod
    def same(a, b):
        return (
            len(a.cards) == len(b.cards)
            and bool(a.cards)
            and all(
                abs(x.box[0] - y.box[0]) <= 3
                and abs(x.box[1] - y.box[1]) <= 3
                and same_card(x, y)
                for x, y in zip(a.cards, b.cards)
            )
        )

    def opened_grid(self, cap):
        """Wait without input for cards inside a just-opened Full List.

        Its heading can appear before the card borders finish fading in. An
        empty initial parse must not return success and leave this nested
        modal above the sweep receipt the caller expects to dismiss.
        """
        started = self.r.clock()
        waiting = False
        while True:
            p = self.read(cap)
            if p.kind == "grid" and p.cards:
                fresh = self.capture()
                if fresh.is_fresh(self.r.clock()) and same_receipt_view(
                    cap.png, fresh.png, p, vision=self.vision
                ):
                    if waiting:
                        self.r.journal.record("receipt_full_list_ready",
                                              waited=self.r.clock() - started,
                                              cards=len(p.cards))
                    self.observed = (fresh.png, p)
                    return fresh, p
            if not waiting:
                self.evidence_frame(cap, "full-list-opening")
                self.r.journal.record("receipt_waiting_for_full_list",
                                      observed_kind=p.kind, cards=len(p.cards))
                waiting = True
            if self.r.clock() - started >= GRID_ENTRY_TIMEOUT:
                self.evidence_frame(cap, "full-list-unreadable")
                self.r.fail("Full reward list has no readable cards; receipt saved")
            self.r.sleep(.25)
            cap = self.capture()

    def returned_sweep(self, cap, original=None):
        """Observe a closing Full List until its original receipt is restored.

        The nested modal fades out after Okay. Do not tap again during that
        transition: only a fresh sweep matching the original receipt permits
        the caller to continue with its normal resource verification.
        """
        started = self.r.clock()
        waiting = False
        while True:
            p = self.read(cap)
            if p.kind == "sweep" and (original is None or same_receipt_view(
                original[0], cap.png, original[1], vision=self.vision
            )):
                if waiting:
                    self.r.journal.record("receipt_full_list_closed",
                                          waited=self.r.clock() - started)
                return cap, p
            if not waiting:
                self.evidence_frame(cap, "full-list-closing")
                self.r.journal.record("receipt_waiting_for_sweep",
                                      observed_kind=p.kind)
                waiting = True
            if self.r.clock() - started >= GRID_RETURN_TIMEOUT:
                self.evidence_frame(cap, "full-list-return-rejected")
                self.r.fail("Full List did not return to the original sweep receipt")
            self.r.sleep(.25)
            cap = self.capture()

    def run(self):
        items = []
        complete = False
        kind = None
        try:
            cap = self.capture()
            p = self.read(cap)
            kind = p.kind
            original_sweep = (cap.png, p) if kind == "sweep" else None
            if p.expand_target:
                self.input(cap, p.expand_target)
                cap = self.capture()
                cap, p = self.opened_grid(cap)
                kind = p.kind
            elif kind == "grid" and not p.cards:
                cap, p = self.opened_grid(cap)
            if not p.cards:
                return save_result(
                    self.r.config,
                    self.evidence,
                    [],
                    False,
                    task=getattr(
                        self.r,
                        "task",
                        "cafe" if hasattr(self.r, "floor") else "lessons",
                    ),
                    note="Unrecognized reward card layout",
                )
            # Large receipts animate to the end. Rewind and scan overlapping
            # pages; the Final sweep row and Lesson Report are single panels.
            if kind in {"reward", "grid"}:
                stable = 0
                for _ in range(12):
                    cap, q = self.pan(cap, kind, True)
                    stable = stable + 1 if self.same(p, q) else 0
                    p = q
                    if stable >= 2:
                        break
                else:
                    self.r.fail("Reward receipt beginning could not be verified")
                if kind == "reward" and p.leading_clipped:
                    self.r.fail("Reward receipt beginning still contains a partial card")
            stable = 0
            previous_keys = []
            previous_items = []
            all_named = True
            for index in range(16):
                self.evidence_frame(cap, f"page-{index:02d}")
                keys = list(p.cards)
                if not keys:
                    self.r.fail(
                        "Reward receipt has no readable cards; totals need review"
                    )
                overlap = 0
                if previous_keys:
                    if len(keys) == len(previous_keys) and all(
                        same_card(a, b) for a, b in zip(keys, previous_keys)
                    ):
                        overlap = len(keys)
                    else:
                        matches = [
                            n
                            for n in range(1, min(len(keys), len(previous_keys)) + 1)
                            if all(
                                same_card(a, b)
                                for a, b in zip(previous_keys[-n:], keys[:n])
                            )
                        ]
                        if not matches and kind == "grid":
                            scrolled = scrolled_grid_overlap(previous_keys, keys)
                            if scrolled:
                                matches = [scrolled]
                        if not matches:
                            self.r.fail(
                                "Reward receipt pages did not overlap; totals need review"
                            )
                        overlap = max(matches)
                current_items = previous_items[-overlap:] if overlap else []
                for number, card in enumerate(p.cards):
                    if number < overlap:
                        continue
                    # Distinct cards with identical names/quantities on one page
                    # are separate drops. Only the ordered overlap is reused.
                    icon_id = save_icon(self.r.config, card.icon)
                    # Scroll/tooltip dismissal already parsed this receipt.
                    # Reuse those names only after a new screenshot passes the
                    # same strict identity, foreground, and deadline checks.
                    # This avoids a second full OCR pass for every item.
                    current = (
                        self.observed[1]
                        if self.observed is not None and self.observed[0] == cap.png
                        else self.read(cap)
                    )
                    if current.kind != kind or not any(
                        c.box == card.box and same_card(c, card) for c in current.cards
                    ):
                        self.evidence_frame(cap, "card-mismatch")
                        self.r.journal.record(
                            "receipt_card_mismatch", expected_kind=kind,
                            observed_kind=current.kind,
                            expected={"box": card.box, "name": card.name,
                                      "quantity": card.quantity},
                            observed=[{"box": c.box, "name": c.name,
                                       "quantity": c.quantity} for c in current.cards],
                        )
                        self.r.fail("Reward card changed before inspection")
                    cap = self.revalidate(cap, force=True)
                    item = {
                        "name": card.name or known_name(self.r.config, icon_id),
                        "quantity": card.quantity,
                        "icon_id": icon_id,
                    }
                    items.append(item)
                    current_items.append(item)
                    cap, name = self.inspect_card(cap, card, kind)
                    name = name or item["name"]
                    item["name"] = name
                    all_named &= bool(name and card.quantity)
                previous_keys = keys
                previous_items = current_items
                if kind not in {"reward", "grid"}:
                    complete = all_named and not p.clipped
                    break
                cap, q = self.pan(cap, kind, False)
                stable = stable + 1 if self.same(p, q) else 0
                p = q
                if stable >= 2:
                    # A left partial at the end was covered by the preceding
                    # ordered overlap. A right partial still hides unread loot.
                    incomplete_edge = p.trailing_clipped if kind == "reward" else p.clipped
                    complete = all_named and not incomplete_edge
                    break
            else:
                self.r.fail("Reward receipt end could not be verified")
            if kind == "grid":
                cap = self.capture()
                p = self.read(cap)
                if p.kind != "grid":
                    self.r.fail("Full List changed before returning to sweep receipt")
                self.input(cap, (640, 533))
                cap = self.capture()
                self.returned_sweep(cap, original_sweep)
            return save_result(
                self.r.config,
                self.evidence,
                items,
                complete,
                task=getattr(
                    self.r, "task", "cafe" if hasattr(self.r, "floor") else "lessons"
                ),
            )
        except Exception as exc:
            save_result(
                self.r.config,
                self.evidence,
                items,
                False,
                task=getattr(
                    self.r, "task", "cafe" if hasattr(self.r, "floor") else "lessons"
                ),
                note=str(exc),
            )
            raise


def same_sweep_below_notice(before, after):
    """Bind an obscured initial receipt to its clean view before any input.

    This exception is only for a positively detected task notice that has left.
    Preserve the uncovered heading, close control, complete Final row and
    Confirm button. Both views still require independent sweep recognition.
    """
    first, second = decode_frame(before), decode_frame(after)
    if (not task_notice_visible(first) or task_notice_visible(second)
            or getattr(second, "shape", None) != (720, 1280, 3)
            or has_tooltip(first) or has_tooltip(second)):
        return False
    return all(same_pixels(first[y:y+h, x:x+w], second[y:y+h, x:x+w])
               for x, y, w, h in ((170, 100, 940, 35), (170, 70, 140, 30),
                                  (960, 70, 150, 30), (370, 440, 718, 190)))


def settle_initial_sweep_notice(runner, frame, vision, evidence):
    """Save a clean recovery reference without changing the original evidence."""
    if (getattr(runner, "task", None) not in {"spend_ap", "bounties", "scrimmages"}
            or getattr(frame.screen, "kind", None) != "receipt"
            or getattr(frame.screen, "count", None) is None
            or not task_notice_visible(decode_frame(frame.capture.png))):
        return frame
    original = page(frame.capture.png, vision)
    reader = ReceiptReader(runner, vision, evidence)
    reader.timeout = 30
    cap = reader.capture()
    current = reader.read(cap)
    if (original.kind != "sweep" or current.kind != "sweep"
            or not same_sweep_below_notice(frame.capture.png, cap.png)):
        runner.fail("Sweep receipt changed while its initial task notice cleared")
    fresh = runner.wait("receipt")
    if (fresh.screen.count != frame.screen.count
            or getattr(fresh.screen, "task", None) != getattr(frame.screen, "task", None)
            or not same_receipt_view(cap.png, fresh.capture.png, current, vision=vision)):
        runner.fail("Sweep identity changed after its initial task notice cleared")
    reader.evidence_frame(fresh.capture, "notice-cleared-reference")
    runner.journal.record("receipt_initial_task_notice_cleared")
    return fresh


def recover_sweep_receipt(runner, frame, vision, evidence, error, *, tooltip_origin=None):
    """Leave optional loot details only when the original sweep is still proven.

    This never retries a sweep or clears its pending spend. The caller must
    still dismiss the returned receipt and verify its actual AP/ticket balance.
    The reader already persisted all confirmed items and its incomplete note.
    """
    original = page(frame.capture.png, vision)
    if original.kind != "sweep" or getattr(frame.screen, "count", None) is None:
        raise error
    reader = ReceiptReader(runner, vision, evidence)
    reader.timeout = 30
    cap = reader.capture()
    current = reader.read(cap)
    reader.evidence_frame(cap, "inspection-incomplete")
    if current.kind == "tooltip" and tooltip_origin is not None:
        tooltip_png, origin_kind = tooltip_origin
        if (origin_kind not in {"sweep", "grid"}
                or not same_receipt_view(tooltip_png, cap.png, current, vision=vision)):
            runner.fail("Item tooltip changed after incomplete loot inspection")
        cap = reader.dismiss_tooltip(cap, origin_kind)
        current = reader.read(cap)
    if current.kind == "grid" and not current.cards:
        cap, current = reader.opened_grid(cap)
    if current.kind == "grid" and current.cards:
        # A fresh, recognized Full List has exactly one safe exit. Keep the
        # normal identity/deadline/foreground guard for this input as well.
        cap = reader.revalidate(cap, force=True)
        reader.input(cap, (640, 533))
        cap = reader.capture()
        cap, current = reader.returned_sweep(cap, (frame.capture.png, original))
    if current.kind != "sweep" or not same_receipt_view(
        frame.capture.png, cap.png, original, vision=vision
    ):
        reader.evidence_frame(cap, "sweep-recovery-rejected")
        runner.fail("Incomplete loot inspection did not return to the original sweep receipt")
    fresh = runner.wait("receipt")
    if (fresh.screen.count != frame.screen.count
            or getattr(fresh.screen, "task", None) != getattr(frame.screen, "task", None)
            or not same_receipt_view(frame.capture.png, fresh.capture.png, original, vision=vision)):
        runner.fail("Sweep receipt changed after incomplete loot inspection")
    runner.journal.record("loot_inspection_incomplete", error=str(error),
                          evidence=str(evidence), next_step="verify_spent_resources")
    record_action(runner.config, "loot_inspection_incomplete",
                  "Some reward details could not be read; checking the completed sweep's resource balance",
                  task=runner.task, evidence=str(evidence), error=str(error))
    return fresh


def inspect_receipt(runner, frame, evidence):
    """Inspect then return a fresh task-specific frame for the existing dismissal."""
    vision = getattr(runner.vision, "startup", None)
    if vision is None:
        # Injected offline runner doubles do not provide OCR; their existing
        # transition tests remain independent of the separately replayed reader.
        return frame
    frame = settle_initial_sweep_notice(runner, frame, vision, evidence)
    reader = ReceiptReader(runner, vision, evidence)
    try:
        reader.run()
    except TaskError as error:
        if (getattr(runner, "task", None) not in {"spend_ap", "bounties", "scrimmages"}
                or getattr(frame.screen, "kind", None) != "receipt"):
            raise
        return recover_sweep_receipt(runner, frame, vision, evidence, error,
                                     tooltip_origin=reader.tooltip_origin)
    return runner.wait("receipt")
