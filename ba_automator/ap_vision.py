"""Fixed English mission/commission recognition, local OCR and star colors."""

from dataclasses import dataclass
import re
import cv2
from .vision import decode_frame, Word
from .crafting_vision import within, number, bright, cyan
from .shop_vision import has, text_in


@dataclass(frozen=True)
class APStage:
    id: str
    stars: int
    target: tuple[int, int] | None
    remaining: int | None = None


@dataclass(frozen=True)
class APScreen:
    kind: str
    ap: int | None = None
    strategy: str | None = None
    stage: str | None = None
    stages: tuple[APStage, ...] = ()
    area: int | None = None
    count: int | None = None
    cost: int | None = None
    after: int | None = None
    remaining: int | None = None
    stars: int | None = None
    target: tuple[int, int] | None = None
    rewards: tuple[str, ...] = ()
    left: bool = False
    right: bool = False


def gold(frame, box):
    x1, y1, x2, y2 = map(int, box)
    if x1 < 0 or y1 < 0 or x2 > 1280 or y2 > 720:
        return False
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return (
        float(
            (
                (hsv[:, :, 0] >= 15)
                & (hsv[:, :, 0] <= 40)
                & (hsv[:, :, 1] > 90)
                & (hsv[:, :, 2] > 190)
            ).mean()
        )
        > 0.2
    )


def mission_stars(frame):
    return sum(gold(frame, (157, y - 7, 174, y + 7)) for y in (367, 401, 435))


def ap_value(words):
    result = number(words, (493, 0, 620, 48), r"(\d+)/(\d+)")
    return result[0] if result else None


def projected_ap(words):
    matches = []
    for w in within(words, (965, 338, 1115, 382)):
        match = re.fullmatch(r"[^0-9]*([0-9]+)\s*→\s*([0-9]+)", w.text.strip())
        if match:
            matches.append(tuple(map(int, match.groups())))
    return matches[0] if len(matches) == 1 else None


def area_arrow(frame, x):
    hsv = cv2.cvtColor(frame[330:391, x : x + 42], cv2.COLOR_BGR2HSV)
    return (
        float(
            (
                (hsv[:, :, 0] > 95)
                & (hsv[:, :, 0] < 125)
                & (hsv[:, :, 1] > 65)
                & (hsv[:, :, 2] < 220)
            ).mean()
        )
        > 0.12
    )


