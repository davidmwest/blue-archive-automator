from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ba_automator.adb import AdbDevice
from ba_automator.config import Config
from ba_automator.locking import InstanceLock
from ba_automator.restart import MAX_ACTIONS, TRACE_LIMIT, RestartError, run_restart


@dataclass(frozen=True)
class Observation:
    state: str
    detail: str = "test observation"
    target: tuple[int, int] | None = None
    text: tuple[str, ...] = ("private OCR text must not enter the journal",)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.interrupt = False

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if self.interrupt:
            raise KeyboardInterrupt
        self.now += seconds


class FakeDevice:
    def __init__(self, package="com.nexon.bluearchive"):
        self.package = package
        self.calls = []
        self.frame = 0
        self.size = (1280, 720)
        self.foreground = package
        self.capture_error = None

    def connect(self):
        self.calls.append("connect")

    def verify_package(self):
        self.calls.append("verify_package")

    def display_size(self):
        self.calls.append("display_size")
        return self.size

    def force_stop(self):
        self.calls.append("force_stop")

    def launch(self):
        self.calls.append("launch")

    def screenshot(self):
        self.calls.append("screenshot")
        if self.capture_error is not None:
            raise self.capture_error
        self.frame += 1
        # Vision owns real PNG validation. This runner consumes opaque captured bytes.
        return f"fake screenshot {self.frame}".encode()

    def tap(self, x, y, *, deadline=None, monotonic=None):
        if deadline is not None and monotonic() > deadline:
            return False
        self.calls.append(("tap", x, y))
        return True

    def foreground_package(self):
        self.calls.append("foreground_package")
        return self.foreground

    @property
    def taps(self):
        return [call for call in self.calls if isinstance(call, tuple)]


