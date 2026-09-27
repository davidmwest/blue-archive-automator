"""Local, fixed English recognition for Bounty and Scrimmage sweeps."""

from dataclasses import dataclass, replace
import math
import re
import cv2
from .vision import decode_native_frame, native_game_region, read_game_words, Word, decode_frame
from .crafting_vision import within, number, bright, cyan
from .shop_vision import has, text_in
from .ap_vision import APStage, ap_value, gold, mission_stars, classify_ap
from .ticket_state import AREAS

STAGE_NAMES = {area: area for areas in AREAS.values() for area in areas}
STAGE_NAMES.update(
    {"Desert Railroad": "Abandoned Train", "Classroom": "Besieged Classroom"}
)


@dataclass(frozen=True)
class TicketScreen:
    kind: str
    task: str | None = None
    area: str | None = None
    stage: str | None = None
    ap: int | None = None
    tickets: int | None = None
    after_tickets: int | None = None
    after_ap: int | None = None
    count: int | None = None
    ap_cost: int | None = None
    stars: int | None = None
    stages: tuple[APStage, ...] = ()
    target: tuple[int, int] | None = None
    rewards: tuple[str, ...] = ()


def ticket_counter(words, bounds):
    # OCR splits single-digit counters from their label on both chooser/list.
    text = " ".join(
        w.text for w in sorted(within(words, bounds), key=lambda w: w.box[0])
    )
    match = re.fullmatch(r"(?:Tickets Owned|Bounty Ticket)\s*(\d+)/(\d+)", text)
    return tuple(map(int, match.groups())) if match else None


def reread_ticket_projection(native, reader, words):
    """Recover an incomplete projection only from agreeing native crop reads."""
    bounds = (1010, 337, 1108, 384)
    pieces = sorted(within(words, bounds), key=lambda word: word.box[0])
    # Do not replace a complete, contradictory projection or reinterpret
    # Scrimmage's adjacent AP counter. Native whole-frame OCR can also
    # omit the arrow, final digit, or entire projection. Every surviving digit
    # must agree with both complete crop readings. An absent projection is not
    # a zero balance: the crops must still independently prove both values.
    if (len(pieces) not in (0, 1, 2)
            or any(not math.isfinite(word.confidence) or word.confidence < .95 for word in pieces)
            or any(not re.fullmatch(r"\d+", word.text.strip()) for word in pieces)):
        return None
    partial = len(pieces) == 1
    if partial:
        if not 1030 <= pieces[0].center[0] <= 1060:
            return None
    elif pieces:
        if not 1030 <= pieces[0].center[0] < pieces[1].center[0] <= 1100:
            return None
    observed_digits = tuple(int(word.text) for word in pieces)
    expected = None
    confidence = []
    # Isolate the entire number/arrow row without the ticket icon or bubble
    # border. A little vertical margin avoids clipping thin arrow/digit edges.
    # Two scales must independently read the exact same complete projection.
    for crop_bounds, scale in (((1025, 346, 1100, 381), 3),
                               ((1025, 346, 1100, 381), 4)):
        crop = native_game_region(native, crop_bounds)
        width, height = crop_bounds[2] - crop_bounds[0], crop_bounds[3] - crop_bounds[1]
        enlarged = cv2.resize(crop, (width * scale, height * scale),
                              interpolation=cv2.INTER_CUBIC)
        padded = cv2.copyMakeBorder(enlarged, 8 * scale, 8 * scale,
                                    8 * scale, 8 * scale, cv2.BORDER_CONSTANT,
                                    value=(255, 255, 255))
        observed = reader.read(padded)
        if (len(observed) != 1 or not math.isfinite(observed[0].confidence)
                or observed[0].confidence < .95):
            return None
        match = re.fullmatch(r"(\d+)\s*→\s*(\d+)", observed[0].text.strip())
        if not match:
            return None
        projection = tuple(map(int, match.groups()))
        if (pieces and (projection[:1] if partial else projection) != observed_digits
                or expected is not None and projection != expected):
            return None
        expected = projection
        confidence.append(observed[0].confidence)
    return Word(f"{expected[0]}→{expected[1]}", min(confidence), crop_bounds)


