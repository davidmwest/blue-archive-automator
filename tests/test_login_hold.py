"""After the account signs in on another device, the game is left alone for two hours."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from ba_automator import login_hold
from ba_automator.config import Config
from ba_automator.restart import RestartError, run_restart
from ba_automator.vision import Observation, Word, classify

NOW = datetime(2026, 10, 14, 13, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path):
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive",
                  run_dir=tmp_path / "runs", lock_dir=tmp_path / "locks", state_dir=tmp_path / "state")


@pytest.mark.parametrize("text", [
    "You have been disconnected because this account logged in on another device.",
    "Logged in from another device. Returning to the title screen.",
    "Duplicate login detected.",
    "The account is in use on a different device",
])
def test_notice_wording_names_the_other_device(text):
    assert login_hold.signed_in_elsewhere(text)


@pytest.mark.parametrize("text", [
    "Connection lost. Retry?", "TOUCH TO START", "Daily login bonus", "Network error",
    "Reward acquired", "Another round?",
])
def test_ordinary_screens_are_not_a_takeover(text):
    assert not login_hold.signed_in_elsewhere(text)


def test_hold_lasts_two_hours_and_survives_reading(config):
    assert login_hold.held_until(config, NOW) is None
    until = login_hold.begin(config, detail="notice", now=NOW)
    assert until == NOW + timedelta(hours=2)
    assert login_hold.held_until(config, NOW + timedelta(minutes=119)) == until
    assert login_hold.held_until(config, NOW + timedelta(hours=2)) is None
    assert "before logging in again" in login_hold.message(until)
    actions = [json.loads(line) for line in
               (config.state_dir / "important-actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert actions[-1]["action"] == "login_hold_started"


def test_unreadable_hold_record_holds_nothing(config):
    path = login_hold.state_path(config)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert login_hold.held_until(config, NOW) is None


def test_the_live_global_notice_is_recognized_over_the_page_behind_it():
    # Captured 2026-10-14 over the Event Shop, with Notice and Confirm.
    words = [Word("Event Shop", .99, (104, 9, 255, 41)), Word("Notice", .99, (590, 150, 690, 185)),
             Word("Duplicate login detected.", .99, (492, 300, 785, 340)),
             Word("Returning to title.", .99, (535, 335, 742, 368)),
             Word("Confirm", .99, (580, 485, 700, 520))]
    observation = classify(words, {})
    assert (observation.state, observation.detector, observation.target) == ("blocked", login_hold.DETECTOR, None)


def test_startup_recognizes_the_notice_before_its_button():
    words = [Word("You have been logged out because this account", .99, (330, 300, 950, 330)),
             Word("signed in on another device.", .99, (440, 340, 840, 370)),
             Word("OK", .99, (610, 500, 670, 530))]
    observation = classify(words, {})
    assert (observation.state, observation.detector) == ("blocked", login_hold.DETECTOR)
    assert observation.target is None


class Device:
    def __init__(self, foreground="com.nexon.bluearchive"):
        self.foreground, self.calls = foreground, []

    def foreground_package(self):
        return self.foreground

    def screenshot(self):
        self.calls.append("screenshot")
        return b"png"


def notice():
    return Observation("blocked", "The account was signed in on another device", None, (),
                       detector=login_hold.DETECTOR)


def test_a_failed_task_starts_a_hold_when_the_notice_is_on_screen(config, tmp_path):
    vision = SimpleNamespace(analyze=lambda png: notice())
    until = login_hold.inspect_after_failure(config, Device(), vision, tmp_path / "run")
    assert until is not None and (tmp_path / "run" / "signed-in-elsewhere.png").read_bytes() == b"png"
    # A running hold is not restarted, and nothing is read for it.
    device = Device()
    assert login_hold.inspect_after_failure(config, device, vision) is None and device.calls == []


def test_other_failures_start_no_hold_and_never_raise(config):
    ordinary = SimpleNamespace(analyze=lambda png: Observation("unknown", "x"))
    assert login_hold.inspect_after_failure(config, Device(), ordinary) is None
    assert login_hold.inspect_after_failure(config, Device("com.android.launcher3"),
                                            SimpleNamespace(analyze=lambda png: notice())) is None

    class Broken(Device):
        def screenshot(self):
            raise OSError("adb went away")
    assert login_hold.inspect_after_failure(config, Broken(), ordinary) is None
    assert login_hold.held_until(config) is None


class StartupDevice(Device):
    def connect(self):
        self.calls.append("connect")

    def verify_package(self):
        pass

    def display_size(self):
        return (1280, 720)

    def force_stop(self):
        self.calls.append("force_stop")

    def launch(self):
        self.calls.append("launch")

    def tap(self, *args, **kwargs):
        self.calls.append("tap")
        return True


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def restart(config, device, vision):
    clock = Clock()
    return run_restart(config, device, vision, monotonic=clock.monotonic, sleep=clock.sleep)


def test_restart_does_not_touch_the_game_during_a_hold(config):
    login_hold.begin(config, detail="notice")
    device = StartupDevice()
    with pytest.raises(RestartError, match="before logging in again"):
        restart(config, device, SimpleNamespace(analyze=lambda png: Observation("home", "home")))
    assert device.calls == []


def test_restart_holds_instead_of_relaunching_over_a_notice(config, monkeypatch):
    # Relaunching would clear the notice and log in, ending the other session.
    monkeypatch.setattr("ba_automator.restart.read_game_words", lambda png, vision: [
        Word("Logged in from another device.", .99, (400, 300, 880, 330))])
    device = StartupDevice()
    vision = SimpleNamespace(read=lambda image: [], analyze=lambda png: Observation("home", "home"))
    with pytest.raises(RestartError, match="before logging in again"):
        restart(config, device, vision)
    assert "force_stop" not in device.calls and "tap" not in device.calls
    assert login_hold.held_until(config) is not None


def test_a_notice_during_startup_starts_a_hold_without_input(config):
    device = StartupDevice()
    with pytest.raises(RestartError, match="before logging in again"):
        restart(config, device, SimpleNamespace(analyze=lambda png: notice()))
    assert "tap" not in device.calls and login_hold.held_until(config) is not None


def test_manager_waits_out_a_hold_then_runs_an_interrupted_daily_again(tmp_path):
    from test_server import ProcessFactory, eventually
    from ba_automator import daily_schedule
    from ba_automator.club import game_day
    from ba_automator.server import DashboardController
    path = tmp_path / "config" / "local.toml"
    path.parent.mkdir()
    path.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                    'adb_path="../tools/adb"\n[daily]\nschedule_enabled=true\n[checkin]\nschedule_enabled=false\n'
                    '[storage]\nrun_dir="../data/runs"\nlock_dir="../data/locks"\n', encoding="utf-8")
    selected = Config.from_file(path)
    now = [NOW]
    daily_schedule.write_state(selected, {
        "version": 1, "game_day": game_day(NOW), "status": "failed", "run_id": "earlier",
        "started_at": (NOW - timedelta(minutes=30)).isoformat(),
        "completed_at": (NOW - timedelta(minutes=20)).isoformat()})
    login_hold.begin(selected, detail="notice", now=NOW)
    factory = ProcessFactory()
    controller = DashboardController(path, process_factory=factory, wall_clock=lambda: now[0])
    try:
        controller.enqueue("restart")
        eventually(lambda: "before logging in again" in controller.status()["phase"])
        assert factory.processes == []
        now[0] += timedelta(hours=2, minutes=1)
        controller.resume()
        eventually(lambda: len(factory.processes) == 1)
        assert factory.processes[0].arguments[-1] == "restart"
        factory.processes[0].finish()
        eventually(lambda: len(factory.processes) == 2)
        assert factory.processes[1].arguments[-1] == "daily"
        factory.processes[1].finish()
    finally:
        controller.close()


def test_ending_a_hold_early_keeps_its_record_and_a_login_clears_it(config):
    assert login_hold.end_early(config) is False
    login_hold.begin(config, detail="notice", now=NOW)
    assert login_hold.end_early(config, now=NOW + timedelta(minutes=30)) is True
    assert login_hold.held_until(config, NOW + timedelta(minutes=31)) is None
    assert login_hold.recorded(config)["ended_early"] is True
    login_hold.clear(config)
    assert login_hold.recorded(config) is None


def test_after_a_hold_ends_the_old_notice_is_relaunched_over(config, monkeypatch):
    # Live, the notice from 06:18 was still on screen when the hold was ended;
    # treating it as a new takeover started a second hold instead of the job.
    login_hold.begin(config, detail="notice")
    login_hold.end_early(config)
    monkeypatch.setattr("ba_automator.restart.read_game_words", lambda png, vision: [
        Word("Duplicate login detected.", .99, (492, 300, 785, 340))])
    device = StartupDevice()
    vision = SimpleNamespace(read=lambda image: [], analyze=lambda png: Observation("home", "home"))
    monkeypatch.setattr("ba_automator.restart.HOME_STABLE_SECONDS", 0)
    result = restart(config.__class__(**{**config.__dict__, "home_confirmations": 1}), device, vision)
    assert result.status == "success" and "force_stop" in device.calls
    assert login_hold.recorded(config) is None          # the session is ours again


def test_a_failed_task_does_not_start_a_second_hold_over_a_handled_notice(config):
    login_hold.begin(config, detail="notice")
    login_hold.end_early(config)
    vision = SimpleNamespace(analyze=lambda png: notice())
    assert login_hold.inspect_after_failure(config, Device(), vision) is None
    assert login_hold.held_until(config) is None
