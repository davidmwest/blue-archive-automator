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
from .runtime import Capture
from .vision import decode_frame


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


def encode(image):
    ok, png = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Could not encode reward icon")
    return png.tobytes()


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
                    encode(image[y + 4 : y + h - 24, x + 15 : x + w - 12]),
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
                    encode(image[y + 4 : y + 43, x + 10 : x + w - 8]),
                )
            )
        # Any card intersecting the viewport edge needs another overlapping page.
        clipped |= any(
            8 < width < 40 and (start < 5 or start + width > right - left - 5)
            for start, _, width, _ in segments
        )
    else:
        # Labeled reward cards (mail, Cafe, crafting, Tasks, Tactical Challenge).
        roi = image[250:490, 90:1190]
        mask = (np.min(roi, axis=2) > 180).astype(np.uint8) * 255
        rects = sorted(
            cv2.boundingRect(c)
            for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[
                0
            ]
        )
        for rx, ry, w, h in rects:
            if not (90 <= w <= 160 and 175 <= h <= 230):
                continue
            x, y = rx + 90, ry + 250
            if rx <= 1 or rx + w >= 1099:
                clipped = True
                continue
            qty = amount(
                read_crop(vision, image[y + h - 48 : y + h - 9, x + 8 : x + w - 8], 2)
            )
            titles = read_crop(
                vision, image[y + 6 : y + int(h * 0.24), x + 5 : x + w - 5], 3
            )
            name = (
                " ".join(
                    a.text.strip()
                    for a in sorted(titles, key=lambda a: (a.box[1], a.box[0]))
                )
                if titles and all(a.confidence >= 0.85 for a in titles)
                else None
            )
            if not complete_name(name):
                name = None
            icon = image[
                y + int(h * 0.30) : y + int(h * 0.70),
                x + int(w * 0.14) : x + int(w * 0.88),
            ]
            cards.append(Card((x, y, w, h), qty, name, encode(icon)))
        # A partial bright card at either edge must not be silently counted complete.
        clipped |= any(h >= 175 and (x <= 1 or x + w >= 1099) for x, y, w, h in rects)
    return Page(kind, tuple(cards), clipped, expand_target)


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


def same_receipt_view(before, after, expected):
    """Revalidate recognized foreground without another expensive OCR pass.

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
    if (
        np.count_nonzero(old_title[0]) < 2000
        # Tiny particles can cross a few heading edge pixels; the yellow text
        # itself must remain in place and retain the same color.
        or np.count_nonzero(old_title[0] != new_title[0]) > 32
        or not same_pixels(
            old_title[1][old_title[0] & new_title[0]],
            new_title[1][old_title[0] & new_title[0]],
        )
    ):
        return False

    def boxes(image):
        roi = image[250:490, 90:1190]
        mask = (np.min(roi, axis=2) > 180).astype(np.uint8) * 255
        return sorted(
            (x + 90, y + 250, w, h)
            for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
            for x, y, w, h in [cv2.boundingRect(c)]
            if w > 8 and h >= 175
        )

    old_boxes = boxes(first)
    if old_boxes != boxes(second):
        return False
    named = {
        card.box
        for card in expected.cards
        if complete_name(card.name) and card.quantity is not None
    }
    for x, y, w, h in old_boxes:
        regions = [(x, y, w, h)]
        if (x, y, w, h) in named:
            # Full OCR-read names and quantities identify these cards. Their
            # artwork can sparkle (notably Pyroxenes) without changing the loot.
            regions = [
                (x + 5, y + 6, w - 10, int(h * 0.24) - 6),
                (x + 8, y + h - 48, w - 16, 39),
            ]
        for rx, ry, rw, rh in regions:
            if not same_pixels(
                first[ry : ry + rh, rx : rx + rw],
                second[ry : ry + rh, rx : rx + rw],
            ):
                return False
    return bool(old_boxes)


def same_card(a, b):
    if a.quantity != b.quantity:
        return False
    if a.quantity is not None and complete_name(a.name) and complete_name(b.name):
        # Named cards can animate. Compare complete labels and dimensions;
        # callers verify position for a tap/stable page, while ordered overlap
        # deliberately permits the same card to move after a scroll.
        return a.name == b.name and a.box[2:] == b.box[2:]
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

    def capture(self):
        r = self.r
        if r.clock() - self.started > 240 or self.inputs >= 160:
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
        return result

    def input(self, cap, target, *, end=None):
        r = self.r
        if cap.deadline - r.clock() < 1.0:
            expected = (
                self.observed[1]
                if self.observed is not None and self.observed[0] == cap.png
                else None
            )
            fresh = self.capture()
            if not fresh.is_fresh(r.clock()) or not same_receipt_view(
                cap.png, fresh.png, expected
            ):
                r.fail(
                    "Reward receipt changed or capture expired before inspection input"
                )
            r.journal.record(
                "receipt_revalidated", previous_age=r.clock() - cap.captured_at
            )
            cap = fresh
        if (
            not cap.is_fresh(r.clock())
            or r.device.foreground_package() != r.config.package
        ):
            r.fail("Reward inspection input expired or foreground changed")
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
            cap = self.capture()
            name = read_tooltip(decode_frame(cap.png), self.vision)
            if name or self.read(cap).kind != kind:
                break
        self.evidence_frame(cap, f"item-{self.sequence:03d}")
        self.sequence += 1
        if has_tooltip(decode_frame(cap.png)):
            cap = self.dismiss_tooltip(cap, kind)
        elif self.read(cap).kind != kind:
            self.r.fail("Unrecognized item detail; receipt evidence saved for review")
        return cap, name or card.name

    def pan(self, cap, kind, left):
        # Overlap is required to prove no cards were skipped by momentum.
        if kind == "grid":
            start, end = ((650, 255), (650, 375)) if left else ((650, 375), (650, 255))
        else:
            start, end = ((480, 367), (875, 367)) if left else ((875, 367), (480, 367))
        self.input(cap, start, end=end)
        cap = self.capture()
        p = self.read(cap)
        if p.kind != kind:
            self.r.fail("Receipt changed while scrolling rewards")
        return cap, p

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
                    cap = self.capture()
                    current = self.read(cap)
                    if current.kind != kind or not any(
                        c.box == card.box and same_card(c, card) for c in current.cards
                    ):
                        self.r.fail("Reward card changed before inspection")
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
                    complete = all_named and not p.clipped
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
