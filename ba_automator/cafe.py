"""Cafe earnings and relationship visits using local image recognition only."""
from datetime import datetime, timezone
import logging
import re
import time
from uuid import uuid4

from .actions import record_action
from .cafe_vision import CafeVision
from .cafe_camera import measure_camera_displacement
from .locking import InstanceLock
from .restart import _Journal, RunResult, RestartError, FRAME_MAX_AGE, HOME_STABLE_SECONDS
from .vision import decode_frame

LOGGER = logging.getLogger(__name__)
CAFE_TIMEOUT = 900
# Scene motion is measured after each slow drag; furniture is never classified.
PAN_RIGHT = ((480, 350), (830, 350))
PAN_LEFT = ((830, 350), (480, 350))
PAN_DOWN = ((750, 250), (750, 500))
PAN_UP = ((750, 500), (750, 250))


def text_of(words):
    return " ".join(word.normalized for word in words)


def floor_from_switch(words):
    """Identify one of the two unlocked English Cafe floors from its switch."""
    destinations = set(re.findall(r"\bmove to cafe no ([12])\b", text_of(words)))
    return 3 - int(next(iter(destinations))) if len(destinations) == 1 else None


def find_button(words, labels, *, bounds=(280, 350, 1020, 680)):
    x1, y1, x2, y2 = bounds
    hits = [w for w in words if w.normalized in labels
            and x1 <= w.center[0] <= x2 and y1 <= w.center[1] <= y2]
    return hits[0].center if len(hits) == 1 else None


def earnings_amounts(words):
    """Read the three fixed Cafe storage columns; unknown numbers remain unknown."""
    columns = {}
    ambiguous = set()
    for word in words:
        if not 415 <= word.center[1] <= 460:
            continue
        match = re.fullmatch(r"([\d, ]+)\s*/\s*([\d, ]+)", word.text.strip())
        if not match:
            continue
        numerator = re.sub(r"\D", "", match[1])
        denominator = re.sub(r"\D", "", match[2])
        if not numerator or not denominator:
            continue
        amount = int(numerator)
        for name, left, right in (("credits1", 300, 525), ("ap", 530, 755), ("credits2", 760, 985)):
            if left <= word.center[0] <= right:
                if name in ambiguous:
                    continue
                if name in columns and columns[name] != amount:
                    columns.pop(name)
                    ambiguous.add(name)
                else:
                    columns[name] = amount
    return columns


def reward_amounts(words):
    amounts = {}
    ambiguous = set()
    labels = [w for w in words if w.normalized in {"ap", "credit points"}
              and 230 <= w.center[1] <= 340]
    for label in labels:
        for w in words:
            if abs(w.center[0] - label.center[0]) > 75 or not 405 <= w.center[1] <= 480:
                continue
            match = re.fullmatch(r"[xX×]?\s*([\d,]+)", w.text.strip())
            if match:
                digits = match[1].replace(",", "")
                if not digits:
                    continue
                key = "ap" if label.normalized == "ap" else "credits"
                value = int(digits)
                if key in ambiguous:
                    continue
                if key in amounts and amounts[key] != value:
                    amounts.pop(key)
                    ambiguous.add(key)
                else:
                    amounts[key] = value
    return amounts


def exact_invitation_name(words, name):
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return bool(normalized) and sum(w.normalized == normalized for w in words) == 1


def invitation_panel(words):
    """Ignore the dim Cafe HUD, including its separate Bonus Invitation control."""
    return [w for w in words if 380 <= w.center[0] <= 920 and 65 <= w.center[1] <= 625]


def invitation_cooldown(words):
    return any(815 <= w.center[0] <= 975 and 560 <= w.center[1] <= 630
               and re.fullmatch(r"\s*\d{1,2}:\d{2}:\d{2}\s*", w.text) for w in words)


def invitation_cooldown_notice(words):
    return "you can send an invite after the cooldown is over" in text_of(invitation_panel(words))


