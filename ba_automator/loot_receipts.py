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
from .runtime import Capture
from .vision import decode_frame

RECEIPT_TIMEOUT = 240
REWARD_RECEIPT_TIMEOUT = 900
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
    for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[
        0
    ]:
        x, y, w, h = cv2.boundingRect(contour)
        if not (170 <= w <= 700 and 70 <= h <= 420):
            continue
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
            qty = amount(read_crop(vision, crop))
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
                first[y + 4 : y + h - 16, x + 4 : x + w - 4],
                second[y + 4 : y + h - 16, x + 4 : x + w - 4],
            )
            for x, y, w, h in old_tips
        )
    if expected is None:
        return False
    panels = {
        "grid": ((314, 114, 650, 489),),
        "sweep": ((170, 70, 940, 65), (191, 440, 897, 190)),
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
    return same_icon(a.icon, b.icon)


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
        self.timeout = RECEIPT_TIMEOUT

    def capture(self):
        r = self.r
        if r.clock() - self.started > self.timeout or self.inputs >= 160:
            r.fail("Reward inspection reached its bounded limit; receipt saved")
        if r.device.foreground_package() != r.config.package:
            r.fail("Foreground changed during reward inspection")
        at = r.clock()
        cap = Capture(r.device.screenshot(), at, r.config.package)
        # Timestamp must precede capture: the caller's freshness rules still apply.
        return cap

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
            for _ in range(3):
                p = self.read(cap)
                if p.kind == kind and p.cards:
                    return cap
                if p.kind == "tooltip":
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

    def run(self):
        items = []
        complete = False
        kind = None
        try:
            cap = self.capture()
            p = self.read(cap)
            kind = p.kind
            if p.expand_target:
                self.input(cap, p.expand_target)
                cap = self.capture()
                p = self.read(cap)
                kind = p.kind
                if kind != "grid":
                    self.r.fail("Full reward list did not open; receipt saved")
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
                if self.read(cap).kind != "sweep":
                    self.r.fail("Sweep receipt did not return after Full List")
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


def inspect_receipt(runner, frame, evidence):
    """Inspect then return a fresh task-specific frame for the existing dismissal."""
    vision = getattr(runner.vision, "startup", None)
    if vision is None:
        # Injected offline runner doubles do not provide OCR; their existing
        # transition tests remain independent of the separately replayed reader.
        return frame
    ReceiptReader(runner, vision, evidence).run()
    return runner.wait("receipt")
