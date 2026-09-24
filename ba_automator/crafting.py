"""Collect finished crafts and refill vacant slots with the saved Quick Craft preset."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import time
from uuid import uuid4

from .actions import record_action
from .crafting_state import read_state, write_state
from .crafting_vision import CraftVision
from .locking import InstanceLock
from .runtime import Capture, Journal, RunResult, TaskError, HOME_STABLE_SECONDS

LOGGER = logging.getLogger(__name__)
TIMEOUT = 240
TIMER_MARGIN = 15
EMPTY_RECHECK = timedelta(hours=3)


@dataclass(frozen=True)
class CraftFrame:
    capture: Capture
    screen: object
    observed_at: datetime


class CraftingRunner:
    def __init__(self, config, device, vision, *, crafting_vision=None,
                 monotonic=time.monotonic, sleep=time.sleep,
                 wall_clock=lambda: datetime.now(timezone.utc)):
        self.config, self.device = config, device
        self.vision = crafting_vision or CraftVision(vision)
        self.clock, self.sleep, self.wall_clock = monotonic, sleep, wall_clock
        self.started = monotonic()
        self.run_dir = config.run_dir / f"crafting-{wall_clock().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
        self.journal = Journal(self.run_dir, monotonic, self.started)
        self.actions = 0
        self.last_frame = None
        self.state = None

    def fail(self, detail):
        raise TaskError(detail, self.run_dir)

    def budget(self):
        if self.clock() - self.started >= TIMEOUT or self.actions >= 25:
            self.fail('Crafting reached its time or input limit')

    def phase(self, detail):
        LOGGER.info('crafting: %s', detail)
        self.journal.record('state', state='crafting', detail=detail, frame=self.last_frame)

    def capture(self):
        self.budget()
        captured_at, observed_at = self.clock(), self.wall_clock()
        png = self.device.screenshot()
        foreground = self.device.foreground_package()
        if foreground != self.config.package:
            self.fail('Blue Archive left the foreground during Crafting')
        self.last_frame = self.journal.screenshot(png)
        screen = self.vision.analyze(png)
        self.budget()
        return CraftFrame(Capture(png, captured_at, foreground), screen, observed_at)

    def wait(self, kinds, timeout=30, predicate=lambda screen: True):
        if isinstance(kinds, str):
            kinds = {kinds}
        end = self.clock() + timeout
        while self.clock() < end:
            frame = self.capture()
            if (frame.screen.kind in kinds and predicate(frame.screen)
                    and frame.capture.is_fresh(self.clock())
                    and frame.capture.deadline - self.clock() >= 1):
                return frame
            self.sleep(.5)
        self.fail(f'Crafting expected {", ".join(sorted(kinds))}; review the screenshot trace')

    def tap(self, frame, target, detail):
        self.budget()
        if (not isinstance(target, tuple) or len(target) != 2
                or any(type(n) is not int for n in target)
                or not 0 <= target[0] < 1280 or not 0 <= target[1] < 720):
            self.fail('Crafting has no valid recognized tap target')
        if not frame.capture.is_fresh(self.clock()):
            self.fail('Crafting frame expired before input')
        if self.device.foreground_package() != self.config.package:
            self.fail('Blue Archive left the foreground before Crafting input')
        self.journal.record('intent', operation='tap', target=target, detail=detail, frame=self.last_frame)
        sent = self.device.tap(*target, deadline=frame.capture.deadline, monotonic=self.clock)
        self.journal.record('outcome', operation='tap', result='ok' if sent else 'skipped_stale')
        if not sent:
            self.fail('Crafting input expired during device preflight')
        self.actions += 1
        self.sleep(.5)

    def navigate(self, source, destination, target):
        """Retry navigation only while its source is freshly recognized."""
        for attempt in range(3):
            frame = self.wait({source, destination})
            if frame.screen.kind == destination:
                return frame
            self.tap(frame, target, f'Open {destination}')
            self.sleep(2)
        return self.wait(destination)

    def save(self):
        self.state['updated_at'] = self.wall_clock().isoformat()
        write_state(self.config, self.state)

    def settled_list(self):
        """Require repeated slot states so an initial empty/loading view cannot spend."""
        previous = self.wait('list')
        for _ in range(6):
            self.sleep(1)
            frame = self.wait('list')
            if [slot.kind for slot in frame.screen.slots] == [slot.kind for slot in previous.screen.slots]:
                return frame
            previous = frame
        self.fail('Crafting slots did not settle before navigation or spending')

    def observe_slots(self, frame):
        self.state['slots'] = [
            {'slot': slot.number, 'due_at': (frame.observed_at + timedelta(seconds=(slot.seconds or 0) + TIMER_MARGIN)).isoformat()}
            for slot in frame.screen.slots if slot.kind in {'running', 'ready'}]
        self.state['next_check_at'] = None if self.state['slots'] else (self.wall_clock() + EMPTY_RECHECK).isoformat()
        self.save()

    def important(self, action, detail, **extra):
        record_action(self.config, action, detail, task='crafting', **extra)
        self.journal.record('important_action', action=action, detail=detail, **extra)

    def home(self):
        self.navigate('list', 'home', (1237, 24))
        end, since = self.clock() + 30, None
        while self.clock() < end:
            frame = self.capture()
            if frame.screen.kind == 'home' and frame.capture.is_fresh(self.clock()):
                since = self.clock() if since is None else since
                if self.clock() - since >= HOME_STABLE_SECONDS:
                    self.journal.save_image('home.png', frame.capture.png)
                    return
            else:
                since = None
            self.sleep(.5)
        self.fail('Crafting did not return to a stable unobstructed home screen')

    def disable(self, frame, reason):
        self.state['disabled_reason'] = reason
        self.save()
        self.important('crafting_disabled', reason + '. Configure Quick Craft in game, then queue Crafting to retry.', status='disabled')
        if frame.screen.kind == 'disabled':
            self.tap(frame, frame.screen.target, 'Close unconfigured Quick Craft')
            self.wait('list')
        self.home()
        self.phase('Disabled: ' + reason)
        return 'disabled'

    def reconcile(self, frame):
        pending = self.state['pending_action']
        if not pending:
            return
        # A new game screenshot is authoritative after interruption. Never replay
        # an uncertain spend just because the previous subprocess disappeared.
        if pending['kind'] == 'start':
            intended = set(pending.get('slots', []))
            running = {slot.number for slot in frame.screen.slots if slot.kind in {'running', 'ready'}}
            if intended and intended <= running:
                self.important('craft_start_reconciled', 'Previously interrupted craft start is now visible in every expected slot.', status='reconciled')
            else:
                self.fail('A previous craft start has an uncertain result; inspect the saved evidence before retrying')
        else:
            self.fail('A previous craft collection has an uncertain receipt; inspect the saved evidence before retrying')
        self.state['pending_action'] = None
        self.save()

    def collect(self, frame):
        ready = [slot.number for slot in frame.screen.slots if slot.kind == 'ready']
        if not ready:
            return frame
        if frame.screen.collect_target is None:
            self.fail('Finished crafts have no verified active Claim All control')
        self.state['pending_action'] = {'kind': 'collect', 'slots': ready, 'run_dir': str(self.run_dir)}
        self.save()
        self.important('craft_collection_requested', f'Collecting completed craft slots {ready}.', status='attempted', slots=ready)
        self.journal.save_image('before-collection.png', frame.capture.png)
        self.tap(frame, frame.screen.collect_target, 'Claim completed crafts')
        receipt = self.wait('receipt')
        self.journal.save_image('collection-receipt.png', receipt.capture.png)
        self.tap(receipt, receipt.screen.target, 'Dismiss craft receipt')
        result = self.wait('list', predicate=lambda screen: all(slot.kind == 'empty' for slot in screen.slots if slot.number in ready))
        self.important('crafts_collected', f'Collected {len(ready)} completed craft(s); reward receipt and empty slots verified.', status='confirmed', slots=ready, count=len(ready))
        self.state['pending_action'] = None
        self.observe_slots(result)
        return result

    def fill(self, frame):
        empty = [slot.number for slot in frame.screen.slots if slot.kind == 'empty']
        if not empty:
            return 'success'
        if frame.screen.target is None:
            return self.disable(frame, 'Quick Craft is unavailable with an empty crafting slot')
        self.tap(frame, frame.screen.target, 'Open saved Quick Craft preset')
        quick = self.wait({'quick', 'disabled'})
        if quick.screen.kind == 'disabled':
            return self.disable(quick, quick.screen.reason)
        initial = quick.screen
        if initial.required % initial.quantity:
            self.fail('Quick Craft keystone cost does not divide into its selected quantity')
        unit = initial.required // initial.quantity
        count = min(len(empty), initial.owned // unit)
        if count == 0:
            self.phase('No keystones available; leaving empty slots for the next check')
            self.tap(quick, (1206, 102), 'Close Quick Craft without spending')
            self.wait('list')
            return 'success'
        self.tap(quick, (1186, 512), 'Select maximum affordable simultaneous crafts')
        maximum = self.wait('quick', predicate=lambda screen: screen.quantity == count)
        if (maximum.screen.owned != initial.owned or maximum.screen.required != unit * count
                or maximum.screen.required > maximum.screen.owned
                or maximum.screen.credits * initial.quantity != initial.credits * count):
            self.fail('Quick Craft cost changed while choosing the maximum batch')
        self.journal.save_image('maximum-batch.png', maximum.capture.png)
        self.tap(maximum, (1114, 589), 'Open batch confirmation')
        confirm = self.wait('confirm', predicate=lambda screen: screen.quantity == count)
        self.state['pending_action'] = {'kind': 'start', 'slots': empty[:count], 'count': count, 'run_dir': str(self.run_dir)}
        self.save()
        self.important('craft_start_requested', f'Starting {count} Quick Craft(s) using {unit * count} keystones and {maximum.screen.credits:,} credits.', status='attempted', count=count)
        self.tap(confirm, confirm.screen.target, 'Confirm verified Quick Craft batch')
        result = self.wait('list', timeout=60, predicate=lambda screen: all(slot.kind == 'running' for slot in screen.slots if slot.number in empty[:count]))
        self.journal.save_image('started-crafts.png', result.capture.png)
        self.important('crafts_started', f'Verified {count} new craft timer(s); collection visits are scheduled.', status='confirmed', count=count)
        self.state['pending_action'] = None
        self.observe_slots(result)
        return 'success'

    def run(self):
        try:
            self.journal.record('started', task='crafting')
            with InstanceLock(self.config):
                self.state = read_state(self.config)
                self.device.connect()
                self.device.verify_package()
                if self.device.display_size() != (1280, 720):
                    self.fail('Crafting requires the 1280×720 display profile')
                frame = self.navigate('home', 'list', (660, 658))
                frame = self.settled_list()
                self.reconcile(frame)
                self.state['disabled_reason'] = None
                self.observe_slots(frame)
                self.phase('Checking finished crafts and vacant slots')
                frame = self.collect(frame)
                result = self.fill(frame)
                if result != 'disabled':
                    self.home()
                self.journal.record('finished', status=result, actions=self.actions, frame='home.png')
                return RunResult(result, self.run_dir, self.clock() - self.started, self.actions)
        except BaseException as exc:
            self.journal.record('finished', status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                                reason=str(exc), actions=self.actions, frame=self.last_frame)
            raise
        finally:
            self.journal.close()


def run_crafting(config, device, vision, **kwargs):
    return CraftingRunner(config, device, vision, **kwargs).run()