def invitation_rows(words):
    """Join wrapped identities against their own OCR-visible row Invite control.

    The English list's name column is x488..704; row controls are around x787,
    separated by roughly 78 pixels. Names alone never become tap coordinates.
    """
    controls = sorted([w for w in words if 745 <= w.center[0] <= 835 and 194 <= w.center[1] <= 588
                       and w.normalized in {"invite", "invited", "visiting", "in cafe"}],
                      key=lambda w: w.center[1])
    groups = [[] for _ in controls]
    for word in words:
        if not (488 <= word.center[0] <= 704 and 194 <= word.center[1] <= 588) or not word.normalized:
            continue
        if re.match(r"^(?:rank|relationship|lv|level|bond)(?: |$)", word.normalized) or word.normalized.isdecimal():
            continue
        distances = [abs(word.center[1] - control.center[1]) for control in controls]
        if not distances or min(distances) > 36 or distances.count(min(distances)) != 1:
            continue
        groups[distances.index(min(distances))].append(word)
    rows = []
    for control, parts in zip(controls, groups):
        parts.sort(key=lambda w: (w.center[1], w.center[0]))
        identity = " ".join(w.normalized for w in parts)
        if identity:
            rows.append({"identity": identity, "target": control.center,
                         "enabled": control.normalized == "invite"})
    return rows


def invitation_list(words):
    header = [w for w in words if 400 <= w.center[0] <= 850 and 65 <= w.center[1] <= 175]
    heading = (any(w.normalized in {"invitation", "invite", "momotalk", "momo talk", "free invitation"}
                   for w in header) or bool(re.search(r"\bmomo talk\b", text_of(header))))
    # A confirmation can leave list text visible underneath; do not tap through it.
    choices = any(w.normalized in {"confirm", "yes", "no", "ok", "cancel"}
                  and 400 <= w.center[0] <= 950 and 390 <= w.center[1] <= 620 for w in words)
    return heading and bool(invitation_rows(words)) and not choices


def invitation_block_reason(words):
    text = text_of(invitation_panel(words))
    if any(term in text for term in ("pyroxene", "purchase", "payment", "bonus invitation",
                                     "paid invitation", "spend", "cost", "ticket")):
        return "Invitation screen includes a purchase path; only the normal free invitation is supported"
    if any(term in text for term in ("other cafe", "another cafe", "already in", "already visiting",
                                     "currently in", "currently visiting", "replace", "substitute",
                                     "swap", "move from", "move this student", "change cafe")):
        return "Invitation would replace or move a student between Cafes; review it manually"
    return None


def invitation_confirmation(words, name):
    """Require a normal invitation dialog after the exact row was selected."""
    panel = invitation_panel(words)
    rows = invitation_rows(words)
    button = find_button(panel, {"confirm"}, bounds=(440, 390, 930, 590))
    if button is None and not rows:
        button = find_button(panel, {"invite"}, bounds=(440, 390, 930, 590))
    if button is None or invitation_block_reason(words):
        return None
    expected = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if not expected:
        return None
    for word in panel:
        prompt = word.normalized
        match = re.fullmatch(r"(?:would you like|do you want) to (?:invite|send an invitation to|send an invite to) (.+)", prompt)
        if match:
            recipient = re.split(r" to (?:the |your )?cafe\b", match[1], maxsplit=1)[0]
            return button if recipient in {expected, "this student", "the selected student", "selected student"} else None
        if prompt in {"would you like to send an invitation", "do you want to send an invitation",
                      "would you like to send an invite", "do you want to send an invite"}:
            return button
    # Some clients display the identity in its own field instead of a question.
    # Do not use names from an OCR-visible background list as modal evidence.
    heading = any(w.normalized in {"invitation", "free invitation", "invite"}
                  and 140 <= w.center[1] <= 220 for w in panel)
    identity = " ".join(w.normalized for w in sorted(panel, key=lambda w: (w.center[1], w.center[0]))
                        if 430 <= w.center[0] <= 820 and 230 <= w.center[1] <= 390)
    return button if not rows and heading and identity == expected else None