def classify_tickets(frame, words, *, home=False):
    ap = ap_value(words)
    unknown = TicketScreen("unknown", ap=ap)
    task = next(
        (
            t
            for t, title in [("bounties", "bounty"), ("scrimmages", "scrimmage")]
            if has(words, title, (80, 0, 310, 55))
        ),
        None,
    )
    # Reuse the same game's receipt layout, but keep the ticket task's identity.
    receipt = classify_ap(frame, words)
    if receipt.kind == "receipt":
        return TicketScreen(
            "receipt",
            task=task,
            ap=ap,
            count=receipt.count,
            target=receipt.target,
            rewards=receipt.rewards,
        )
    if task is None:
        if home:
            return TicketScreen("home", ap=ap)
        if receipt.kind == "campaign":
            return TicketScreen("campaign", ap=ap)
        return unknown
    confirmation = text_in(words, (390, 292, 883, 384))
    match = (
        re.fullmatch(
            r"Use (\d+) Bounty Ticket to Sweep (\d+) time\(s\)\?", confirmation
        )
        if task == "bounties"
        else re.fullmatch(
            r"Use Scrimmage Ticket (\d+), AP (\d+) to Sweep (\d+) time\(s\)\?",
            confirmation,
        )
    )
    if (
        match
        and has(words, "notice", (500, 130, 780, 200))
        and has(words, "confirm", (650, 465, 880, 545))
        and cyan(frame, (660, 480, 860, 530))
    ):
        values = list(map(int, match.groups()))
        tickets, cost, count = (
            (values[0], 0, values[1]) if task == "bounties" else values
        )
        if tickets != count or count < 1:
            return unknown
        return TicketScreen(
            "confirm",
            task=task,
            ap=ap,
            tickets=tickets,
            count=count,
            ap_cost=cost,
            target=(767, 509),
        )
    if (
        has(words, "mission info", (440, 110, 850, 170))
        and bright(frame, (420, 125, 495, 152))
        and has(words, "sweep", (850, 200, 1010, 262))
    ):
        # Native OCR may join the stage index to its title across the thin
        # separator ("08Overpass H"). Parse the complete heading so both split
        # and joined observations still have to prove the index/letter pair.
        identity = " ".join(word.text for word in sorted(
            within(words, (125, 183, 620, 245)), key=lambda word: word.box[0]
        ))
        match = re.fullmatch(
            r"(\d{1,2})\s*(?:\|\s*)?("
            + "|".join(re.escape(STAGE_NAMES[a]) for a in AREAS[task])
            + r")\s*([A-Z])",
            identity,
        )
        count = number(words, (904, 277, 970, 330))
        # Scrimmage's AP and ticket projections can be separate OCR words.
        # Preserve their boundary: joining 643→643 and 15→14 makes the middle
        # digits ambiguous, and a greedy match can invent AP 6431 / tickets 5.
        projection = " ".join(
            word.text for word in sorted(
                within(words, (855, 337, 1108, 384)), key=lambda word: word.box[0]
            )
        ).strip()
        # The exhausted selector displays an explicit 0 -> dash, quantity zero.
        # This is a read-only balance observation; it never supplies a tap target.
        if count and count[0] == 0:
            projection = re.sub(r"0\s*→\s*-$", "0→0", projection)
        pattern = (
            r"[^0-9]*(\d+)\s*→\s*(\d+)"
            if task == "bounties"
            else r"[^0-9]*(\d+)\s*→\s*(\d+)(?:\s*\|\s*|\s+)[^0-9]*(\d+)\s*→\s*(\d+)"
        )
        projected = re.fullmatch(pattern, projection)
        if not (match and count and projected and ap is not None):
            return unknown
        index, title_name, stage = match.groups()
        area = next(a for a in AREAS[task] if STAGE_NAMES[a] == title_name)
        if int(index) != ord(stage) - 64:
            return unknown
        values = list(map(int, projected.groups()))
        before_ap, after_ap, tickets, after_tickets = (
            (ap, ap, *values) if task == "bounties" else values
        )
        qty = count[0]
        if (
            before_ap != ap
            or after_ap > ap
            or not 0 <= after_tickets <= tickets <= 999
            or tickets - after_tickets != qty
            or (qty == 0 and before_ap != after_ap)
            or (qty and (before_ap - after_ap) % qty)
        ):
            return unknown
        cost = (before_ap - after_ap) // qty if qty else None
        return TicketScreen(
            "detail",
            task=task,
            area=area,
            stage=stage,
            ap=ap,
            tickets=tickets,
            after_tickets=after_tickets,
            after_ap=after_ap,
            count=qty,
            ap_cost=cost,
            stars=mission_stars(frame),
            target=(
                (937, 405)
                if qty
                and has(words, "start sweep", (770, 375, 1110, 444))
                and cyan(frame, (810, 385, 1050, 430))
                else None
            ),
        )
    if not bright(frame, (320, 5, 390, 32)):
        return unknown
    chooser = "location select" if task == "bounties" else "academy select"
    if has(words, chooser, (620, 75, 950, 135)):
        tickets = ticket_counter(words, (80, 78, 305, 125))
        if (
            tickets
            and 0 <= tickets[0] <= 999
            and all(has(words, a, (970, 140, 1250, 470)) for a in AREAS[task])
        ):
            return TicketScreen("menu", task=task, tickets=tickets[0], ap=ap)
    if has(words, "stage list", (820, 80, 1030, 136)):
        # The area's title becomes readable during its crossfade, before the
        # stars and Enter buttons reach their final colors. Require the solid
        # navy header to have settled before reporting any star count.
        header = frame[97:118, 820:850].astype("int16")
        if float(abs(header - (114, 76, 45)).mean()) > 8:
            return unknown
        heading = (
            text_in(words, (90, 130, 620, 183))
            .replace(" ", "")
            .replace("-", "")
            .lower()
        )
        area = next(
            (
                a
                for a in AREAS[task]
                if heading
                == (("Bounty" if task == "bounties" else "") + a)
                .replace(" ", "")
                .lower()
            ),
            None,
        )
        tickets = ticket_counter(words, (125, 180, 365, 215))
        if area is None or not tickets or not 0 <= tickets[0] <= 999:
            return unknown
        stages = []
        for word in within(words, (760, 150, 1065, 680)):
            match = re.fullmatch(
                re.escape(STAGE_NAMES[area]) + r"\s*([A-Z])", word.text
            )
            if not match:
                continue
            # Stage name is centered 13 px below its numerical index.
            y = word.center[1] - 13
            if not 147 <= y <= 641:
                continue
            stars = sum(
                gold(frame, (x, y + 22, x + 11, y + 34)) for x in (698, 713, 730)
            )
            target = (
                (1119, y + 13)
                if cyan(frame, (1080, y - 3, 1150, y + 30))
                and has(words, "enter", (1060, y - 15, 1190, y + 45))
                else None
            )
            stages.append(APStage(match[1], stars, target))
        if stages:
            return TicketScreen(
                "list",
                task=task,
                area=area,
                ap=ap,
                tickets=tickets[0],
                stages=tuple(sorted(stages, key=lambda s: s.id)),
            )
    return unknown


class TicketVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = read_game_words(png, self.startup)
        home = {"home_left", "home_right"} <= self.startup.matches(frame).keys()
        screen = classify_tickets(frame, words, home=home)
        count = number(words, (904, 277, 970, 330))
        ap_projection = within(words, (855, 337, 1010, 384))
        ticket_projection_recoverable = (
            has(words, "bounty", (80, 0, 310, 55))
            or (has(words, "scrimmage", (80, 0, 310, 55))
                and len(ap_projection) == 1
                and math.isfinite(ap_projection[0].confidence)
                and ap_projection[0].confidence >= .95
                and re.fullmatch(r"\d+\s*→\s*\d+", ap_projection[0].text.strip()))
        )
        if (screen.kind == "unknown" and count and count[0] > 0
                and ticket_projection_recoverable
                and has(words, "mission info", (440, 110, 850, 170))
                and has(words, "sweep", (850, 200, 1010, 262))):
            projection = reread_ticket_projection(
                decode_native_frame(png), self.startup, words,
            )
            if projection is not None:
                pieces = within(words, (1010, 337, 1108, 384))
                repaired = [word for word in words if word not in pieces]
                screen = classify_tickets(frame, [*repaired, projection], home=home)
        if screen.kind != "unknown" or count != (0,):
            return screen
        # Whole-frame OCR can omit part or all of the exhausted ticket label.
        # Recovery needs agreeing enlarged observations; a zero quantity alone
        # is not evidence that the ticket balance is zero.
        candidates = [
            word for word in within(words, (1030, 337, 1108, 384))
            if re.fullmatch(r"0\s*→", word.text.strip())
        ]
        missing_scrimmage_projection = (
            not within(words, (1030, 337, 1108, 384))
            and has(words, "scrimmage", (80, 0, 310, 55))
        )
        if len(candidates) == 1:
            candidate = candidates[0]
            recovered_words = [
                replace(word, text="0→-") if word is candidate else word
                for word in words
            ]
            bounds, margin = (1010, 341, 1105, 392), 0
        elif missing_scrimmage_projection:
            # Scrimmage may omit the entire exhausted ticket projection. Its
            # AP projection must still independently prove no resource change.
            recovered_words = [*words, Word("0→-", 1.0, (1040, 348, 1093, 378))]
            # Exclude the ticket icon and neighboring AP digits; leave blank
            # margin so the dash is not confused with the bubble's edge.
            bounds, margin = (1040, 348, 1093, 378), 8
        else:
            return screen
        recovered = classify_tickets(frame, recovered_words)
        if (recovered.kind, recovered.count, recovered.tickets,
                recovered.after_tickets, recovered.target) != ("detail", 0, 0, 0, None):
            return screen
        native = decode_native_frame(png)
        crop = native_game_region(native, bounds)
        padding = round(margin * native.shape[1] / 1280)
        if padding:
            crop = cv2.copyMakeBorder(crop, padding, padding, padding, padding,
                                      cv2.BORDER_CONSTANT, value=(255, 255, 255))
        width, height = bounds[2] - bounds[0] + margin * 2, bounds[3] - bounds[1] + margin * 2
        # Both scales must independently read the complete zero projection.
        for scale in (2, 3):
            enlarged = cv2.resize(crop, (width * scale, height * scale),
                                  interpolation=cv2.INTER_CUBIC)
            observed = self.startup.read(enlarged)
            if (len(observed) != 1 or not math.isfinite(observed[0].confidence)
                    or observed[0].confidence < .9
                    or not re.fullmatch(r"0\s*→\s*-", observed[0].text.strip())):
                return screen
        return recovered
