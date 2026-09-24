"""Local OCR and fixed-coordinate recognition for the English startup flow."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import re

import cv2
import numpy as np


class VisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    confidence: float
    box: tuple[int, int, int, int]

    @property
    def normalized(self) -> str:
        return re.sub(r"[^a-z0-9]+", " ", self.text.lower()).strip()

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.box
        return (x1 + x2) // 2, (y1 + y2) // 2


@dataclass(frozen=True)
class Observation:
    state: str
    detail: str
    target: tuple[int, int] | None = None
    text: tuple[str, ...] = ()
    detector: str = "known"


def decode_frame(png: bytes) -> np.ndarray:
    if not png:
        raise VisionError("Screenshot is empty")
    try:
        image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise VisionError("Screenshot is not a decodable image") from exc
    if image is None:
        raise VisionError("Screenshot is not a decodable image")
    if image.shape[:2] != (720, 1280):
        height, width = image.shape[:2]
        raise VisionError(f"Expected 1280×720 landscape; screenshot is {width}×{height}")
    return image


class StartupVision:
    """Keep OCR local and CPU-bounded; no cloud model calls during a run."""

    def __init__(self):
        from rapidocr import RapidOCR

        cv2.setNumThreads(2)
        self.ocr = RapidOCR(params={
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        })
        self.assets = []
        root = files("ba_automator").joinpath("assets")
        manifest = root.joinpath("startup.json")
        if manifest.is_file():
            for spec in json.loads(manifest.read_text()):
                template = cv2.imdecode(
                    np.frombuffer(root.joinpath(spec["file"]).read_bytes(), dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
                if template is None:
                    raise VisionError(f"Cannot decode recognition asset: {spec['file']}")
                self.assets.append((spec, template))

    def read(self, frame: np.ndarray) -> list[Word]:
        result = self.ocr(frame, use_cls=False)
        if result.txts is None:
            return []
        words = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            if score < 0.65:
                continue
            x1, y1 = np.min(box, axis=0)
            x2, y2 = np.max(box, axis=0)
            words.append(Word(text, float(score), (int(x1), int(y1), int(x2), int(y2))))
        return words

    def matches(self, frame: np.ndarray) -> dict[str, tuple[int, int]]:
        hits = {}
        for spec, template in self.assets:
            x1, y1, x2, y2 = spec["region"]
            crop = frame[y1:y2, x1:x2]
            if spec.get("mode") == "white_mask":
                # Full-screen news uses a white X over changing promotional artwork.
                # Match both the strokes and the surrounding negative space.
                threshold = spec.get("white_threshold", 160)
                crop = (np.min(crop, axis=2) >= threshold).astype(np.uint8) * 255
                template = (np.min(template, axis=2) >= threshold).astype(np.uint8) * 255
            if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
                continue
            # Absolute color agreement rejects menu labels visible through a dimmed modal.
            scores = cv2.matchTemplate(crop, template, cv2.TM_SQDIFF_NORMED)
            minimum, _, location, _ = cv2.minMaxLoc(scores)
            if minimum <= spec.get("max_error", 0.025):
                tx, ty = location
                sample = crop[ty:ty + template.shape[0], tx:tx + template.shape[1]]
                mae = np.abs(sample.astype(np.float32) - template.astype(np.float32)).mean()
                if mae <= spec.get("max_mae", 12):
                    hits[spec["name"]] = (x1 + tx + template.shape[1] // 2,
                                          y1 + ty + template.shape[0] // 2)
        return hits

    def analyze(self, png: bytes) -> Observation:
        frame = decode_frame(png)
        words = self.read(frame)
        observation = classify(words, self.matches(frame))
        # Specific startup handlers retain priority over this narrow visual fallback.
        if observation.state == "unknown":
            if publisher_splash(frame, words):
                return Observation(
                    "loading", "Waiting for the recognized publisher-logo startup splash", None,
                    observation.text, detector="publisher_splash",
                )
            target = shaded_overlay_close(frame, words, self.assets)
            if target is not None:
                return Observation(
                    "popup", "Dismiss a shaded-home overlay with one verified corner X", target,
                    observation.text, detector="shaded_overlay",
                )
        return observation


def publisher_splash(frame: np.ndarray, words: list[Word]) -> bool:
    """Recognize the black Nexon / Nexon Games / IO Division startup screen.

    Logo text in another screen is insufficient. All OCR text must belong to the
    reviewed logo strip, with three distinct publisher groups in their positions.
    This is only a loading observation; it never extends the startup deadline.
    """
    if frame.shape != (720, 1280, 3):
        return False
    regions = ((100, 280, 370, 420), (390, 280, 670, 420),
               (670, 280, 980, 420), (980, 280, 1180, 420))
    allowed = ({"nexon"}, {"nexon", "games", "nexongames"},
               {"iodivision"}, {"mx", "studio", "mxstudio"})
    groups = [set() for _ in regions]
    for word in words:
        token = word.normalized.replace(" ", "")
        x1, y1, x2, y2 = word.box
        matching = [index for index, (left, top, right, bottom) in enumerate(regions)
                    if token in allowed[index] and left <= x1 < x2 <= right
                    and top <= y1 < y2 <= bottom]
        if len(matching) != 1:
            return False
        if word.confidence >= .80:
            groups[matching[0]].add(token)
    if ("nexon" not in groups[0] or "iodivision" not in groups[2]
            or not ({"nexon", "games"} <= groups[1] or "nexongames" in groups[1])):
        return False
    dark = np.max(frame, axis=2) <= 20
    outside_strip = np.ones((720, 1280), dtype=bool)
    outside_strip[280:420, 100:1180] = False
    return bool(dark.mean() >= .94 and dark[outside_strip].mean() >= .995)


def shaded_overlay_close(
    frame: np.ndarray,
    words: list[Word],
    assets: list[tuple[dict, np.ndarray]],
) -> tuple[int, int] | None:
    """Recognize a conservative dismissible modal without a popup-specific asset.

    Dimming alone is never actionable. Both home templates must still match in shape
    under one similar darkening factor; a bright rectangular modal and exactly one
    isolated X near its top-right corner must also be present. Unsupported styling,
    dialog choices, and account/payment language deliberately produce no target.
    """
    if frame.shape != (720, 1280, 3):
        return None
    text = " ".join(word.normalized for word in words)
    sensitive = (
        r"\b(?:purchase|buy|spend|payment|checkout|price|currency|credits?|pyroxenes?|"
        r"account|password|email|verification|authentication|delete|reset|transfer|"
        r"usd|eur|jpy|gbp|krw|confirm|cancel|yes|no|ok|close|continue|submit|"
        r"accept|decline|agree|consent|privacy|recruit|summon|exchange|sell|"
        r"download|update|maintenance|retry|reconnect)\b|"
        r"\b(?:sign in|sign up|log in|log out|login|logout|two factor|link account)\b"
    )
    if re.search(sensitive, text) or any(re.search(r"[$€£¥₩]", word.text) for word in words):
        return None

    gains = []
    for name in ("home_left", "home_right"):
        anchors = [(spec, template) for spec, template in assets if spec.get("name") == name]
        if len(anchors) != 1:
            return None
        spec, template = anchors[0]
        x1, y1, x2, y2 = spec["region"]
        crop = frame[y1:y2, x1:x2]
        if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
            return None
        reference_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        if reference_gray.std() < 15:
            return None
        scores = cv2.matchTemplate(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), reference_gray,
                                   cv2.TM_CCOEFF_NORMED)
        _, correlation, _, location = cv2.minMaxLoc(scores)
        if correlation < 0.985:
            return None
        tx, ty = location
        reference = template.astype(np.float32)
        sample = crop[ty:ty + template.shape[0], tx:tx + template.shape[1]].astype(np.float32)
        gain = float(np.sum(sample * reference) / max(float(np.sum(reference * reference)), 1))
        residual = float(np.abs(sample - gain * reference).mean())
        if not 0.25 <= gain <= 0.80 or residual > 6:
            return None
        gains.append(gain)
    if abs(gains[0] - gains[1]) > 0.08:
        return None

    # Restrict the fallback to a bright central modal. This excludes dark artwork,
    # full-screen web views, and an isolated X in the character/background image.
    mask = (np.min(frame, axis=2) >= 205).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), dtype=np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    panels = []
    for x, y, width, height, area in stats[1:count]:
        if (240 <= x <= 700 and 60 <= y <= 280 and 350 <= width <= 1000
                and 180 <= height <= 560 and x + width <= 1250 and y + height <= 665
                and area / (width * height) >= 0.72):
            panels.append((int(x), int(y), int(width), int(height)))
    if len(panels) != 1:
        return None
    x, y, width, _ = panels[0]
    left, top = max(0, x + width - 110), max(0, y - 12)
    right, bottom = min(1280, x + width + 12), min(720, y + 90)
    region = frame[top:bottom, left:right]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32)
    difference = gray - cv2.GaussianBlur(gray, (0, 0), 6)
    candidates = []
    for polarity in (1, -1):
        strokes = (polarity * difference >= 35).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(strokes, connectivity=8)
        for index in range(1, count):
            gx, gy, gw, gh, area = map(int, stats[index])
            if not (14 <= gw <= 38 and 14 <= gh <= 38 and 0.8 <= gw / gh <= 1.25
                    and 0.16 <= area / (gw * gh) <= 0.58):
                continue
            glyph = labels[gy:gy + gh, gx:gx + gw] == index
            yy, xx = np.indices(glyph.shape, dtype=np.float32)
            xx, yy = xx / (gw - 1), yy / (gh - 1)
            diagonal = (np.abs(xx - yy) <= 0.18) | (np.abs(xx + yy - 1) <= 0.18)
            # Require both diagonals and their four arms, including negative space.
            if np.count_nonzero(glyph & ~diagonal) > area * 0.08:
                continue
            arms = ((xx < 0.35) & (yy < 0.35), (xx > 0.65) & (yy < 0.35),
                    (xx < 0.35) & (yy > 0.65), (xx > 0.65) & (yy > 0.65))
            if any(np.count_nonzero(glyph & arm) < area * 0.10 for arm in arms):
                continue
            if np.count_nonzero(glyph & (np.abs(xx - 0.5) < 0.14)
                                & (np.abs(yy - 0.5) < 0.14)) < 3:
                continue
            cx, cy = left + gx + gw // 2, top + gy + gh // 2
            # A text letter X embedded in a larger OCR phrase is not a close control.
            if any(word.box[0] - 3 <= cx <= word.box[2] + 3
                   and word.box[1] - 3 <= cy <= word.box[3] + 3
                   and word.normalized != "x" for word in words):
                continue
            candidates.append((cx, cy))
    # Opposite-polarity edge artifacts or multiple close-like controls are ambiguous.
    if len(candidates) != 1:
        return None
    return candidates[0]


def classify(words: list[Word], matches: dict[str, tuple[int, int]]) -> Observation:
    """Only explicit startup contexts authorize a tap; never accept a bare Yes/OK."""
    visible = tuple(word.text for word in words)
    text = " ".join(word.normalized for word in words)

    def result(state, detail, target=None):
        return Observation(state, detail, target, visible)

    def button(labels, *, min_y=350, max_y=680, min_x=300):
        candidates = [word for word in words if word.normalized in labels
                      and min_y <= word.center[1] <= max_y and word.center[0] >= min_x]
        return max(candidates, key=lambda word: word.confidence).center if candidates else None

    # The in-game news portal can discuss maintenance or updates as article content.
    # Its own sidebar plus circular close control identify the dismissible overlay.
    if {"news_close", "news_sidebar"} <= matches.keys():
        return result("popup", "Close the in-game announcements overlay", matches["news_close"])

    # Block before checking background home labels or affirmative controls.
    if any(phrase in text for phrase in ("under maintenance", "maintenance in progress", "maintenance is underway")):
        return result("blocked", "The game is under maintenance")
    if any(phrase in text for phrase in ("update the app", "update your app", "latest version of the app",
                                         "go to the store", "update from the store")):
        return result("blocked", "A store app update is required; game-data downloads are handled separately")
    if (("password" in text and any(term in text for term in ("sign in", "log in", "email")))
            or any(phrase in text for phrase in ("select login method", "choose a login method"))):
        return result("blocked", "Account sign-in needs attention; the task uses the existing game session")

    dialog_text = " ".join(word.normalized for word in words
                           if 330 <= word.center[0] <= 950 and 120 <= word.center[1] <= 610)
    ready = any(phrase in dialog_text for phrase in ("ready to download", "additional data", "data download",
                                             "download required", "download the data"))
    size_request = ("download" in dialog_text and bool(re.search(r"\b\d+(?: \d+)?\s*(?:mb|gb|mib|gib)\b", dialog_text))
                    and any(term in dialog_text for term in ("required", "must", "would you", "do you", "wifi", "wi fi")))
    if ready or size_request:
        if any(term in dialog_text for term in ("purchase", "pyroxene", "payment", "spend")):
            return result("unknown", "Download text conflicts with a spending or payment prompt")
        target = button({"yes", "confirm", "ok", "download"}, min_x=550, max_y=620)
        if target:
            return result("download_prompt", "Accept required game-data download", target)

    if any(phrase in text for phrase in ("connection lost", "connection error", "network error", "failed to connect")):
        target = button({"reconnect", "retry", "confirm", "ok"})
        if target:
            return result("popup", "Retry startup network connection", target)

    if "new products added" in text:
        target = button({"confirm", "close"})
        if target:
            return result("popup", "Dismiss the new-products notice", target)

    for name in ("notice_close", "startup_close", "survey_close"):
        if name in matches:
            if name == "startup_close" and not any(phrase in text for phrase in (
                    "don t show again today", "dont show again today", "do not show again today")):
                continue
            return result("popup", f"Dismiss recognized {name.replace('_', ' ')}", matches[name])

    for word in words:
        compact = word.normalized.replace(" ", "")
        if compact in {"touchtostart", "taptostart", "touchtobegin", "taptobegin"}:
            return result("title", "Enter the game using the existing login", word.center)
        if compact in {"touchtocontinue", "taptocontinue"}:
            return result("popup", "Dismiss startup reward screen", word.center)

    # Arona's calendar has no X or Continue label. Its fixed ten-day grid and
    # exact heading authorize a tap on the empty floor, outside reward cells.
    def at(label, bounds):
        x1, y1, x2, y2 = bounds
        return any(w.normalized.replace(" ", "") == label and
                   x1 <= w.center[0] <= x2 and y1 <= w.center[1] <= y2 for w in words)
    if (at("aronasattendance", (580, 105, 1120, 177)) and
        at("daily", (690, 75, 810, 130)) and
        all(at(f"day{n}", (480+(n-1)%5*154, 174+(n-1)//5*218,
                            590+(n-1)%5*154, 218+(n-1)//5*218)) for n in (1,2,3,5,6,7,8,9,10))):
        return result("popup", "Advance Arona's daily attendance calendar", (1190, 675))

    if any(phrase in text for phrase in ("attendance", "daily login", "login bonus", "reward acquired",
                                         "rewards acquired", "items have expired", "expired items")):
        target = button({"confirm", "ok", "close"})
        if target:
            return result("popup", "Dismiss recognized login reward or expired-item notice", target)

    if any(phrase in text for phrase in ("downloading", "extracting", "decompressing", "applying patch",
                                         "verifying downloaded")):
        return result("downloading", "Waiting for game-data download or installation")

    if button({"confirm", "yes", "no", "ok", "cancel", "close", "purchase", "buy"}, min_y=220):
        return result("unknown", "An unrecognized confirmation may be covering the home screen")

    if any(phrase in text for phrase in ("now loading", "loading", "checking data", "initializing",
                                         "resetting the game data")):
        return result("loading", "Waiting for startup loading")

    # Both color-sensitive, fixed-position home anchors must be unobstructed.
    if {"home_left", "home_right"} <= matches.keys():
        return result("home", "Home menu anchors are visible and unobstructed")
    return result("unknown", "Waiting for a recognized startup screen")