class CafeRunner:
    def __init__(self, config, device, vision, *, monotonic=time.monotonic, sleep=time.sleep):
        self.config, self.device, self.startup = config, device, vision
        self.vision = CafeVision(vision)
        self.clock, self.sleep = monotonic, sleep
        self.started = self.clock()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.run_dir = config.run_dir / f"cafe-{stamp}-{uuid4().hex[:8]}"
        self.journal = _Journal(self.run_dir, monotonic, self.started)
        self.actions = 0
        self.confirmed = 0
        self.floor = None
        self.last_frame = None

    def fail(self, message):
        raise RestartError(message, self.run_dir)

    def capture(self, *, ocr=False):
        if self.clock() - self.started >= CAFE_TIMEOUT:
            self.fail("Cafe exceeded its fifteen-minute time limit")
        captured = self.clock()
        png = self.device.screenshot()
        frame = decode_frame(png)
        self.last_frame = self.journal.screenshot(png)
        if self.device.foreground_package() != self.config.package:
            self.fail("Blue Archive left the foreground during Cafe; no further input was sent")
        words = self.vision.words(frame) if ocr else []
        return captured, png, frame, words

    def tap(self, capture, target, detail):
        captured, _, _, _ = capture
        self.journal.record("intent", operation="tap", task="cafe", detail=detail,
                            target=list(target), frame=self.last_frame)
        sent = self.device.tap(*target, deadline=captured + FRAME_MAX_AGE, monotonic=self.clock)
        self.journal.record("outcome", operation="tap", result="ok" if sent else "skipped_stale")
        if not sent:
            self.fail("Cafe frame expired before input; run again with the emulator responsive")
        self.actions += 1

    def phase(self, detail):
        LOGGER.info("cafe: %s", detail)
        self.journal.record("state", state="cafe", detail=detail, frame=self.last_frame)

    def wait_cafe(self, timeout=30):
        end = self.clock() + timeout
        while self.clock() < end:
            cap = self.capture()
            if self.vision.is_cafe(cap[2]):
                return cap
            self.sleep(.8)
        self.fail("Cafe screen did not become unobstructed; review the screenshot trace")

    def wait_words(self, predicate, timeout=20, *, require_cafe=False,
                   failure="Expected Cafe dialog did not appear; review the screenshot trace"):
        end = self.clock() + timeout
        while self.clock() < end:
            cap = self.capture(ocr=True)
            if predicate(cap[3]) and (not require_cafe or self.vision.is_cafe(cap[2])):
                return cap
            self.sleep(.6)
        self.fail(failure)

    def wait_floor(self, expected=None):
        return self.wait_words(
            lambda words: floor_from_switch(words) in (1, 2)
            and (expected is None or floor_from_switch(words) == expected),
            timeout=30, require_cafe=True,
            failure="Cafe floor availability could not be verified from its switch label; "
                    "this routine requires both unlocked floors in the English client",
        )

    def enter(self):
        cap = self.capture()
        if not self.vision.is_cafe(cap[2]):
            observation = self.startup.analyze(cap[1])
            if observation.state != "home":
                self.fail("Cafe requires an unobstructed home screen; run restart first")
            self.tap(cap, (100, 659), "Open Cafe from verified home")
        self.wait_cafe()

    def collect(self):
        self.phase("Collecting Cafe AP and credits")
        self.tap(self.wait_cafe(), (1165, 655), "Open Cafe earnings")
        cap = self.wait_words(lambda w: "cafe earnings" in text_of(w) and "earnings status" in text_of(w))
        amounts = earnings_amounts(cap[3])
        claim = find_button(cap[3], {"claim"})
        if not claim:
            self.fail("Cafe earnings dialog has no unambiguous Claim button")
        if set(amounts) == {"credits1", "ap", "credits2"} and all(amount == 0 for amount in amounts.values()):
            self.journal.record("earnings", result="empty")
        else:
            self.tap(cap, claim, "Collect available Cafe earnings")
            record_action(self.config, "earnings_claim_attempted", "Pressed Claim on Cafe earnings.", cafe=self.floor)
            cap = self.wait_words(lambda w: "reward acquired" in text_of(w), timeout=25)
            receipt = reward_amounts(cap[3])
            self.journal.save_image(f"earnings-{self.floor}.png", cap[1])
            detail = "Cafe reward receipt verified"
            if receipt:
                detail += ": " + ", ".join(f"{amount:,} {name.upper() if name == 'ap' else name}" for name, amount in receipt.items())
            record_action(self.config, "earnings_collected", detail + ".", cafe=self.floor, **receipt)
            self.tap(cap, (640, 630), "Dismiss verified Cafe reward receipt")
            cap = self.wait_words(lambda w: "cafe earnings" in text_of(w) and "reward acquired" not in text_of(w))
        self.tap(cap, (982, 145), "Close verified Cafe earnings dialog")
        self.wait_cafe()

    def pan(self, vector):
        cap = self.wait_cafe()
        self.journal.record("intent", operation="pan", start=list(vector[0]), end=list(vector[1]))
        if not self.device.swipe(*vector, duration_ms=1800,
                                 deadline=cap[0] + FRAME_MAX_AGE, monotonic=self.clock):
            self.fail("Cafe view expired before camera movement")
        self.sleep(.5)
        displacement = None
        diagnostics = {}
        for attempt in range(3):
            after = self.wait_cafe()
            displacement = measure_camera_displacement(cap[2], after[2], diagnostics=diagnostics)
            if displacement is not None:
                break
            if attempt < 2:
                self.sleep(.8)  # Let a speech bubble or animation settle; no extra drag.
        self.journal.record("camera_motion", displacement=displacement,
                            diagnostics=diagnostics, frame=self.last_frame)
        return displacement

    def pan_region(self, vector, *, to_edge=False):
        """Move one overlapping view, or normalize to a verified camera edge.

        Two separately observed stationary drags establish an edge. An unknown
        short drag triggers a student scan and resets movement evidence; it never
        counts as distance or an edge. All attempts have a hard bound.
        """
        axis = 0 if vector[0][0] != vector[1][0] else 1
        direction = 1 if vector[1][axis] > vector[0][axis] else -1
        distance = 0.0
        stationary = 0
        # Scene interior is 1010 by 440 pixels; these steps overlap generously.
        step = 450 if axis == 0 else 220
        for _ in range(12):
            motion = self.pan(vector)
            if motion is None:
                # The fixed-profile drags are short enough to check this view
                # before moving again, even when animation hides scene matches.
                self.pet_visible()
                distance = 0.0
                stationary = 0
                continue
            movement = motion[axis] * direction
            if abs(motion[1-axis]) > max(12, abs(movement) * .3) or movement < -5:
                self.fail("Cafe camera moved unexpectedly; inspect the saved view")
            if abs(movement) < 4:
                stationary += 1
                if stationary >= 2:
                    return True
            else:
                stationary = 0
                distance += movement
                if not to_edge and distance >= step:
                    return False
        self.fail("Cafe camera coverage could not be verified after twelve drags")

    def clear_relationship_popup(self):
        """Dismiss recognized feedback and return whether a rank increase was seen."""
        cap = self.capture(ocr=True)
        if self.vision.is_cafe(cap[2]):
            return False
        text = text_of(cap[3])
        if any(w.normalized == "relationship rank up" and 530 <= w.center[1] <= 680 for w in cap[3]):
            self.journal.save_image(f"rank-up-{self.actions}.png", cap[1])
            record_action(self.config, "relationship_rank_increased", "Relationship rank-up screen verified.", cafe=self.floor)
            self.tap(cap, (640, 630), "Dismiss the verified relationship rank-up screen")
            self.wait_cafe()
            return True
        if "relationship rank" in text:
            button = find_button(cap[3], {"confirm", "ok", "close"})
            if not button and "touch to continue" in text:
                button = (640, 630)
            if button:
                self.tap(cap, button, "Dismiss relationship rank confirmation")
                self.wait_cafe()
                return "rank up" in text or "increased" in text
        self.wait_cafe(timeout=8)
        return False

    def pet_visible(self):
        misses = 0
        attempts = 0
        while misses < 3 and attempts < 12:
            cap = self.wait_cafe()
            markers = self.vision.markers(cap[2])
            if not markers:
                misses += 1
                self.sleep(1.1)
                continue
            misses = 0
            target = markers[0][1]
            self.tap(cap, target, "Tap student with a detected relationship icon")
            attempts += 1
            record_action(self.config, "student_tap_attempted", "Tapped a detected student relationship icon.",
                          cafe=self.floor, target=list(target))
            confirmed = False
            # Feedback may arrive after a short server round-trip.
            for _ in range(5):
                self.sleep(.4)
                after = self.capture()
                if self.vision.relationship_feedback(cap[2], after[2], target):
                    confirmed = True
                    self.confirmed += 1
                    name = f"relationship-{self.floor}-{self.confirmed}.png"
                    self.journal.save_image(name, after[1])
                    record_action(self.config, "relationship_increased", "Relationship heart feedback verified.",
                                  cafe=self.floor, evidence=str(self.run_dir / name))
                    break
            self.sleep(.8)
            rank_increased = self.clear_relationship_popup()
            # One student tap can produce both a heart and a rank-up overlay.
            if rank_increased and not confirmed:
                self.confirmed += 1
            self.journal.record("student_interaction", cafe=self.floor, target=list(target),
                                confirmed=confirmed or rank_increased,
                                heart_feedback=confirmed, rank_increased=rank_increased)
        if attempts >= 12:
            self.fail("Cafe markers remained after twelve attempts in one view")

    def sweep(self):
        self.phase(f"Checking student icons in Cafe {self.floor}")
        self.pet_visible()
        self.pan_region(PAN_RIGHT, to_edge=True)
        self.pan_region(PAN_DOWN, to_edge=True)
        horizontal = PAN_LEFT
        views = 0
        for row in range(8):
            for column in range(8):
                views += 1
                self.phase(f"Cafe {self.floor}: checking camera view {views}")
                self.pet_visible()
                if self.pan_region(horizontal):
                    # A final shorter movement may expose students at the edge.
                    self.pet_visible()
                    break
            else:
                self.fail("Cafe camera width exceeded the bounded scan")
            at_bottom = self.pan_region(PAN_UP)
            if at_bottom:
                # Check the final row even when the remaining movement is short.
                horizontal = PAN_RIGHT if horizontal == PAN_LEFT else PAN_LEFT
                for _ in range(8):
                    views += 1
                    self.pet_visible()
                    if self.pan_region(horizontal):
                        self.pet_visible()
                        record_action(self.config, "cafe_scan_completed",
                                      f"Checked {views} overlapping camera views and verified the room's camera boundaries.",
                                      cafe=self.floor)
                        return
                self.fail("Cafe final camera row exceeded the bounded scan")
            horizontal = PAN_RIGHT if horizontal == PAN_LEFT else PAN_LEFT
        self.fail("Cafe camera height exceeded the bounded scan")

    def invite(self):
        if not self.config.cafe_invite_enabled:
            return False
        cap = self.capture(ocr=True)
        if not self.vision.is_cafe(cap[2]):
            self.fail("Cafe must be unobstructed before checking invitations")
        if invitation_cooldown(cap[3]):
            self.phase(f"Cafe {self.floor}: free invitation is cooling down")
            return False
        name = self.config.cafe_invite_student
        normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        if not normalized:
            self.fail("The configured invitation name has no recognized English identity")

        def check_dialog(screen):
            reason = invitation_block_reason(screen[3])
            if reason and reason.startswith("Invitation would") and invitation_list(screen[3]):
                return  # Another row's visiting status is not the selected student's confirmation.
            if reason:
                self.fail(reason)

        def dismiss_cooldown(screen):
            button = find_button(invitation_panel(screen[3]), {"confirm"}, bounds=(500, 440, 780, 560))
            if not invitation_cooldown_notice(screen[3]) or button is None:
                self.fail("Invitation cooldown notice has no unambiguous Confirm button")
            self.tap(screen, button, "Dismiss the recognized invitation cooldown notice")
            self.phase(f"Cafe {self.floor}: free invitation is cooling down")

        def restore_cafe(require_new_cooldown, notice_closed=False):
            end = self.clock() + 30
            list_closed = False
            while self.clock() < end:
                screen = self.capture(ocr=True)
                check_dialog(screen)
                if invitation_cooldown_notice(screen[3]):
                    if not notice_closed:
                        dismiss_cooldown(screen)
                        notice_closed = True
                    require_new_cooldown = False
                elif invitation_list(screen[3]):
                    if not list_closed:
                        # The known list heading and name/control rows identify this X.
                        self.tap(screen, (837, 95), "Close the verified MomoTalk invitation list")
                        list_closed = True
                elif self.vision.is_cafe(screen[2]):
                    if not require_new_cooldown or invitation_cooldown(screen[3]):
                        return require_new_cooldown
                self.sleep(.6)
            self.fail("Invitation result could not be verified from the Cafe cooldown")

        def wait_list():
            end = self.clock() + 20
            while self.clock() < end:
                screen = self.capture(ocr=True)
                check_dialog(screen)
                if invitation_cooldown_notice(screen[3]) or invitation_list(screen[3]):
                    return screen
                # The previous Cafe frame may remain briefly after opening the list.
                self.sleep(.5)
            self.fail("Free invitation selection was not recognized")

        self.tap(cap, (883, 652), "Open the free Invitation control")
        cap = wait_list()
        for page in range(16):
            if invitation_cooldown_notice(cap[3]):
                dismiss_cooldown(cap)
                restore_cafe(False, notice_closed=True)
                return False
            rows = [row for row in invitation_rows(cap[3]) if row["identity"] == normalized]
            if len(rows) > 1:
                self.fail("Configured invitation name is ambiguous")
            if len(rows) == 1:
                if not rows[0]["enabled"]:
                    self.fail("Configured student does not have an available normal Invite control")
                self.tap(cap, rows[0]["target"], f"Press the free Invite row for {name}")
                break
            if page == 15:
                self.fail(f"Configured student {name!r} was not found in the bounded invitation scan")
            self.journal.record("intent", operation="invitation_scroll", start=[650, 550], end=[650, 250])
            sent = self.device.swipe((650, 550), (650, 250),
                                     deadline=cap[0] + FRAME_MAX_AGE, monotonic=self.clock)
            self.journal.record("outcome", operation="invitation_scroll", result="ok" if sent else "skipped_stale")
            if not sent:
                self.fail("Invitation list frame expired")
            self.sleep(.7)
            cap = wait_list()

        end = self.clock() + 20
        while self.clock() < end:
            cap = self.capture(ocr=True)
            check_dialog(cap)
            if invitation_cooldown_notice(cap[3]):
                dismiss_cooldown(cap)
                restore_cafe(False, notice_closed=True)
                return False
            button = invitation_confirmation(cap[3], name)
            if button is not None:
                self.tap(cap, button, f"Use free invitation for {name}")
                break
            self.sleep(.5)
        else:
            self.fail("The configured student's normal free invitation confirmation was not verified")
        record_action(self.config, "invitation_attempted", f"Requested free invitation for {name}.", student=name, cafe=self.floor)
        if restore_cafe(True):
            record_action(self.config, "student_invited", "Free invitation verified by its new cooldown.", student=name, cafe=self.floor)
            return True
        return False

    def run(self):
        try:
            self.journal.record("started", task="cafe", serial=self.config.serial)
            with InstanceLock(self.config):
                self.device.connect()
                self.device.verify_package()
                self.enter()
                cap = self.wait_floor()
                self.floor = floor_from_switch(cap[3])
                self.collect()

                def move_to_floor(other):
                    if other == self.floor:
                        return
                    cap = self.wait_floor(expected=self.floor)
                    self.tap(cap, (132, 103), f"Move to Cafe {other}")
                    self.wait_floor(expected=other)
                    self.floor = other

                visited = []
                for index in range(2):
                    self.sweep()
                    visited.append(self.floor)
                    if index == 1:
                        break
                    other = 1 if self.floor == 2 else 2
                    move_to_floor(other)
                # Optional recognition cannot prevent required pats on either floor.
                if self.config.cafe_invite_enabled:
                    for floor in reversed(visited):
                        move_to_floor(floor)
                        if self.invite():
                            self.sweep()  # Include the newly invited student.
                            break  # One configured student needs only one invitation.
                self.phase("Returning to home")
                self.tap(self.wait_cafe(), (1237, 24), "Return to home after Cafe")
                end = self.clock() + 30
                home_since = None
                home_count = 0
                while self.clock() < end:
                    cap = self.capture()
                    is_home = self.startup.analyze(cap[1]).state == "home"
                    now = self.clock()
                    if is_home and now - cap[0] <= FRAME_MAX_AGE:
                        home_since = now if home_since is None else home_since
                        home_count += 1
                    else:
                        home_since = None
                        home_count = 0
                    if (home_since is not None and home_count >= self.config.home_confirmations
                            and now - home_since >= HOME_STABLE_SECONDS):
                        self.journal.save_image("home.png", cap[1])
                        self.journal.record("finished", status="success", actions=self.actions, frame="home.png")
                        record_action(self.config, "cafe_visit_completed", f"Cafe visit complete; {self.confirmed} relationship increases verified.")
                        return RunResult("success", self.run_dir, self.clock() - self.started, self.actions)
                    self.sleep(.8)
                self.fail("Cafe completed but the home screen was not verified")
        except KeyboardInterrupt:
            self.journal.record("finished", status="interrupted", actions=self.actions, frame=self.last_frame)
            raise
        except Exception as exc:
            self.journal.record("finished", status="failed", reason=str(exc), actions=self.actions, frame=self.last_frame)
            if isinstance(exc, RestartError):
                raise
            raise RestartError(f"Cafe stopped: {exc}", self.run_dir) from exc
        finally:
            self.journal.close()


def run_cafe(config, device, vision, **kwargs):
    return CafeRunner(config, device, vision, **kwargs).run()
