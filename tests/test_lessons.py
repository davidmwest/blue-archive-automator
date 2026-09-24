"""Offline spending guards and multi-ticket orchestration for Lessons."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from ba_automator.config import Config
from ba_automator.lesson_planner import LessonLocation, LessonRoom, LessonStudent, choose_lesson
from ba_automator.lesson_vision import LessonScreen, LocationRow, RoomCard
from ba_automator.lessons import LessonFrame, LessonsRunner
from ba_automator.locking import InstanceLock
from ba_automator.runtime import Capture, TaskError
import ba_automator.lessons as lessons_module


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Device:
    def __init__(self, package):
        self.foreground = package
        self.taps = []
        self.connected = self.verified = False
        self.accept_input = True
        self.size = (1280, 720)

    def connect(self):
        self.connected = True

    def verify_package(self):
        self.verified = True

    def display_size(self):
        return self.size

    def screenshot(self):
        return b"offline synthetic screenshot"

    def foreground_package(self):
        return self.foreground

    def tap(self, x, y, *, deadline, monotonic):
        if not self.accept_input or monotonic() > deadline:
            return False
        self.taps.append((x, y))
        return True


@pytest.fixture
def harness(tmp_path, monkeypatch):
    config = Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                    run_dir=tmp_path / "runs", lock_dir=tmp_path / "locks", state_dir=tmp_path / "state")
    clock = Clock()
    device = Device(config.package)
    screens = [LessonScreen("unknown")]
    records = []
    runners = []

    def analyze(png):
        return screens.pop(0) if len(screens) > 1 else screens[0]

    vision = SimpleNamespace(analyze=analyze)
    startup = SimpleNamespace(analyze=lambda png: SimpleNamespace(state="unknown"))
    monkeypatch.setattr(lessons_module, "record_action",
                        lambda config, event, detail, **kwargs: records.append({"event": event, "detail": detail, **kwargs}))

    def make(cls=LessonsRunner, **overrides):
        runner = cls(replace(config, **overrides), device, startup, lesson_vision=vision,
                     monotonic=clock.monotonic, sleep=clock.sleep)
        runners.append(runner)
        return runner

    def frame(screen):
        return LessonFrame(Capture(device.screenshot(), clock.now, device.foreground), screen)

    yield SimpleNamespace(config=config, clock=clock, device=device, vision=vision, startup=startup,
                          make=make, frame=frame, screens=screens, records=records)
    for runner in runners:
        runner.journal.close()


def map_screen(name="A", *, rank=3, xp=100, tickets=2):
    return LessonScreen("map", tickets=tickets, location_name=name, location_rank=rank,
                        location_xp=xp, location_xp_to_next=450, location_capped=False)


def journal(runner):
    return [json.loads(line) for line in (runner.run_dir / "events.jsonl").read_text().splitlines()]


def test_foreground_loss_blocks_capture_before_recognition_or_input(harness):
    runner = harness.make()
    harness.device.foreground = "com.android.settings"
    with pytest.raises(TaskError, match="foreground"):
        runner.capture()
    assert harness.device.taps == []


def test_tap_refuses_expired_frame(harness):
    runner = harness.make()
    frame = harness.frame(map_screen())
    harness.clock.sleep(5.01)
    with pytest.raises(TaskError, match="expired before input"):
        runner.tap(frame, (10, 20), "test")
    assert harness.device.taps == []


def test_foreground_change_after_recognition_is_rechecked_before_tap(harness):
    runner = harness.make()
    frame = harness.frame(map_screen())
    harness.device.foreground = "com.android.settings"
    with pytest.raises(TaskError, match="foreground before Lesson input"):
        runner.tap(frame, (10, 20), "test")
    assert not harness.device.taps


def test_device_deadline_rejects_frame_that_expires_during_preflight(harness):
    runner = harness.make()
    harness.device.accept_input = False
    with pytest.raises(TaskError, match="device preflight"):
        runner.tap(harness.frame(map_screen()), (10, 20), "test")
    assert harness.device.taps == []
    assert runner.actions == 0
    assert journal(runner)[-1]["result"] == "skipped_stale"


@pytest.mark.parametrize("target", [None, (True, 0), (-1, 0), (1280, 0), (0, 720), [10, 20]])
def test_invalid_input_targets_do_not_reach_device(harness, target):
    runner = harness.make()
    with pytest.raises(TaskError, match="input target"):
        runner.tap(harness.frame(map_screen()), target, "test")
    assert not harness.device.taps


def test_unrecognized_screens_timeout_without_guessing_a_button(harness):
    runner = harness.make()
    with pytest.raises(TaskError, match="expected overview"):
        runner.wait("overview", timeout=2)
    assert harness.clock.now == 2
    assert not harness.device.taps


def test_ocr_that_makes_every_frame_stale_never_authorizes_input(harness):
    runner = harness.make()

    def slow_recognition(png):
        harness.clock.sleep(6)
        return LessonScreen("overview", tickets=2)

    harness.vision.analyze = slow_recognition
    with pytest.raises(TaskError, match="expected overview"):
        runner.wait("overview", timeout=7)
    assert not harness.device.taps


@pytest.mark.parametrize("count", [None, -1, True, 100, "2"])
def test_ambiguous_ticket_count_is_never_assumed_zero(harness, count):
    runner = harness.make()
    with pytest.raises(TaskError, match="ticket count"):
        runner.tickets(harness.frame(LessonScreen("overview", tickets=count)))


def test_unexpected_ticket_change_blocks(harness):
    runner = harness.make()
    with pytest.raises(TaskError, match="expected 2, observed 1"):
        runner.tickets(harness.frame(LessonScreen("overview", tickets=1)), expected=2)


@pytest.mark.parametrize("count", [0, 4])
def test_overview_recaptures_a_missing_counter_without_assuming_zero(harness, count):
    runner = harness.make()
    harness.screens[:] = [LessonScreen("overview"), LessonScreen("overview", tickets=count)]
    assert runner.overview().screen.tickets == count
    assert not harness.device.taps


def test_permanently_missing_overview_counter_times_out_without_input(harness):
    runner = harness.make()
    harness.screens[:] = [LessonScreen("overview")]
    with pytest.raises(TaskError, match="expected overview"):
        runner.overview()
    assert harness.clock.now >= 30
    assert not harness.device.taps


class OrchestrationRunner(LessonsRunner):
    """Simulate observed game updates while using the real run loop and planner."""

    starting_tickets = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.survey_calls = 0
        self.selected = []
        self.home_calls = 0
        self.locations = [LessonLocation("a", "A", 3, 0, 450),
                          LessonLocation("b", "B", 3, 50, 450)]
        self.rooms = [LessonRoom("a", "0", "Library", (LessonStudent("0", True, 10),)),
                      LessonRoom("a", "1", "Office", (LessonStudent("0", True, 20),)),
                      LessonRoom("b", "0", "Library", (LessonStudent("0", True, 40),))]

    def overview(self):
        return LessonFrame(Capture(b"overview", self.clock(), self.config.package),
                           LessonScreen("overview", tickets=self.starting_tickets))

    def survey(self):
        self.survey_calls += 1
        return tuple(self.locations), tuple(self.rooms)

    def execute(self, decision):
        # If run() batches choices from the first survey, this assertion fails
        # before the second ticket instead of accepting a stale queue of choices.
        assert self.survey_calls == len(self.selected) + 1
        self.selected.append((decision.location.id, decision.room.id))
        self.rooms = [replace(room, available=False) if room == decision.room else room for room in self.rooms]
        self.locations = [replace(location, xp=location.xp + 100)
                          if location.id == decision.location.id else location for location in self.locations]
        self.expected_tickets -= 1
        self.confirmed += 1

    def return_home(self):
        self.home_calls += 1


def test_zero_tickets_returns_home_without_survey_or_spend(harness):
    runner = harness.make(OrchestrationRunner)
    runner.starting_tickets = 0
    assert runner.run().status == "success"
    assert runner.survey_calls == 0
    assert runner.selected == []
    assert runner.home_calls == 1
    assert runner.expected_tickets == 0
    assert harness.records[-1]["lessons"] == 0


def test_configured_budget_limits_spend_and_preserves_unused_tickets(harness):
    runner = harness.make(OrchestrationRunner, lessons_max_tickets=2)
    assert runner.run().status == "success"
    assert len(runner.selected) == runner.survey_calls == 2
    assert runner.expected_tickets == 1
    assert harness.records[-1]["tickets_after"] == 1


def test_budget_larger_than_available_never_invents_or_buys_tickets(harness):
    runner = harness.make(OrchestrationRunner, lessons_max_tickets=99)
    assert runner.run().status == "success"
    assert runner.confirmed == 3
    assert runner.expected_tickets == 0


def test_each_rank_ticket_rechecks_xp_and_changes_school_when_appropriate(harness):
    runner = harness.make(OrchestrationRunner, lessons_strategy="school_rank")
    assert runner.run().status == "success"
    # A starts behind B, then its one confirmed lesson puts it ahead of B.
    # Highest bond breaks the two-room tie at A without overriding school XP.
    assert runner.selected == [("a", "1"), ("b", "0"), ("a", "0")]
    assert runner.survey_calls == 3
    assert runner.home_calls == 1


def test_relationship_strategy_is_actually_passed_to_planner(harness):
    runner = harness.make(OrchestrationRunner)
    runner.run()
    assert runner.selected == [("b", "0"), ("a", "1"), ("a", "0")]


def test_incomplete_ownership_stops_entire_run_without_spending(harness):
    runner = harness.make(OrchestrationRunner)
    runner.rooms[0] = replace(runner.rooms[0], students=(LessonStudent("0", None),))
    with pytest.raises(TaskError, match="ownership"):
        runner.run()
    assert runner.selected == []
    assert runner.home_calls == 0
    assert journal(runner)[-1]["status"] == "failed"


def test_no_owned_students_finishes_with_tickets_left(harness):
    runner = harness.make(OrchestrationRunner)
    runner.rooms = [replace(room, students=(LessonStudent("0", False),)) for room in runner.rooms]
    runner.run()
    assert runner.selected == []
    assert runner.expected_tickets == 3
    assert "leaving tickets unused" in harness.records[-1]["detail"]


def test_cancellation_records_stopped_and_releases_instance_lock(harness, monkeypatch):
    runner = harness.make(OrchestrationRunner)

    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "survey", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    assert runner.selected == []
    assert journal(runner)[-1]["status"] == "stopped"
    assert runner.journal.stream.closed
    with InstanceLock(runner.config):
        pass


def test_wrong_display_fails_before_navigation(harness):
    runner = harness.make(OrchestrationRunner)
    harness.device.size = (1920, 1080)
    with pytest.raises(TaskError, match="1280"):
        runner.run()
    assert runner.survey_calls == 0
    assert not runner.selected


def prepare_execution(harness, monkeypatch, *, receipt=True, after_tickets=1,
                      completed=True, preview_cost=1, preview_students=None, return_kind="rooms"):
    runner = harness.make()
    runner.expected_tickets = runner.initial_tickets = 2
    students = (LessonStudent("0", True, 10),)
    location = LessonLocation("a", "A", 3, 100, 450)
    room = LessonRoom("a", "0", "Library", students)
    decision = choose_lesson([location], [room])
    card = RoomCard(0, "Library", 1, True, (250, 250), students)
    monkeypatch.setattr(runner, "navigate", lambda location_id: harness.frame(map_screen()))
    monkeypatch.setattr(runner, "room_grid", lambda frame: harness.frame(LessonScreen("rooms", tickets=2, room_cards=(card,))))
    preview = LessonScreen("confirm", tickets=2, tickets_after=2 - preview_cost,
                           room_name="Library", students=students if preview_students is None else preview_students,
                           start_target=(640, 550))
    screens = [preview]
    if receipt:
        screens.append(LessonScreen("receipt", room_name="Library", dismiss_target=(640, 650)))
        if return_kind == "rooms":
            screens.append(LessonScreen("rooms", tickets=after_tickets,
                                        room_cards=(replace(card, available=not completed),)))
        screens.append(map_screen(xp=200, tickets=after_tickets))
    else:
        screens.append(LessonScreen("unknown"))
    harness.screens[:] = screens
    return runner, decision


def test_verified_receipt_decrement_and_completion_record_one_lesson(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    runner.execute(decision)
    assert runner.confirmed == 1
    assert runner.expected_tickets == 1
    assert runner.completed_rooms == {("a", "0")}
    assert harness.device.taps.count((640, 550)) == 1
    receipt = [item for item in harness.records if item["event"] == "lesson_completed"]
    assert len(receipt) == 1
    assert receipt[0]["tickets_before"] == 2
    assert receipt[0]["tickets_after"] == 1
    assert receipt[0]["xp_before"] == 100
    assert receipt[0]["xp_after"] == 200
    assert (runner.run_dir / "lesson-01-receipt.png").exists()


@pytest.mark.parametrize("return_kind", ["rooms", "map"], ids=["after-grid-close", "directly-after-report"])
def test_transient_school_header_occlusion_settles_before_recording_completion(harness, monkeypatch, return_kind):
    runner, decision = prepare_execution(harness, monkeypatch, return_kind=return_kind)
    name, school_id = "Haruhabara Electric Town", "haruhabaraelectrictown"
    decision = replace(decision, location=replace(decision.location, id=school_id, name=name),
                       room=replace(decision.room, location_id=school_id))
    monkeypatch.setattr(runner, "navigate", lambda location_id: harness.frame(map_screen(name)))
    harness.screens[-1] = map_screen(name, xp=200, tickets=1)
    # The close-button ripple can cover a letter in an otherwise recognizable
    # map. Wait for the expected identity rather than failing a spent ticket or
    # accepting that distorted name as a newly discovered school.
    harness.screens.insert(len(harness.screens) - 1,
                           map_screen("Haruhaba.'a Electric Town", xp=150, tickets=1))

    runner.execute(decision)

    assert runner.confirmed == 1
    assert runner.expected_tickets == 1
    assert runner.completed_rooms == {(school_id, "0")}
    assert harness.device.taps.count((640, 550)) == 1
    completions = [record for record in harness.records if record["event"] == "lesson_completed"]
    assert len(completions) == 1
    assert completions[0]["location"] == name
    assert completions[0]["xp_after"] == 200


@pytest.mark.parametrize("return_kind", ["rooms", "map"], ids=["after-grid-close", "directly-after-report"])
def test_persistently_wrong_result_school_times_out_without_replaying_start(harness, monkeypatch, return_kind):
    runner, decision = prepare_execution(harness, monkeypatch, return_kind=return_kind)
    harness.screens[-1] = map_screen("Different school", xp=200, tickets=1)

    with pytest.raises(TaskError, match="expected .*map.*but found map"):
        runner.execute(decision)

    assert runner.confirmed == 0
    assert harness.device.taps.count((640, 550)) == 1
    assert not any(record["event"] == "lesson_completed" for record in harness.records)
    assert 60 <= harness.clock.now < 70
    assert (runner.run_dir / "lesson-01-receipt.png").exists()


def test_receipt_without_ticket_decrement_is_not_reported_as_success(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch, after_tickets=2)
    with pytest.raises(TaskError, match="tickets changed unexpectedly"):
        runner.execute(decision)
    assert runner.confirmed == 0
    assert not any(item["event"] == "lesson_completed" for item in harness.records)
    assert harness.device.taps.count((640, 550)) == 1


def test_missing_receipt_never_retries_start(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch, receipt=False)
    with pytest.raises(TaskError, match="expected .*receipt.*but found unknown"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250), (640, 550)]
    assert runner.confirmed == 0
    assert [item["event"] for item in harness.records] == ["lesson_start_attempted"]


def test_receipt_without_dismiss_control_never_guesses_or_retries(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    harness.screens[1] = replace(harness.screens[1], dismiss_target=None)
    with pytest.raises(TaskError, match="no recognized dismissal control"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250), (640, 550)]
    assert runner.confirmed == 0


def test_relationship_rank_up_then_receipt_completes_without_replaying_start(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    harness.screens.insert(1, LessonScreen("relationship_rank_up", dismiss_target=(1170, 650)))
    runner.execute(decision)
    assert harness.device.taps.count((640, 550)) == 1
    assert harness.device.taps.count((1170, 650)) == 1
    assert runner.confirmed == 1
    assert runner.expected_tickets == 1
    assert [record["event"] for record in harness.records] == [
        "lesson_start_attempted", "lesson_relationship_rank_up", "lesson_completed"]
    assert (runner.run_dir / "lesson-01-relationship-1.png").exists()
    assert (runner.run_dir / "lesson-01-receipt.png").exists()


def test_endless_relationship_rank_ups_stop_after_four_dismissals(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch, receipt=False)
    harness.screens[1] = LessonScreen("relationship_rank_up", dismiss_target=(1170, 650))
    with pytest.raises(TaskError, match="celebrations exceeded their limit"):
        runner.execute(decision)
    assert harness.device.taps.count((1170, 650)) == 4
    assert harness.device.taps.count((640, 550)) == 1
    assert runner.confirmed == 0
    assert runner.expected_tickets == 2
    assert not any(record["event"] == "lesson_completed" for record in harness.records)
    assert harness.clock.now < 60


@pytest.mark.parametrize("kind", ["relationship_rank_up", "area_rank_up"])
def test_rank_up_without_dismissal_control_stops_without_guessing(harness, monkeypatch, kind):
    runner, decision = prepare_execution(harness, monkeypatch, receipt=False)
    harness.screens[1] = LessonScreen(kind)
    with pytest.raises(TaskError, match="[Rr]ank-up has no recognized dismissal control"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250), (640, 550)]
    assert runner.confirmed == 0
    assert runner.expected_tickets == 2
    assert [record["event"] for record in harness.records] == ["lesson_start_attempted"]


@pytest.mark.parametrize("kind", ["relationship_rank_up", "area_rank_up"])
def test_rank_up_followed_by_unknown_screen_times_out_without_replaying_start(harness, monkeypatch, kind):
    runner, decision = prepare_execution(harness, monkeypatch, receipt=False)
    harness.screens.insert(1, LessonScreen(kind, dismiss_target=(1170, 650)))
    with pytest.raises(TaskError, match="expected .*receipt.*but found unknown"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250), (640, 550), (1170, 650)]
    assert runner.confirmed == 0
    assert runner.expected_tickets == 2
    assert not any(record["event"] == "lesson_completed" for record in harness.records)


@pytest.mark.parametrize("popup_position", [1, 2, 3], ids=["before-report", "after-report", "after-grid-close"])
def test_area_rank_up_at_each_result_stage_is_logged_and_fresh_rank_reconciled(harness, monkeypatch, popup_position):
    runner, decision = prepare_execution(harness, monkeypatch)
    decision = replace(decision, location=replace(decision.location, xp=350))
    monkeypatch.setattr(runner, "navigate", lambda location_id: harness.frame(map_screen(xp=350)))
    harness.screens[-1] = replace(map_screen(rank=4, xp=0, tickets=1), location_xp_to_next=750)
    harness.screens.insert(popup_position, LessonScreen("area_rank_up", dismiss_target=(640, 500)))
    # A previous lesson's celebration count must not consume this ticket's cap.
    runner.celebrations = 4

    runner.execute(decision)

    assert harness.device.taps.count((640, 550)) == 1
    assert harness.device.taps.count((640, 500)) == 1
    assert runner.confirmed == 1
    assert runner.expected_tickets == 1
    assert [record["event"] for record in harness.records] == [
        "lesson_start_attempted", "lesson_area_rank_up", "lesson_completed"]
    completion = harness.records[-1]
    assert (completion["rank_before"], completion["rank_after"]) == (3, 4)
    assert (completion["xp_before"], completion["xp_after"]) == (350, 0)
    assert (runner.run_dir / "lesson-01-area-1.png").exists()


def test_four_mixed_celebrations_across_report_and_grid_are_allowed(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    preview, receipt, grid, after = harness.screens
    relationship = LessonScreen("relationship_rank_up", dismiss_target=(1170, 650))
    area = LessonScreen("area_rank_up", dismiss_target=(640, 500))
    harness.screens[:] = [preview, relationship, area, receipt, relationship, grid, area, after]

    runner.execute(decision)

    assert runner.confirmed == 1
    assert runner.celebrations == 4
    assert harness.device.taps.count((640, 550)) == 1
    assert harness.device.taps.count((1170, 650)) == 2
    assert harness.device.taps.count((640, 500)) == 2
    assert len([record for record in harness.records if record["event"] == "lesson_completed"]) == 1
    assert (runner.run_dir / "lesson-01-relationship-1.png").exists()
    assert (runner.run_dir / "lesson-01-area-2.png").exists()
    assert (runner.run_dir / "lesson-01-relationship-3.png").exists()
    assert (runner.run_dir / "lesson-01-area-4.png").exists()


def test_combined_celebration_limit_does_not_reset_between_report_and_grid(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    preview, receipt, grid, after = harness.screens
    relationship = LessonScreen("relationship_rank_up", dismiss_target=(1170, 650))
    area = LessonScreen("area_rank_up", dismiss_target=(640, 500))
    harness.screens[:] = [preview, relationship, area, receipt, relationship, area, grid, relationship, after]

    with pytest.raises(TaskError, match="celebrations exceeded their limit"):
        runner.execute(decision)

    assert harness.device.taps.count((640, 550)) == 1
    assert harness.device.taps.count((1170, 650)) == 2
    assert harness.device.taps.count((640, 500)) == 2
    assert runner.confirmed == 0
    assert not any(record["event"] == "lesson_completed" for record in harness.records)


@pytest.mark.parametrize("name", ["Office", None, ""])
def test_mismatched_or_unread_receipt_room_stops_before_dismissal_or_another_ticket(harness, monkeypatch, name):
    runner, decision = prepare_execution(harness, monkeypatch)
    harness.screens[1] = replace(harness.screens[1], room_name=name)
    with pytest.raises(TaskError, match="receipt names an unexpected room"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250), (640, 550)]
    assert runner.confirmed == 0
    assert runner.expected_tickets == 2
    assert not any(record["event"] == "lesson_completed" for record in harness.records)


def test_cancellation_after_start_never_replays_ticket(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    analyze = harness.vision.analyze

    def interrupt_at_receipt(png):
        if (640, 550) in harness.device.taps:
            raise KeyboardInterrupt
        return analyze(png)

    harness.vision.analyze = interrupt_at_receipt
    with pytest.raises(KeyboardInterrupt):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250), (640, 550)]
    assert runner.confirmed == 0


def test_decrement_with_available_room_is_ambiguous_and_stops(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch, completed=False)
    with pytest.raises(TaskError, match="completed room could not be verified"):
        runner.execute(decision)
    assert runner.confirmed == 0
    assert not any(item["event"] == "lesson_completed" for item in harness.records)


def test_repeated_room_is_blocked_before_any_navigation_or_input(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    runner.completed_rooms.add(("a", "0"))
    with pytest.raises(TaskError, match="duplicate lesson"):
        runner.execute(decision)
    assert harness.device.taps == []


@pytest.mark.parametrize("cost", [0, 2])
def test_preview_must_show_exactly_one_ticket_cost(harness, monkeypatch, cost):
    runner, decision = prepare_execution(harness, monkeypatch, preview_cost=cost)
    with pytest.raises(TaskError, match="single-ticket cost"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250)]
    assert not harness.records


def test_changed_preview_student_profile_blocks_start(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch,
                                         preview_students=(LessonStudent("different-slot", True, 30),))
    with pytest.raises(TaskError, match="students differ"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250)]


def test_incomplete_preview_blocks_start_even_when_known_portraits_match(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    harness.screens[0] = replace(harness.screens[0], inspection_complete=False)
    with pytest.raises(TaskError, match="preview did not confirm"):
        runner.execute(decision)
    assert harness.device.taps == [(250, 250)]
    assert not harness.records


def test_preview_slot_ids_are_not_mistaken_for_student_identity(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch,
                                         preview_students=(LessonStudent("different-slot", True, 10),))
    runner.execute(decision)
    assert runner.confirmed == 1


def test_changed_school_xp_since_survey_blocks_start(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    monkeypatch.setattr(runner, "navigate", lambda location_id: harness.frame(map_screen(xp=200)))
    with pytest.raises(TaskError, match="progress changed"):
        runner.execute(decision)
    assert not harness.device.taps


def test_changed_selected_room_blocks_preview_and_start(harness, monkeypatch):
    runner, decision = prepare_execution(harness, monkeypatch)
    changed = RoomCard(0, "Library", 1, True, (250, 250), (LessonStudent("0", True, 90),))
    monkeypatch.setattr(runner, "room_grid", lambda frame: harness.frame(
        LessonScreen("rooms", tickets=2, room_cards=(changed,))))
    with pytest.raises(TaskError, match="room changed"):
        runner.execute(decision)
    assert not harness.device.taps


def test_incomplete_room_grid_cannot_be_passed_to_planner(harness):
    runner = harness.make()
    runner.expected_tickets = 2
    card = RoomCard(0, "Library", 1, True, (250, 250), (LessonStudent("0", True, 10),))
    # Individual recognized cards look valid, but vision says another card was
    # unreadable. Ignoring the screen-level flag would falsely claim optimality.
    harness.screens[:] = [LessonScreen("rooms", tickets=2, room_cards=(card,), inspection_complete=False)]
    with pytest.raises(TaskError, match="fully inspected"):
        runner.room_grid(harness.frame(map_screen()))
    assert harness.device.taps == [(1156, 663)]


def prepare_survey(harness, monkeypatch, *, cycle=("A", "B", "A"), total=6):
    runner = harness.make()
    runner.expected_tickets = 2
    overview = LessonScreen("overview", tickets=2, total_rank=total,
                            location_rows=(LocationRow("A", 3, 100, 450, False, (900, 220)),))
    monkeypatch.setattr(runner, "overview", lambda: harness.frame(overview))
    # Survey's navigation is stubbed; its actual enumeration, duplicate detection,
    # rank checksum, snapshot persistence, and ticket reconciliation still run.
    monkeypatch.setattr(runner, "wait", lambda kinds, predicate=None, timeout=30: harness.frame(map_screen(cycle[0])))
    remaining = iter(cycle[1:])
    monkeypatch.setattr(runner, "next_school", lambda frame: harness.frame(map_screen(next(remaining))))
    monkeypatch.setattr(runner, "tap", lambda *args: None)

    def grid(frame):
        students = (LessonStudent("0", True, 10),)
        return harness.frame(LessonScreen("rooms", tickets=2,
                                         room_cards=(RoomCard(0, "Library", 1, True, (250, 250), students),)))

    monkeypatch.setattr(runner, "room_grid", grid)
    return runner


def test_complete_school_cycle_reconciles_rank_sum_and_persists_all_rooms(harness, monkeypatch):
    runner = prepare_survey(harness, monkeypatch)
    locations, rooms = runner.survey()
    assert [item.id for item in locations] == ["a", "b"]
    assert {item.location_id for item in rooms} == {"a", "b"}
    saved = json.loads((runner.run_dir / "survey-01.json").read_text())
    assert saved["total_rank"] == 6
    assert len(saved["rooms"]) == 2


def test_partial_school_cycle_cannot_masquerade_as_complete(harness, monkeypatch):
    runner = prepare_survey(harness, monkeypatch, total=9)
    with pytest.raises(TaskError, match="do not add up"):
        runner.survey()
    assert not (runner.run_dir / "survey-01.json").exists()


def test_cycle_repeating_nonstarting_school_blocks(harness, monkeypatch):
    runner = prepare_survey(harness, monkeypatch, cycle=("A", "B", "B"))
    with pytest.raises(TaskError, match="repeated before returning"):
        runner.survey()


def test_rank_parser_rejects_unknown_xp_before_planning(harness):
    runner = harness.make()
    with pytest.raises(TaskError, match="XP could not be verified"):
        runner.map_location(harness.frame(replace(map_screen(), location_xp=None)))


def test_navigation_order_change_blocks_before_room_selection(harness):
    runner = harness.make()
    harness.screens[:] = [map_screen("Unexpected school")]
    with pytest.raises(TaskError, match="navigation changed order"):
        runner.next_school(harness.frame(map_screen("A")), expected="b")
    assert harness.device.taps == [(1237, 362)]


def test_runtime_timeout_is_bounded(harness):
    runner = harness.make()
    harness.clock.sleep(lessons_module.LESSONS_TIMEOUT)
    with pytest.raises(TaskError, match="thirty-minute"):
        runner.capture()
    assert not harness.device.taps