class FakeVision:
    def __init__(self, observations, clock, delay=0):
        self.observations = list(observations)
        self.index = 0
        self.clock = clock
        self.delay = delay

    def analyze(self, png):
        self.clock.now += self.delay
        result = self.observations[min(self.index, len(self.observations) - 1)]
        self.index += 1
        if isinstance(result, BaseException):
            raise result
        return result


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.clock = FakeClock()
        self.device = FakeDevice()

    def config(self, **overrides):
        values = {
            "serial": "127.0.0.1:5695", "package": self.device.package,
            "poll_interval": 1.0, "action_cooldown": 0.1,
            "startup_timeout": 30.0, "download_timeout": 60.0, "unknown_timeout": 5.0,
            "run_dir": self.root / "runs", "lock_dir": self.root / "locks",
        }
        values.update(overrides)
        return Config(**values)

    def run_task(self, observations, *, selected=None, delay=0):
        return run_restart(
            selected or self.config(), self.device, FakeVision(observations, self.clock, delay),
            monotonic=self.clock.monotonic, sleep=self.clock.sleep,
        )

    @staticmethod
    def journal(run_dir):
        return [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]

    def test_restarts_only_after_validation_and_records_action_intent_and_outcome(self):
        result = self.run_task([Observation("title", target=(640, 600)), Observation("home")])
        self.assertEqual(self.device.calls[:5],
                         ["connect", "verify_package", "display_size", "force_stop", "launch"])
        self.assertEqual(self.device.taps, [("tap", 640, 600)])
        self.assertEqual(result.status, "success")
        self.assertEqual(result.actions, 1)
        self.assertEqual((result.run_dir / "home.png").read_bytes(), b"fake screenshot 7")
        records = self.journal(result.run_dir)
        operations = [(record["event"], record["operation"]) for record in records if "operation" in record]
        self.assertEqual(operations, [("intent", "force_stop"), ("outcome", "force_stop"),
                                      ("intent", "launch"), ("outcome", "launch"),
                                      ("intent", "tap"), ("outcome", "tap")])
        self.assertNotIn("private OCR text", (result.run_dir / "events.jsonl").read_text())

    def test_home_debounce_waits_for_delayed_popup_then_starts_over(self):
        result = self.run_task([Observation("home")] * 3
                               + [Observation("popup", target=(1080, 85)), Observation("home")])
        self.assertEqual(self.device.taps, [("tap", 1080, 85)])
        self.assertEqual(result.duration, 9.0)
        self.assertEqual([item["state"] for item in self.journal(result.run_dir) if item["event"] == "state"],
                         ["home", "popup", "home"])

    def test_confirmation_count_and_minimum_stability_are_both_required(self):
        result = self.run_task([Observation("home")], selected=self.config(home_confirmations=8))
        self.assertEqual(result.duration, 7.0)
        self.assertEqual(len(self.device.taps), 0)

    def test_popup_evidence_preserves_before_after_outside_trace_ring(self):
        result = self.run_task([
            Observation("popup", "Close announcement", (1100, 50)),
            Observation("home"),
        ])
        records = [record for record in self.journal(result.run_dir)
                   if record["event"] == "popup_dismissal"]
        self.assertEqual(len(records), 2)
        before, after = records
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["result"], "pending")
        self.assertIsNone(before["after"])
        self.assertEqual(after["result"], "changed")
        self.assertEqual(after["after_state"], "home")
        self.assertEqual((result.run_dir / before["before"]).read_bytes(), b"fake screenshot 1")
        self.assertEqual((result.run_dir / after["after"]).read_bytes(), b"fake screenshot 2")

    def test_popup_evidence_reports_unchanged_after_failed_close(self):
        result = self.run_task([
            Observation("popup", "Close announcement", (1100, 50)),
            Observation("popup", "Close announcement", (1100, 50)),
            Observation("home"),
        ])
        records = [record for record in self.journal(result.run_dir)
                   if record["event"] == "popup_dismissal" and record["after"]]
        self.assertEqual([record["result"] for record in records], ["unchanged", "changed"])
        self.assertEqual(len({record["id"] for record in records}), 2)

    def test_explicit_download_approval_gets_extra_time_outside_startup_budget(self):
        result = self.run_task(
            [Observation("download_prompt", target=(780, 500))]
            + [Observation("downloading")] * 9 + [Observation("home")],
            selected=self.config(startup_timeout=8, download_timeout=15),
        )
        self.assertEqual(result.duration, 15.0)
        self.assertEqual(self.device.taps, [("tap", 780, 500)])

    def test_auto_download_disabled_does_not_tap_confirmation(self):
        with self.assertRaisesRegex(RestartError, "auto_download is disabled") as raised:
            self.run_task([Observation("download_prompt", target=(780, 500))],
                          selected=self.config(auto_download=False))
        self.assertEqual(self.device.taps, [])
        self.assertEqual(self.journal(raised.exception.run_dir)[-1]["status"], "failed")

    def test_download_deadline_cannot_be_extended_by_changing_status_or_prompt(self):
        with self.assertRaisesRegex(RestartError, "download exceeded"):
            self.run_task(
                [Observation("download_prompt", target=(780, 500)), Observation("downloading"),
                 Observation("loading"), Observation("download_prompt", detail="new download", target=(780, 500)),
                 Observation("downloading")], selected=self.config(download_timeout=6),
            )
        self.assertEqual(self.clock.now, 6)
        self.assertEqual(len(self.device.taps), 2)

    def test_loading_times_out_without_random_taps(self):
        with self.assertRaisesRegex(RestartError, "startup time limit"):
            self.run_task([Observation("loading")], selected=self.config(startup_timeout=7))
        self.assertEqual(self.clock.now, 7)
        self.assertEqual(self.device.taps, [])

    def test_unknown_screen_never_taps_and_leaves_diagnostics(self):
        with self.assertRaisesRegex(RestartError, "unknown-screen time limit") as raised:
            self.run_task([Observation("unknown", target=(400, 300))])
        records = self.journal(raised.exception.run_dir)
        self.assertEqual(self.device.taps, [])
        self.assertTrue((raised.exception.run_dir / records[-1]["frame"]).exists())
        self.assertEqual(records[-1]["status"], "failed")

    def test_blocked_screen_never_taps(self):
        with self.assertRaisesRegex(RestartError, "Account sign-in required"):
            self.run_task([Observation("blocked", "Account sign-in required", (400, 300))])
        self.assertEqual(self.device.taps, [])

    def test_external_credentials_or_store_blocks_any_screen_action(self):
        for state in ("home", "title", "popup", "download_prompt", "unknown"):
            with self.subTest(state=state):
                self.device.foreground = "com.android.vending"
                with self.assertRaisesRegex(RestartError, "foreground.*com.android.vending"):
                    self.run_task([Observation(state, target=(600, 500))])
        self.assertEqual(self.device.taps, [])

    def test_changing_detail_does_not_reset_repeated_tap_limit(self):
        observations = [Observation("popup", f"different OCR result {index}", (1100, 100)) for index in range(7)]
        with self.assertRaisesRegex(RestartError, "did not change after 5 attempts"):
            self.run_task(observations)
        self.assertEqual(len(self.device.taps), 5)

    def test_overall_tap_limit_applies_across_changing_states(self):
        observations = [Observation("title" if index % 2 else "popup", target=(600, 400))
                        for index in range(MAX_ACTIONS + 2)]
        with self.assertRaisesRegex(RestartError, "limit of 40 taps"):
            self.run_task(observations, selected=self.config(startup_timeout=100))
        self.assertEqual(len(self.device.taps), MAX_ACTIONS)

    def test_slow_analysis_forces_recapture_instead_of_stale_taps(self):
        with self.assertRaisesRegex(RestartError, "unknown-screen time limit"):
            self.run_task([Observation("title", target=(640, 600))], delay=6)
        self.assertGreaterEqual(self.device.frame, 2)
        self.assertEqual(self.device.taps, [])

    def test_delayed_transport_preflight_skips_stale_tap_and_uses_recaptured_target(self):
        transport = AdbDevice(self.config())
        self.device.tap = transport.tap
        preflight_calls = 0

        def preflight():
            nonlocal preflight_calls
            preflight_calls += 1
            if preflight_calls == 1:
                self.clock.now += 6.0

        def execute(arguments, **kwargs):
            self.device.calls.append(("tap", int(arguments[-2]), int(arguments[-1])))
            return b""

        with patch.object(transport, "_check_shared_server", side_effect=preflight), \
                patch.object(transport, "_execute", side_effect=execute):
            result = self.run_task([
                Observation("title", target=(640, 600)),
                Observation("title", target=(640, 610)),
                Observation("home"),
            ])
        self.assertEqual(result.status, "success")
        self.assertEqual(result.actions, 1)
        self.assertEqual(self.device.taps, [("tap", 640, 610)])
        outcomes = [record["result"] for record in self.journal(result.run_dir)
                    if record["event"] == "outcome" and record["operation"] == "tap"]
        self.assertEqual(outcomes, ["skipped_stale", "ok"])

    def test_screenshot_trace_is_bounded_during_long_download(self):
        result = self.run_task([Observation("downloading")] * (TRACE_LIMIT + 10) + [Observation("home")],
                               selected=self.config(download_timeout=100))
        self.assertEqual(len(list(result.run_dir.glob("trace-*.png"))), TRACE_LIMIT)
        self.assertEqual(len(list(result.run_dir.glob("*.png"))), TRACE_LIMIT + 1)

    def test_wrong_display_prevents_force_stop_or_launch(self):
        self.device.size = (1920, 1080)
        with self.assertRaisesRegex(RestartError, "1280×720"):
            self.run_task([Observation("home")])
        self.assertNotIn("force_stop", self.device.calls)
        self.assertNotIn("launch", self.device.calls)

    def test_capture_failure_is_controlled_and_releases_lock(self):
        selected = self.config()
        self.device.capture_error = RuntimeError("screenshot transport broke")
        with self.assertRaisesRegex(RestartError, "screenshot transport broke") as raised:
            self.run_task([Observation("home")], selected=selected)
        self.assertEqual(self.journal(raised.exception.run_dir)[-1]["status"], "failed")
        with InstanceLock(selected):
            pass

    def test_ctrl_c_records_interruption_and_releases_lock(self):
        selected = self.config()
        self.clock.interrupt = True
        with self.assertRaises(KeyboardInterrupt):
            self.run_task([Observation("loading")], selected=selected)
        run_dir = next(selected.run_dir.iterdir())
        self.assertEqual(self.journal(run_dir)[-1]["status"], "interrupted")
        with InstanceLock(selected):
            pass

    def test_invalid_target_is_controlled_without_tap(self):
        for target in (None, (-1, 300), (1280, 500), (False, 20), (12.5, 60)):
            with self.subTest(target=target), self.assertRaisesRegex(RestartError, "explicit tap target"):
                self.run_task([Observation("popup", target=target)])
        self.assertEqual(self.device.taps, [])


if __name__ == "__main__":
    unittest.main()
