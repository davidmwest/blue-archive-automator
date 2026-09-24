"""Survey all schools, choose one verified lesson, and reconcile its ticket receipt."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import re
import time
from uuid import uuid4

from .actions import record_action
from .lesson_planner import LessonLocation, LessonRoom, choose_lesson
from .lesson_vision import LessonScreen, LessonVision
from .locking import InstanceLock
from .runtime import Capture, Journal, RunResult, TaskError, HOME_STABLE_SECONDS

LOGGER = logging.getLogger(__name__)
LESSONS_TIMEOUT = 1800
MAX_SCHOOLS = 50
MAX_INPUTS = 2500


def identity(name: str) -> str:
    """Whitespace and punctuation are not reliable separators in small UI text."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def counter_visible(screen: LessonScreen) -> bool:
    return type(screen.tickets) is int


@dataclass(frozen=True)
class LessonFrame:
    capture: Capture
    screen: LessonScreen


class LessonsRunner:
    def __init__(self, config, device, vision, *, lesson_vision=None,
                 monotonic=time.monotonic, sleep=time.sleep):
        self.config, self.device, self.startup = config, device, vision
        self.vision = lesson_vision or LessonVision(vision)
        self.clock, self.sleep = monotonic, sleep
        self.started = monotonic()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        self.run_dir = config.run_dir / f'lessons-{stamp}-{uuid4().hex[:8]}'
        self.journal = Journal(self.run_dir, monotonic, self.started)
        self.actions = self.confirmed = 0
        self.last_frame = None
        self.survey_number = 0
        self.current_school = None
        self.order = []
        self.expected_tickets = None
        self.initial_tickets = None
        self.completed_rooms = set()
        self.celebrations = 0

    def fail(self, message):
        raise TaskError(message, self.run_dir)

    def check_budget(self):
        if self.clock() - self.started >= LESSONS_TIMEOUT:
            self.fail('Lessons exceeded its thirty-minute limit; remaining tickets were left alone')
        if self.actions >= MAX_INPUTS:
            self.fail('Lessons reached its bounded input limit')

    def capture(self):
        self.check_budget()
        started = self.clock()
        png = self.device.screenshot()
        foreground = self.device.foreground_package()
        if foreground != self.config.package:
            self.fail('Blue Archive left the foreground during Lessons; no further input was sent')
        capture = Capture(png, started, foreground)
        self.last_frame = self.journal.screenshot(png)
        screen = self.vision.analyze(png)
        self.check_budget()
        return LessonFrame(capture, screen)

    def phase(self, detail):
        LOGGER.info('lessons: %s', detail)
        self.journal.record('state', state='lessons', detail=detail, frame=self.last_frame)

    def tap(self, frame, target, detail):
        self.check_budget()
        if (not isinstance(target, tuple) or len(target) != 2
                or any(type(n) is not int for n in target)
                or not 0 <= target[0] < 1280 or not 0 <= target[1] < 720):
            self.fail('Lessons has no valid recognized input target')
        if not frame.capture.is_fresh(self.clock()):
            self.fail('Lessons frame expired before input; no tap was sent')
        if self.device.foreground_package() != self.config.package:
            self.fail('Blue Archive left the foreground before Lesson input; no tap was sent')
        self.journal.record('intent', operation='tap', task='lessons', detail=detail,
                            target=list(target), frame=self.last_frame)
        sent = self.device.tap(*target, deadline=frame.capture.deadline, monotonic=self.clock)
        self.journal.record('outcome', operation='tap', result='ok' if sent else 'skipped_stale')
        if not sent:
            self.fail('Lessons frame expired during device preflight; no tap was sent')
        self.actions += 1
        self.sleep(.35)

    def wait(self, kinds, predicate=None, timeout=30):
        if isinstance(kinds, str):
            kinds = {kinds}
        end = self.clock() + timeout
        last = None
        while self.clock() < end:
            last = self.capture()
            if (last.screen.kind in kinds and (predicate is None or predicate(last.screen))
                    and last.capture.is_fresh(self.clock())):
                return last
            self.sleep(.5)
        self.fail(f'Lessons expected {", ".join(sorted(kinds))}, but found '
                  f'{getattr(last.screen, "kind", "unknown") if last else "no screen"}; review the screenshot trace')

    def tickets(self, frame, *, expected=None):
        value = frame.screen.tickets
        if type(value) is not int or not 0 <= value <= 99:
            self.fail('Lesson ticket count could not be read unambiguously')
        if expected is not None and value != expected:
            self.fail(f'Lesson tickets changed unexpectedly: expected {expected}, observed {value}')
        return value

    def overview(self):
        for _ in range(5):
            frame = self.capture()
            kind = frame.screen.kind
            if kind == 'overview':
                return frame if counter_visible(frame.screen) else self.wait('overview', counter_visible)
            if kind == 'rooms':
                self.tap(frame, (1138, 100), 'Close the recognized room grid')
                self.wait('map')
            elif kind == 'map':
                self.tap(frame, (54, 37), 'Return to Lesson location selection')
                return self.wait('overview', counter_visible)
            elif kind == 'confirm':
                self.tap(frame, (965, 114), 'Close the unstarted lesson preview')
                self.wait('rooms')
            else:
                observation = self.startup.analyze(frame.capture.png)
                if observation.state != 'home':
                    self.fail('Lessons requires a recognized Lesson screen or unobstructed home; run restart first')
                self.tap(frame, (210, 660), 'Open Lessons from verified home')
                return self.wait('overview', counter_visible)
        self.fail('Could not reach the Lesson overview within its navigation limit')

    def map_location(self, frame):
        s = frame.screen
        if (s.kind != 'map' or not s.location_name or type(s.location_rank) is not int
                or not 1 <= s.location_rank <= 12 or type(s.location_capped) is not bool):
            self.fail('School name or rank could not be verified')
        if not s.location_capped and (type(s.location_xp) is not int
                or type(s.location_xp_to_next) is not int or s.location_xp_to_next <= 0
                or not 0 <= s.location_xp < s.location_xp_to_next):
            self.fail(f'School XP could not be verified in {s.location_name}')
        return LessonLocation(identity(s.location_name), s.location_name, s.location_rank,
                              s.location_xp, s.location_xp_to_next, s.location_capped)

    def next_school(self, frame, *, right=True, expected=None):
        previous = identity(frame.screen.location_name or '')
        self.tap(frame, (1237, 362) if right else (40, 362), 'Inspect the next school' if right else 'Inspect the previous school')
        after = self.wait('map', lambda s: identity(s.location_name or '') != previous)
        if expected is not None and identity(after.screen.location_name or '') != expected:
            self.fail('School navigation changed order unexpectedly; no lesson was started')
        self.current_school = identity(after.screen.location_name)
        return after

    def room_grid(self, frame):
        self.tap(frame, (1156, 663), 'Open the verified school room list')
        # The opening modal slides a few pixels after its title is readable.
        # Fixed portrait crops are valid only after the panel has settled.
        self.sleep(1)
        result = self.wait('rooms', lambda s: bool(s.room_cards) and counter_visible(s))
        for _ in range(3):
            self.sleep(.7)
            following = self.wait('rooms', lambda s: bool(s.room_cards) and counter_visible(s))
            if following.screen.room_cards == result.screen.room_cards:
                result = following
                break
            result = following
        else:
            self.fail('Room observations did not stabilize; no ticket was spent')
        self.tickets(result, expected=self.expected_tickets)
        if result.screen.inspection_complete is not True:
            self.fail('The room grid could not be fully inspected; no ticket was spent')
        return result

    def survey(self):
        """Cycle every school and reconcile the observed ranks to the game total."""
        self.survey_number += 1
        overview = self.overview()
        self.tickets(overview, expected=self.expected_tickets)
        total = overview.screen.total_rank
        if type(total) is not int or total < 1:
            self.fail('Total area rank could not be read; a complete school survey cannot be verified')
        rows = [r for r in overview.screen.location_rows if r.unlocked and r.rank is not None]
        if not rows:
            self.fail('No unlocked school could be identified on the Lesson overview')
        self.tap(overview, rows[0].target, 'Enter a recognized unlocked school')
        frame = self.wait('map')
        locations, rooms, order = [], [], []
        allowed = {identity(name) for name in self.config.lessons_locations}
        first = identity(frame.screen.location_name or '')
        for _ in range(MAX_SCHOOLS):
            location = self.map_location(frame)
            self.tickets(frame, expected=self.expected_tickets)
            if location.id in order:
                if location.id != first:
                    self.fail('School traversal repeated before returning to its starting school')
                break
            locations.append(location)
            order.append(location.id)
            self.current_school = location.id
            self.phase(f'Surveying {location.name}: rank {location.rank}, XP {location.xp}/{location.xp_to_next}')
            if not allowed or location.id in allowed:
                grid = self.room_grid(frame)
                for card in grid.screen.room_cards:
                    room = LessonRoom(location.id, str(card.index), card.name, card.students,
                                      card.available, card.inspection_complete)
                    rooms.append(room)
                self.journal.save_image(f'survey-{self.survey_number:02d}/{location.id}.png', grid.capture.png)
                self.tap(grid, (1138, 100), 'Close the inspected room list')
                frame = self.wait('map', lambda s: identity(s.location_name or '') == location.id)
            frame = self.next_school(frame)
        else:
            self.fail('School traversal exceeded its limit without a complete cycle')
        if sum(location.rank for location in locations) != total:
            self.fail('Observed school ranks do not add up to Total Area Rank; survey is incomplete')
        if missing := allowed - set(order):
            self.fail('Configured schools were not found: ' + ', '.join(sorted(missing)))
        self.order = order
        snapshot = {'survey': self.survey_number, 'tickets': self.expected_tickets,
                    'total_rank': total, 'locations': [asdict(x) for x in locations],
                    'rooms': [asdict(x) for x in rooms]}
        (self.run_dir / f'survey-{self.survey_number:02d}.json').write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
        self.journal.record('lesson_survey', schools=len(locations), rooms=len(rooms),
                            total_rank=total, tickets=self.expected_tickets)
        self.phase(f'Compared {len(rooms)} rooms across {len(locations)} schools; choosing one ticket')
        return locations, rooms

    def navigate(self, location_id):
        frame = self.wait('map')
        current = identity(frame.screen.location_name or '')
        if current not in self.order or location_id not in self.order:
            self.fail('Selected school is absent from the verified navigation order')
        distance = (self.order.index(location_id) - self.order.index(current)) % len(self.order)
        right = distance <= len(self.order) // 2
        steps = distance if right else len(self.order) - distance
        index = self.order.index(current)
        for _ in range(steps):
            index = (index + (1 if right else -1)) % len(self.order)
            frame = self.next_school(frame, right=right, expected=self.order[index])
        return frame

    def dismiss_celebration(self, frame):
        self.celebrations += 1
        if self.celebrations > 4:
            self.fail('Lesson celebrations exceeded their limit; review the receipt')
        if frame.screen.dismiss_target is None:
            self.fail('Rank-up has no recognized dismissal control')
        kind = 'relationship' if frame.screen.kind == 'relationship_rank_up' else 'area'
        evidence = f'lesson-{self.confirmed + 1:02d}-{kind}-{self.celebrations}.png'
        self.journal.save_image(evidence, frame.capture.png)
        record_action(self.config, f'lesson_{kind}_rank_up',
                      f'Observed a {kind} rank-up screen during Lessons.', task='lessons',
                      evidence=str(self.run_dir / evidence))
        self.tap(frame, frame.screen.dismiss_target, f'Dismiss the verified {kind} rank-up')
        self.sleep(1.5)

    def await_result(self, kinds, *, predicate=None, timeout=60):
        """Handle recognized celebrations around the report without replaying Start."""
        kinds = {kinds} if isinstance(kinds, str) else set(kinds)
        end = self.clock() + timeout
        while self.clock() < end:
            frame = self.wait(kinds | {'relationship_rank_up', 'area_rank_up'},
                              predicate=lambda s: s.kind not in kinds or predicate is None or predicate(s),
                              timeout=end - self.clock())
            if frame.screen.kind in kinds:
                return frame
            self.dismiss_celebration(frame)
        self.fail('Lesson result did not arrive after its celebration')

    def await_receipt(self):
        return self.await_result('receipt')

    def execute(self, decision):
        """No replay after Start: require receipt AND observed ticket decrement."""
        location, room = decision.location, decision.room
        if (location.id, room.id) in self.completed_rooms:
            self.fail('Planner selected an already completed room; refusing a duplicate lesson')
        frame = self.navigate(location.id)
        observed = self.map_location(frame)
        if observed != location:
            self.fail('Selected school progress changed since the survey; no ticket was spent')
        grid = self.room_grid(frame)
        candidates = [c for c in grid.screen.room_cards if str(c.index) == room.id]
        if len(candidates) != 1:
            self.fail('Selected room could not be found again')
        card = candidates[0]
        if (not card.available or not card.inspection_complete or card.students != room.students
                or identity(card.name) != identity(room.name)):
            self.fail('Selected room changed since the survey; no ticket was spent')
        self.tap(grid, card.target, f'Preview {location.name} / {room.name}')
        self.sleep(1)
        preview = self.wait('confirm')
        s = preview.screen
        before = self.tickets(preview, expected=self.expected_tickets)
        if (identity(s.room_name or '') != identity(room.name) or before <= 0
                or s.tickets_after != before - 1 or s.start_target is None
                or s.inspection_complete is not True):
            self.fail('Lesson preview did not confirm the chosen room and a single-ticket cost')
        # Student slot IDs differ between grid and preview; compare their observed values.
        profile = lambda students: tuple((student.owned, student.bond) for student in students)
        if profile(s.students) != profile(room.students):
            self.fail('Lesson preview students differ from the surveyed room')
        self.journal.save_image(f'lesson-{self.confirmed + 1:02d}-before.png', preview.capture.png)
        self.phase(decision.reason)
        record_action(self.config, 'lesson_start_attempted', decision.reason, task='lessons',
                      location=location.name, room=room.name, strategy=self.config.lessons_strategy,
                      tickets_before=before, owned_students=decision.owned_count, student_count=decision.student_count)
        self.celebrations = 0
        self.tap(preview, s.start_target, f'Spend one ticket on {location.name} / {room.name}')
        receipt = self.await_receipt()
        if identity(receipt.screen.room_name or '') != identity(room.name):
            self.fail('Lesson receipt names an unexpected room; review the spent ticket')
        self.journal.save_image(f'lesson-{self.confirmed + 1:02d}-receipt.png', receipt.capture.png)
        target = receipt.screen.dismiss_target
        if target is None:
            self.fail('Lesson receipt has no recognized dismissal control; the ticket may already be spent')
        self.tap(receipt, target, 'Dismiss the verified lesson receipt')
        expected_school = lambda s: identity(s.location_name or '') == location.id
        after = self.await_result({'rooms', 'map'},
                                  predicate=lambda s: counter_visible(s) and (s.kind == 'rooms' or expected_school(s)))
        after_count = self.tickets(after, expected=before - 1)
        self.journal.save_image(f'lesson-{self.confirmed + 1:02d}-after.png', after.capture.png)
        if after.screen.kind == 'rooms':
            cards = [c for c in after.screen.room_cards if str(c.index) == room.id]
            if len(cards) != 1 or cards[0].available is not False:
                self.fail('Ticket decreased but completed room could not be verified; review the receipt')
            self.tap(after, (1138, 100), 'Close the post-lesson room list')
            # The grid's close-button ripple briefly covers the school header.
            # Wait for its identity to settle before reconciling rank and XP.
            after = self.await_result('map', predicate=expected_school)
        new_location = self.map_location(after)
        if new_location.id != location.id:
            self.fail('Lesson result returned to an unexpected school')
        # Rank/XP are observed rather than predicted: events and rank-ups may alter them.
        self.expected_tickets = after_count
        self.completed_rooms.add((location.id, room.id))
        self.confirmed += 1
        record_action(self.config, 'lesson_completed',
                      f'{location.name} / {room.name}: lesson receipt verified for {decision.owned_count} owned students '
                      f'({decision.student_count} total); tickets {before} → {after_count}.', task='lessons',
                      location=location.name, room=room.name, strategy=self.config.lessons_strategy,
                      owned_students=decision.owned_count, student_count=decision.student_count,
                      tickets_before=before, tickets_after=after_count, rank_before=location.rank,
                      rank_after=new_location.rank, xp_before=location.xp, xp_after=new_location.xp,
                      evidence=str(self.run_dir / f'lesson-{self.confirmed:02d}-receipt.png'))
        self.journal.record('lesson_confirmed', location=location.name, room=room.name,
                            tickets_before=before, tickets_after=after_count)

    def return_home(self):
        overview = self.overview()
        self.tap(overview, (1237, 24), 'Return home after Lessons')
        since, count = None, 0
        end = self.clock() + 45
        while self.clock() < end:
            frame = self.capture()
            observation = self.startup.analyze(frame.capture.png)
            if observation.state == 'home' and frame.capture.is_fresh(self.clock()):
                since = self.clock() if since is None else since
                count += 1
                if count >= self.config.home_confirmations and self.clock() - since >= HOME_STABLE_SECONDS:
                    self.journal.save_image('home.png', frame.capture.png)
                    return
            else:
                since, count = None, 0
            self.sleep(1)
        self.fail('Lessons finished but a clear home screen could not be verified')

    def run(self):
        try:
            self.journal.record('started', task='lessons', serial=self.config.serial,
                                strategy=self.config.lessons_strategy)
            with InstanceLock(self.config):
                self.device.connect()
                self.device.verify_package()
                if self.device.display_size() != (1280, 720):
                    self.fail('Lessons requires the fixed 1280×720 display')
                initial = self.overview()
                self.initial_tickets = self.expected_tickets = self.tickets(initial)
                limit = min(self.initial_tickets, self.config.lessons_max_tickets or self.initial_tickets)
                reason = 'No Lesson tickets available.' if not limit else 'Configured ticket limit reached.'
                for _ in range(limit):
                    locations, rooms = self.survey()
                    decision = choose_lesson(locations, rooms, strategy=self.config.lessons_strategy,
                                             allowed_location_ids=tuple(identity(n) for n in self.config.lessons_locations))
                    self.journal.record('lesson_decision', **asdict(decision))
                    if decision.status == 'blocked':
                        self.fail(decision.reason)
                    if decision.status == 'complete':
                        reason = decision.reason
                        break
                    self.execute(decision)
                    if self.expected_tickets == 0:
                        reason = 'All available Lesson tickets used.'
                        break
                self.return_home()
                detail = f'Lessons finished: {self.confirmed} verified lessons; {self.expected_tickets} tickets left. {reason}'
                record_action(self.config, 'lessons_completed', detail, task='lessons',
                              strategy=self.config.lessons_strategy, lessons=self.confirmed,
                              tickets_before=self.initial_tickets, tickets_after=self.expected_tickets)
                self.phase(detail)
                self.journal.record('finished', status='success', actions=self.actions,
                                    lessons=self.confirmed, tickets_remaining=self.expected_tickets, frame='home.png')
                return RunResult('success', self.run_dir, self.clock() - self.started, self.actions)
        except KeyboardInterrupt:
            self.journal.record('finished', status='stopped', actions=self.actions, frame=self.last_frame)
            raise
        except Exception as exc:
            self.journal.record('finished', status='failed', reason=str(exc), actions=self.actions, frame=self.last_frame)
            if isinstance(exc, TaskError):
                raise
            raise TaskError(f'Lessons stopped: {exc}', self.run_dir) from exc
        finally:
            self.journal.close()


def run_lessons(config, device, vision, **kwargs):
    return LessonsRunner(config, device, vision, **kwargs).run()
