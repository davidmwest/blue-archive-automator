"""Bounded navigation shared by paid-pack and mail tasks."""
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from uuid import uuid4

from .runtime import Capture, Journal, RunResult, TaskError
from .shop_vision import ShopVision

LOGGER = logging.getLogger(__name__)
BILLING_PACKAGE = 'com.android.vending'


@dataclass(frozen=True)
class ShopFrame:
    capture: Capture
    screen: object


class ShopRunner:
    task = 'shop'
    allows_billing = False

    def __init__(self, config, device, startup, *, vision=None, monotonic=time.monotonic,
                 sleep=time.sleep, wall_clock=lambda: datetime.now(timezone.utc)):
        self.config, self.device = config, device
        self.vision = vision or ShopVision(startup)
        self.clock, self.sleep, self.wall_clock = monotonic, sleep, wall_clock
        self.started, self.actions, self.last_frame = monotonic(), 0, None
        self.run_dir = config.run_dir / f'{self.task}-{wall_clock().strftime("%Y%m%dT%H%M%S")}-{uuid4().hex[:8]}'
        self.journal = Journal(self.run_dir, monotonic, self.started)

    def fail(self, message):
        raise TaskError(message, self.run_dir)

    def budget(self):
        if self.clock() - self.started > 600 or self.actions >= 200:
            self.fail(f'{self.task} reached its time or input limit')

    def phase(self, message):
        LOGGER.info('%s: %s', self.task, message)
        self.journal.record('state', state=self.task, detail=message)

    def capture(self):
        self.budget()
        allowed = {self.config.package}
        if self.allows_billing:
            allowed.add(BILLING_PACKAGE)
        for transition in range(4):
            package = self.device.foreground_package()
            # Android can briefly report no focused window during a transition.
            for attempt in range(3):
                if package is not None:
                    break
                self.journal.record('foreground_pending', task=self.task, attempt=attempt + 1)
                self.sleep(.5)
                package = self.device.foreground_package()
            if package not in allowed:
                self.journal.record('foreground_mismatch', task=self.task,
                                    expected=self.config.package, observed=package)
                self.fail(f'Unexpected foreground during {self.task}: '
                          f'{package or "no focused app"}; no input sent. '
                          'Return to Blue Archive and retry')
            at = self.clock()
            png = self.device.screenshot()
            observed = self.device.foreground_package()
            if observed == package:
                break
            # Discard this frame: it could contain checkout details even when
            # capture started in the game. Never save or act on mixed frames.
            if observed is not None and observed not in allowed:
                self.fail('Foreground changed while reading the screen; no input sent')
            self.journal.record('foreground_transition', task=self.task,
                                before=package, after=observed, attempt=transition + 1)
            self.sleep(.5)
        else:
            self.fail('Foreground did not settle while reading the screen; no input sent')
        billing = package == BILLING_PACKAGE
        # Play screens can contain payment/account details. Never save them to
        # dashboard traces, OCR logs, receipts, or important actions.
        if not billing:
            self.last_frame = self.journal.screenshot(png)
        screen = self.vision.analyze(png, billing=billing)
        return ShopFrame(Capture(png, at, package), screen)

    def wait(self, kinds, *, timeout=40, predicate=lambda screen: True, billing_grace=0):
        kinds = {kinds} if isinstance(kinds, str) else kinds
        end = self.clock() + timeout
        billing_settle_until = self.clock() + billing_grace
        while self.clock() < end:
            frame = self.capture()
            if frame.screen.kind in kinds and predicate(frame.screen) and frame.capture.deadline - self.clock() >= 1:
                return frame
            if frame.screen.kind == 'billing_attention' and self.clock() >= billing_settle_until:
                self.fail('Google Play needs attention. Set up payment in this emulator and complete any verification manually')
            self.sleep(.7)
        self.fail(f'{self.task} did not reach {", ".join(sorted(kinds))}; inspect the local trace')

    def tap(self, frame, target, detail):
        self.budget()
        if target is None or not frame.capture.is_fresh(self.clock()):
            self.fail('No fresh recognized input target')
        if self.device.foreground_package() != frame.capture.foreground:
            self.fail('Foreground changed before input')
        self.journal.record('intent', operation='tap', detail=detail, target=target)
        if frame.capture.foreground == BILLING_PACKAGE:
            sent = self.device.tap_billing(*target, size=frame.screen.size,
                                          deadline=frame.capture.deadline, monotonic=self.clock)
        else:
            sent = self.device.tap(*target, deadline=frame.capture.deadline, monotonic=self.clock)
        if not sent:
            self.fail('Input expired before it could be sent')
        self.actions += 1
        self.sleep(.7)

    def navigate(self, source, destination, target):
        for attempt in range(3):
            frame = self.wait({source, destination})
            if frame.screen.kind == destination:
                return frame
            self.tap(frame, target, f'Open {destination}')
            self.sleep(2)
            if source == 'home' and destination == 'campaign' and attempt < 2:
                frame = self.wait({source, destination})
                if frame.screen.kind == destination:
                    return frame
                # After startup, clear Home has remained visible while Campaign
                # ignored input; a later normal tap succeeded. Pace only these
                # retries, then recognize a fresh frame before the next input.
                self.phase('Campaign has not opened; letting the home menu settle before retrying')
                self.sleep(10)
        return self.wait(destination)

    def home(self):
        self.wait('home')
        self.sleep(5)
        frame = self.wait('home')
        self.journal.save_image('home.png', frame.capture.png)

    def finish(self, status='success'):
        self.journal.record('finished', status=status, actions=self.actions)
        return RunResult(status, self.run_dir, self.clock() - self.started, self.actions)
