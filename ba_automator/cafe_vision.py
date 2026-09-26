"""Deterministic Cafe HUD, attention-marker, and relationship feedback detection."""
from importlib.resources import files

import cv2
import numpy as np

from .vision import Word


def scene_point(x: int, y: int) -> bool:
    # Exclude every HUD control, the invitation bar, and the floor-switch strip.
    return 140 <= x <= 1150 and 135 <= y <= 575


def yellow_mask(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, np.array([17, 160, 210]), np.array([36, 255, 255]))


class CafeVision:
    def __init__(self, startup):
        self.startup = startup
        root = files("ba_automator").joinpath("assets")
        self.assets = {
            name: cv2.imdecode(np.frombuffer(root.joinpath(f"cafe_{name}.png").read_bytes(), np.uint8), 1)
            for name in ("attention", "title", "edit", "comfort", "heart")
        }
        self.marker = yellow_mask(self.assets["attention"])

    def is_cafe(self, frame):
        for name, bounds in (("title", (102, 6, 173, 40)),
                             ("edit", (70, 658, 124, 691)),
                             ("comfort", (964, 626, 1062, 660))):
            x1, y1, x2, y2 = bounds
            template = self.assets[name]
            crop = frame[y1:y2, x1:x2]
            error, _, (x, y), _ = cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_SQDIFF_NORMED))
            sample = crop[y:y + template.shape[0], x:x + template.shape[1]]
            if error > .035 or np.abs(sample.astype(float) - template.astype(float)).mean() > 14:
                return False
        return True

    def home_entry_target(self, frame):
        """Use the visible Cafe label, with both unobstructed Home anchors present."""
        hits = self.startup.matches(frame)
        if {"home_left", "home_right"} <= hits.keys():
            # home_left is the Cafe label itself. The illustration above it
            # remained visible but ignored input in saved scheduled visits.
            return hits["home_left"]
        return None

    def visitor_notice(self, frame, words):
        """Recognize the English visiting-student notice over a dimmed Cafe HUD."""
        panel = [w for w in words if 345 <= w.center[0] <= 935 and 170 <= w.center[1] <= 515]
        required = (("guide", (550, 170, 735, 210)),
                    ("visiting student list", (420, 230, 860, 280)),
                    ("confirm", (525, 420, 755, 495)))
        targets = []
        for label, (x1, y1, x2, y2) in required:
            hits = [w for w in panel if w.normalized == label
                    and x1 <= w.center[0] <= x2 and y1 <= w.center[1] <= y2]
            if len(hits) != 1:
                return None
            targets.append(hits[0].center)
        # This notice has only a heading, list label, button, and bond numbers.
        if any(w.normalized not in {item[0] for item in required}
               and not w.normalized.isdecimal() for w in panel):
            return None
        gains = []
        for name, bounds in (("title", (102, 6, 173, 40)),
                             ("edit", (70, 658, 124, 691)),
                             ("comfort", (964, 626, 1062, 660))):
            x1, y1, x2, y2 = bounds
            template = self.assets[name]
            crop = frame[y1:y2, x1:x2]
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))
            reference = template.astype(float)
            sample = crop[y:y + template.shape[0], x:x + template.shape[1]].astype(float)
            gain = float((sample * reference).sum() / max((reference * reference).sum(), 1))
            if score < .985 or not .25 <= gain <= .8 or np.abs(sample - gain * reference).mean() > 6:
                return None
            gains.append(gain)
        return targets[-1] if max(gains) - min(gains) < .08 else None

    def markers(self, frame):
        mask = yellow_mask(frame)
        mask[:45] = 0
        mask[580:] = 0
        found = []
        for scale in (.75, .85, 1., 1.12):
            template = cv2.resize(self.marker, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            scores = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
            for _ in range(12):
                _, score, _, (x, y) = cv2.minMaxLoc(scores)
                if score < .78:
                    break
                h, w = template.shape
                center = (x + w // 2, y + h // 2)
                # The rays are just left of the student's head.
                target = (center[0] + round(48 * scale), center[1] + round(40 * scale))
                if scene_point(*target) and all(abs(center[0]-p[0]) + abs(center[1]-p[1]) > 45 for p, _ in found):
                    found.append((center, target))
                scores[max(0,y-30):y+30, max(0,x-30):x+30] = -1
        return found

    def words(self, frame) -> list[Word]:
        return self.startup.read(frame)

    def relationship_feedback(self, before, after, target):
        # Match the pink relationship heart itself; moving pink hair is not feedback.
        x, y = target
        x1, x2, y1, y2 = max(130,x-120), min(1160,x+120), max(50,y-160), min(585,y+120)
        def match(image):
            crop = image[y1:y2, x1:x2]
            scores = []
            for scale in (.8, 1., 1.2):
                template = cv2.resize(self.assets["heart"], None, fx=scale, fy=scale)
                scores.append(cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1])
            return max(scores) > .86
        return match(after) and not match(before)