def classify_ap(frame, words, *, home=False):
    ap = ap_value(words)
    unknown = APScreen("unknown", ap=ap)
    if (
        has(words, "sweep complete", (450, 65, 835, 125))
        and has(words, "confirm", (480, 535, 795, 625))
        and cyan(frame, (550, 558, 750, 604))
    ):
        counts = [
            int(m[1])
            for w in within(words, (195, 200, 400, 430))
            if (m := re.fullmatch(r"(\d+) time\(s\)", w.text.strip()))
        ]
        rewards = tuple(w.text for w in within(words, (370, 475, 1050, 525)))
        return APScreen(
            "receipt",
            ap=ap,
            count=max(counts) if counts else None,
            target=(640, 583),
            rewards=rewards,
        )
    confirmation = text_in(words, (420, 290, 860, 385))
    match = re.fullmatch(r"Use (\d+) AP to Sweep (\d+) time\(s\)\?", confirmation)
    if (
        match
        and has(words, "notice", (500, 130, 780, 200))
        and has(words, "confirm", (650, 465, 880, 545))
        and cyan(frame, (660, 480, 860, 530))
    ):
        return APScreen(
            "confirm", ap=ap, cost=int(match[1]), count=int(match[2]), target=(767, 505)
        )
    if (
        has(words, "mission info", (440, 80, 850, 130))
        and bright(frame, (420, 92, 495, 122))
        and has(words, "sweep", (850, 235, 1010, 290))
    ):
        # OCR may split the index from the name, with their boxes at slightly
        # different heights. Identify the index independently of reading order.
        identities = [
            m
            for w in within(words, (130, 220, 620, 280))
            if (m := re.match(r"^([1-9]\d?-[123])(?=\D|$)", w.text))
        ]
        stage_match = identities[0] if len(identities) == 1 else None
        count = number(words, (904, 309, 970, 358))
        projection = text_in(words, (825, 372, 1105, 416))
        parsed = re.fullmatch(
            r"[^0-9]*(\d+)\s*→\s*(\d+)\s*\|?\s*Remaining:\s*(\d+)/3", projection
        )
        # Sweep previews show attempts AFTER the selected batch. The mission
        # button retains the current daily allowance; reconcile both counters.
        actual = re.search(
            r"Remaining:\s*(\d+)/3$", text_in(words, (840, 490, 1125, 547))
        )
        if stage_match and count and parsed and actual:
            before, after, projected_remaining = map(int, parsed.groups())
            remaining = int(actual[1])
            qty = count[0]
            if (
                before != ap
                or not 0 <= remaining <= 3
                or not 0 <= qty <= remaining
                or projected_remaining != remaining - qty
            ):
                return unknown
            total = before - after
            if qty and (total <= 0 or total % qty):
                return unknown
            if not qty and total != 0:
                return unknown
            stars = sum(gold(frame, (157, y - 7, 174, y + 7)) for y in (396, 431, 465))
            return APScreen(
                "detail",
                ap=ap,
                strategy="elephs",
                stage=stage_match[1],
                count=qty,
                cost=total // qty if qty else None,
                after=after,
                remaining=remaining,
                stars=stars,
                target=(
                    (937, 437)
                    if qty
                    and has(words, "start sweep", (770, 405, 1110, 477))
                    and cyan(frame, (810, 417, 1050, 460))
                    else None
                ),
            )
        return unknown
    if (
        has(words, "mission info", (440, 110, 850, 170))
        and bright(frame, (420, 125, 495, 152))
        and has(words, "sweep", (850, 200, 1010, 262))
    ):
        identity = text_in(words, (195, 183, 620, 245))
        index = number(words, (125, 185, 195, 240))
        strategy = (
            "reports"
            if re.fullmatch(r"Ruined Munitions Factory\s*[A-Z1]", identity)
            else (
                "credits"
                if re.fullmatch(r"Slumpia Square\s*[A-Z1]", identity)
                else None
            )
        )
        stage = (
            chr(64 + index[0]) if strategy and index and 1 <= index[0] <= 26 else None
        )
        if stage and not re.search(
            ("[I1]" if stage == "I" else stage) + r"$", identity
        ):
            return unknown
        count = number(words, (904, 277, 970, 330))
        projection = projected_ap(words)
        if (
            stage
            and count
            and projection
            and projection[0] == ap
            and projection[0] >= projection[1]
        ):
            total = projection[0] - projection[1]
            if count[0] == 0 and total == 0:
                return APScreen(
                    "detail",
                    ap=ap,
                    strategy=strategy,
                    stage=stage,
                    count=0,
                    after=ap,
                    stars=mission_stars(frame),
                )
            if count[0] <= 0 or total <= 0:
                return unknown
            if total % count[0]:
                return unknown
            return APScreen(
                "detail",
                ap=ap,
                strategy=strategy,
                stage=stage,
                count=count[0],
                cost=total // count[0],
                after=projection[1],
                stars=mission_stars(frame),
                target=(
                    (937, 405)
                    if has(words, "start sweep", (770, 373, 1110, 449))
                    and cyan(frame, (810, 387, 1050, 430))
                    else None
                ),
            )
        return unknown
    # The adjacent help icon sometimes joins the header as an OCR "0".
    if any(
        re.fullmatch(r"commissions(?: [0o])?", w.normalized)
        for w in within(words, (80, 0, 330, 50))
    ) and bright(frame, (320, 5, 390, 32)):
        if (
            has(words, "request select", (620, 65, 1010, 140))
            and has(words, "base defense", (940, 140, 1250, 220))
            and has(words, "item retrieval", (940, 255, 1250, 330))
        ):
            return APScreen("commissions", ap=ap)
        strategy = (
            "credits"
            if has(words, "item retrieval", (80, 125, 420, 195))
            else "reports" if has(words, "base defense", (80, 125, 420, 195)) else None
        )
        if strategy and has(words, "stage list", (800, 75, 1070, 140)):
            stages = []
            for word in within(words, (690, 140, 750, 650)):
                if not re.fullmatch(r"\d{2}", word.text):
                    continue
                n = int(word.text)
                x, y = word.center
                if not 1 <= n <= 26 or not 145 < y < 630:
                    continue
                title = text_in(words, (761, y - 5, 1060, y + 40))
                expected = (
                    "Ruined Munitions Factory"
                    if strategy == "reports"
                    else "Slumpia Square"
                )
                suffix = "[I1]" if n == 9 else chr(64 + n)
                if not re.fullmatch(re.escape(expected) + r"\s*" + suffix, title):
                    continue
                stars = sum(
                    gold(frame, (x0, y + 22, x0 + 11, y + 34)) for x0 in (698, 713, 730)
                )
                target = (
                    (1119, y + 13)
                    if cyan(frame, (1080, y - 3, 1150, y + 30))
                    and has(words, "enter", (1060, y - 15, 1190, y + 45))
                    else None
                )
                stages.append(APStage(chr(64 + n), stars, target))
            # Locked rows have faint indices. Their exact title can account for
            # a zero-star row, but must never authorize an Enter tap.
            prefix = (
                "Ruined Munitions Factory"
                if strategy == "reports"
                else "Slumpia Square"
            )
            for word in within(words, (760, 155, 1060, 645)):
                match = re.fullmatch(re.escape(prefix) + r" ([A-Z])", word.text)
                if not match or any(s.id == match[1] for s in stages):
                    continue
                y = word.center[1] - 13
                if (
                    145 < y < 630
                    and not cyan(frame, (1080, y - 3, 1150, y + 30))
                    and not any(
                        gold(frame, (x0, y + 22, x0 + 11, y + 34))
                        for x0 in (698, 713, 730)
                    )
                ):
                    stages.append(APStage(match[1], 0, None))
            stages.sort(key=lambda s: s.id)
            return (
                APScreen(
                    "commission_list", ap=ap, strategy=strategy, stages=tuple(stages)
                )
                if stages
                else unknown
            )
    if (
        has(words, "campaign", (80, 0, 270, 50))
        and bright(frame, (320, 5, 390, 32))
        and has(words, "mission", (680, 200, 865, 260))
        and has(words, "commissions", (620, 480, 810, 535))
    ):
        return APScreen("campaign", ap=ap)
    if (
        has(words, "mission", (80, 0, 230, 50))
        and bright(frame, (320, 5, 390, 32))
        and has(words, "normal", (730, 130, 880, 185))
        and has(words, "hard", (1000, 130, 1140, 185))
    ):
        area = number(words, (110, 175, 175, 220))
        if not area or not 1 <= area[0] <= 99:
            return unknown
        left, right = area_arrow(frame, 22), area_arrow(frame, 1219)
        hsv = cv2.cvtColor(frame[135:180, 960:1190], cv2.COLOR_BGR2HSV)
        hard = (
            float(
                (
                    (hsv[:, :, 0] < 10) & (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 140)
                ).mean()
            )
            > 0.5
        )
        if not hard:
            return APScreen("normal", ap=ap, area=area[0], left=left, right=right)
        stages = []
        for word in within(words, (680, 210, 765, 510)):
            if not re.fullmatch(rf"{area[0]}-[123]", word.text):
                continue
            x, y = word.center
            remaining = number(
                words, (820, y + 20, 1010, y + 65), r"Remaining:\s*(\d+)/3"
            )
            if not remaining or not 0 <= remaining[0] <= 3:
                return unknown
            stars = sum(
                gold(frame, (x0, y + 22, x0 + 11, y + 34)) for x0 in (698, 713, 730)
            )
            target = (
                (1119, y + 13)
                if cyan(frame, (1080, y - 3, 1150, y + 30))
                and has(words, "enter", (1060, y - 15, 1190, y + 45))
                else None
            )
            stages.append(APStage(word.text, stars, target, remaining[0]))
        total = number(words, (105, 556, 340, 611), r"Star Acquisition \((\d+)/9\)")
        if (
            {s.id for s in stages} != {f"{area[0]}-{i}" for i in (1, 2, 3)}
            or not total
            or sum(s.stars for s in stages) != total[0]
        ):
            return unknown
        return APScreen(
            "hard_list",
            ap=ap,
            area=area[0],
            stages=tuple(stages),
            left=left,
            right=right,
        )
    if home:
        return APScreen("home", ap=ap)
    return unknown


class APVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        if has(words, "mission info", (440, 80, 850, 130)) and not any(
            re.match(r"^[1-9]\d?-[123](?=\D|$)", w.text)
            for w in within(words, (130, 220, 620, 280))
        ):
            # Some detail headers omit the small index entirely. Require two
            # matching enlarged reads of that index, not the adjacent name.
            reads = []
            for scale in (2, 3):
                crop = cv2.resize(frame[220:271, 135:200], None, fx=scale, fy=scale)
                labels = [
                    w
                    for w in self.startup.read(crop)
                    if re.fullmatch(r"[1-9]\d?-[123]", w.text)
                ]
                reads.append(labels[0] if len(labels) == 1 else None)
            if all(reads) and reads[0].text == reads[1].text:
                words.append(
                    Word(
                        reads[0].text,
                        min(w.confidence for w in reads),
                        (135, 220, 200, 271),
                    )
                )
        if has(words, "mission", (80, 0, 230, 50)) and has(
            words, "hard", (1000, 130, 1140, 185)
        ):
            # Small one-digit stage labels can disappear from whole-frame OCR.
            # Read the actual label at a larger scale; never infer its number.
            for top in (216, 331, 445):
                bounds = (684, top, 760, top + 46)
                if not any(
                    re.fullmatch(r"[1-9]\d?-[123]", w.text)
                    for w in within(words, bounds)
                ):
                    x1, y1, x2, y2 = bounds
                    crop = cv2.resize(frame[y1:y2, x1:x2], None, fx=3, fy=3)
                    for word in self.startup.read(crop):
                        if re.fullmatch(r"[1-9]\d?-[123]", word.text):
                            box = tuple(
                                n // 3 + (x1 if i % 2 == 0 else y1)
                                for i, n in enumerate(word.box)
                            )
                            words.append(Word(word.text, word.confidence, box))
        home = {"home_left", "home_right"} <= self.startup.matches(frame).keys()
        return classify_ap(frame, words, home=home)
