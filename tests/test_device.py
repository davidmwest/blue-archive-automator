from __future__ import annotations

from dataclasses import replace
import errno
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ba_automator.adb import AdbDevice, DeviceError
from ba_automator.config import Config, ConfigError
from ba_automator.locking import InstanceLock, LockError


def config(**overrides) -> Config:
    return Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive", **overrides)


class ConfigTests(unittest.TestCase):
    def test_rejects_implicit_remote_or_malformed_targets(self):
        for serial in ("", "emulator-5554", "10.0.0.2:5555", "127.0.0.1", "127.0.0.1:0",
                       "127.0.0.1:65536", "::1:5555", "127.0.0.1:5555;id"):
            with self.subTest(serial=serial), self.assertRaises(ConfigError):
                replace(config(), serial=serial)

    def test_loopback_alias_has_same_lock_identity(self):
        canonical = config()
        alias = replace(canonical, serial="localhost:5695")
        self.assertEqual(alias.serial, canonical.serial)
        self.assertEqual(InstanceLock(alias).path, InstanceLock(canonical).path)

    def test_rejects_wrong_game_or_shell_content(self):
        for package in ("com.YoStarEN.AzurLane", "com.nexon.bluearchive;id", "bluearchive",
                        "com.nexon.bluearchive.debug$(id)"):
            with self.subTest(package=package), self.assertRaises(ConfigError):
                replace(config(), package=package)
        self.assertEqual(replace(config(), package="com.YostarJP.BlueArchive").package,
                         "com.YostarJP.BlueArchive")

    def test_timeouts_and_flags_are_bounded_and_typed(self):
        invalid = {"poll_interval": (0, True, float("nan"), float("inf")),
                   "startup_timeout": (0, 3601), "download_timeout": (-1, 14401),
                   "unknown_timeout": (0, 1801), "home_confirmations": (0, True, 1.5, 11),
                   "auto_download": (1, "true"), "action_cooldown": (0, 31),
                   "expected_width": (1920, 1280.0), "run_dir": ("", False)}
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                    replace(config(), **{key: value})

    def test_file_paths_are_relative_to_config_not_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "local.toml"
            path.write_text('[device]\nserial="127.0.0.1:5695"\n'
                            'package="com.nexon.bluearchive"\nadb_path="tools/adb"\n'
                            '[storage]\nrun_dir="private/runs"\n', encoding="utf-8")
            loaded = Config.from_file(path)
            self.assertEqual(loaded.adb_path, str(root / "tools/adb"))
            self.assertEqual(loaded.run_dir, root / "private/runs")
            self.assertEqual(loaded.lock_dir, root / "data/locks")
            self.assertEqual(loaded.state_dir, root / "data/state")

    def test_cafe_table_maps_to_flat_config_and_preserves_separate_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "profiles" / "local.toml"
            path.parent.mkdir()
            path.write_text('[device]\nserial="127.0.0.1:5695"\n'
                            'package="com.nexon.bluearchive"\n'
                            '[storage]\nrun_dir="../runs"\nstate_dir="../shared-state"\n'
                            '[cafe]\nschedule_enabled=true\ninvite_enabled=true\n'
                            'invite_student="  Hoshino (Swimsuit)  "\n', encoding="utf-8")
            loaded = Config.from_file(path)
            self.assertTrue(loaded.cafe_schedule_enabled)
            self.assertTrue(loaded.cafe_invite_enabled)
            self.assertEqual(loaded.cafe_invite_student, "Hoshino (Swimsuit)")
            self.assertEqual(loaded.state_dir, root / "shared-state")
            self.assertEqual(loaded.run_dir, root / "runs")
            self.assertNotEqual(loaded.state_dir, loaded.run_dir)

    def test_cafe_automation_and_invitations_default_to_disabled(self):
        selected = config()
        self.assertFalse(selected.cafe_schedule_enabled)
        self.assertFalse(selected.cafe_invite_enabled)
        self.assertEqual(selected.cafe_invite_student, "")

    def test_cafe_flags_require_booleans(self):
        for name in ("cafe_schedule_enabled", "cafe_invite_enabled"):
            for value in (1, 0, "true", None):
                with self.subTest(name=name, value=value), self.assertRaises(ConfigError):
                    replace(config(), **{name: value})

    def test_blank_invitation_name_selects_automatic_target_without_enabling_invites(self):
        for value in ("", "   "):
            with self.subTest(value=value):
                automatic = replace(config(), cafe_invite_enabled=True, cafe_invite_student=value)
                self.assertTrue(automatic.cafe_invite_enabled)
                self.assertEqual(automatic.cafe_invite_student, "")
                disabled = replace(config(), cafe_invite_student=value)
                self.assertFalse(disabled.cafe_invite_enabled)

    def test_invitation_name_override_requires_one_bounded_english_student_name(self):
        for value in ("Hoshino\nShiroko", "Hoshino\x00", "x" * 101, False):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                replace(config(), cafe_invite_enabled=True, cafe_invite_student=value)
        with self.assertRaisesRegex(ConfigError, "English"):
            replace(config(), cafe_invite_enabled=True, cafe_invite_student="ホシノ")
        selected = replace(config(), cafe_invite_enabled=True, cafe_invite_student="Hoshino (Swimsuit)")
        self.assertEqual(selected.cafe_invite_student, "Hoshino (Swimsuit)")

    def test_state_directory_requires_a_valid_path(self):
        for value in ("", "   ", False, "state\x00data"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                replace(config(), state_dir=value)

    def test_cafe_fields_reject_internal_prefixes_and_wrong_tables(self):
        invalid_tables = ('[cafe]\ncafe_schedule_enabled=true\n',
                          '[restart]\ncafe_schedule_enabled=true\n',
                          '[storage]\ncafe_invite_student="Hoshino"\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            for table in invalid_tables:
                path.write_text('[device]\nserial="127.0.0.1:5695"\n'
                                'package="com.nexon.bluearchive"\n' + table, encoding="utf-8")
                with self.subTest(table=table), self.assertRaises(ConfigError):
                    Config.from_file(path)

    def test_missing_or_misspelled_config_is_an_error(self):
        documents = ('[device]\npackage="com.nexon.bluearchive"\n',
                     '[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                     '[restart]\nauto_downlod=true\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            for document in documents:
                path.write_text(document, encoding="utf-8")
                with self.assertRaises(ConfigError):
                    Config.from_file(path)


class FakeSocket:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def sendall(self, value):
        self.sent += value

    def recv(self, size):
        # Real sockets can deliver a partial header/body.
        take = min(size, 2)
        value, self.response = self.response[:take], self.response[take:]
        return value


class DeviceTests(unittest.TestCase):
    def test_protocol_mismatch_never_invokes_a_server_contacting_client(self):
        socket = FakeSocket(b"OKAY00040028")
        version = subprocess.CompletedProcess([], 0, b"Android Debug Bridge version 1.0.41\n", b"")
        with patch("ba_automator.adb.socket.create_connection", return_value=socket), \
                patch("ba_automator.adb.subprocess.run", return_value=version) as run:
            with self.assertRaisesRegex(DeviceError, "server protocol 40 differs from client 41"):
                AdbDevice(config()).connect()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ["adb", "version"])
            self.assertEqual(socket.sent, b"000chost:version")

    def test_broken_shared_server_is_left_untouched(self):
        for response in (b"FAIL", b"OKAY0004", b"OKAYzzzz", b"OKAYffff"):
            with self.subTest(response=response):
                device = AdbDevice(config())
                device._client_protocol = 41
                with patch("ba_automator.adb.socket.create_connection", return_value=FakeSocket(response)), \
                        patch("ba_automator.adb.subprocess.run") as run:
                    with self.assertRaises(DeviceError):
                        device.force_stop()
                    run.assert_not_called()

    def test_all_commands_address_configured_device_and_bounded_server(self):
        answers = [b"connected to 127.0.0.1:5695\n", b"device\n", b"package:/data/app/game/base.apk\n",
                   b"", b"com.nexon.bluearchive/.MainActivity\n", b"Status: ok\n",
                   b"\x89PNG\r\n\x1a\n", b"",
                   b"mCurrentFocus=Window{123 u0 com.nexon.bluearchive/.MainActivity}\n",
                   b"Physical size: 1920x1080\nOverride size: 1280x720\n"]
        device = AdbDevice(config())
        device._client_protocol = 41
        with patch("ba_automator.adb.socket.create_connection",
                   side_effect=lambda *a, **k: FakeSocket(b"OKAY00040029")), \
                patch("ba_automator.adb.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 0, answer, b"") for answer in answers
                ]) as run:
            device.connect()
            device.verify_package()
            device.force_stop()
            device.launch()
            self.assertTrue(device.screenshot().startswith(b"\x89PNG"))
            device.tap(100, 200)
            self.assertEqual(device.foreground_package(), "com.nexon.bluearchive")
            self.assertEqual(device.display_size(), (1280, 720))
        prefix = ["adb", "-H", "127.0.0.1", "-P", "5037", "-s", "127.0.0.1:5695"]
        self.assertEqual(run.call_count, len(answers))
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:len(prefix)], prefix)
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertNotIn("shell", call.kwargs)
            self.assertNotIn("kill-server", call.args[0])
            self.assertNotIn("disconnect", call.args[0])

    def test_missing_server_may_start_without_resetting_any_server(self):
        device = AdbDevice(config())
        device._client_protocol = 41
        with patch("ba_automator.adb.socket.create_connection",
                   side_effect=ConnectionRefusedError(errno.ECONNREFUSED, "refused")), \
                patch("ba_automator.adb.subprocess.run",
                      return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            device.force_stop()
            self.assertEqual(run.call_count, 1)

    def test_package_and_launcher_fail_closed(self):
        device = AdbDevice(config())
        with patch.object(device, "_run", return_value=b""):
            with self.assertRaises(DeviceError):
                device.verify_package()
        with patch.object(device, "_run", return_value=b"com.YoStarEN.AzurLane/.MainActivity\n") as run:
            with self.assertRaises(DeviceError):
                device.launch()
            self.assertEqual(run.call_count, 1)

    def test_launch_draw_timeout_continues_only_with_verified_game_foreground(self):
        # Exact September 25 staging response: Android's 11-second draw wait
        # expired, while our 45-second transport deadline had not expired.
        component = b"com.nexon.bluearchive/.MxUnityPlayerActivity\n"
        response = (
            b"Starting: Intent { cmp=com.nexon.bluearchive/.MxUnityPlayerActivity }\n"
            b"Status: timeout\nLaunchState: UNKNOWN (-1)\n"
            b"Activity: com.nexon.bluearchive/.MxUnityPlayerActivity\n"
            b"WaitTime: 11058\nComplete\n"
        )
        for foreground in ("com.nexon.bluearchive", "com.uncube.launcher3", None):
            with self.subTest(foreground=foreground):
                device = AdbDevice(config())
                with patch.object(device, "_run", side_effect=[component, response]) as run, \
                        patch.object(device, "foreground_package", return_value=foreground) as focus:
                    if foreground == device.config.package:
                        with self.assertLogs("ba_automator.adb", level="WARNING"):
                            device.launch()
                    else:
                        with self.assertRaisesRegex(DeviceError, "Android could not launch"):
                            device.launch()
                    focus.assert_called_once_with()
                    self.assertEqual(run.call_count, 2)
                    self.assertEqual(run.call_args.args,
                                     ("shell", "am", "start", "-W", "-n", component.decode().strip()))
                    self.assertEqual(run.call_args.kwargs, {"timeout": 45})

    def test_launch_timeout_does_not_hide_explicit_or_malformed_errors(self):
        for response in (
            b"Status: timeout\nError: Activity not started\n",
            b"Status: timeout\n  Exception: permission denied\n",
            b"Status: error\n",
            b"Status: timeout because launch failed\n",
            b"Status: timeout\nStatus: error\n",
            b"Status: timeout\nStatus: ok\n",
        ):
            with self.subTest(response=response):
                device = AdbDevice(config())
                with patch.object(device, "_run", side_effect=[
                    b"com.nexon.bluearchive/.MainActivity\n", response,
                ]), patch.object(device, "foreground_package", return_value=device.config.package) as focus:
                    with self.assertRaisesRegex(DeviceError, "Android could not launch"):
                        device.launch()
                    focus.assert_not_called()

    def test_launch_transport_timeout_still_fails_without_foreground_fallback(self):
        device = AdbDevice(config())
        with patch.object(device, "_check_shared_server"), \
                patch("ba_automator.adb.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 0, b"com.nexon.bluearchive/.MainActivity\n", b""),
                    subprocess.TimeoutExpired(["adb", "shell", "am", "start"], 45),
                ]), patch.object(device, "foreground_package") as focus:
            with self.assertRaisesRegex(DeviceError, "ADB command failed"):
                device.launch()
            focus.assert_not_called()

    def test_out_of_bounds_taps_are_rejected_without_adb(self):
        device = AdbDevice(config())
        with patch.object(device, "_check_shared_server") as check, \
                patch.object(device, "_execute") as execute:
            for point in ((-1, 0), (1280, 0), (0, 720), (True, 20), (10.0, 20)):
                with self.subTest(point=point), self.assertRaises(DeviceError):
                    device.tap(*point)
            check.assert_not_called()
            execute.assert_not_called()

    def test_tap_rechecks_frame_deadline_after_delayed_server_preflight(self):
        device = AdbDevice(config())
        now = [0.0]

        def slow_preflight():
            now[0] += 6.0

        with patch.object(device, "_check_shared_server", side_effect=slow_preflight), \
                patch.object(device, "_execute") as execute:
            self.assertFalse(device.tap(640, 600, deadline=5.0, monotonic=lambda: now[0]))
            execute.assert_not_called()

    def test_fresh_tap_reports_sent_and_preserves_explicit_target(self):
        device = AdbDevice(config())
        with patch.object(device, "_check_shared_server") as check, \
                patch.object(device, "_execute", return_value=b"") as execute:
            self.assertTrue(device.tap(640, 600, deadline=5.0, monotonic=lambda: 1.0))
            check.assert_called_once()
            self.assertEqual(execute.call_args.args[0], [
                "adb", "-H", "127.0.0.1", "-P", "5037", "-s", "127.0.0.1:5695",
                "shell", "input", "tap", "640", "600",
            ])

    def test_swipe_uses_validated_endpoints_duration_and_explicit_device(self):
        device = AdbDevice(config())
        with patch.object(device, "_check_shared_server") as check, \
                patch.object(device, "_execute", return_value=b"") as execute:
            self.assertTrue(device.swipe((0, 0), (1279, 719), 100,
                                         deadline=5.0, monotonic=lambda: 1.0))
            check.assert_called_once()
            self.assertEqual(execute.call_args.args[0], [
                "adb", "-H", "127.0.0.1", "-P", "5037", "-s", "127.0.0.1:5695",
                "shell", "input", "swipe", "0", "0", "1279", "719", "100",
            ])
            self.assertEqual(execute.call_args.kwargs["timeout"], 10)

    def test_swipe_rechecks_freshness_after_slow_server_preflight(self):
        device = AdbDevice(config())
        now = [0.0]

        def slow_preflight():
            now[0] += 6.0

        with patch.object(device, "_check_shared_server", side_effect=slow_preflight), \
                patch.object(device, "_execute") as execute:
            self.assertFalse(device.swipe((640, 600), (640, 200), 400,
                                          deadline=5.0, monotonic=lambda: now[0]))
            execute.assert_not_called()

    def test_invalid_swipe_coordinates_or_duration_never_reach_adb(self):
        device = AdbDevice(config())
        cases = [((-1, 0), (640, 400), 400), ((0, 0), (1280, 719), 400),
                 ((0, True), (640, 400), 400), ((0, 0), (640.0, 400), 400),
                 ((0, 0), (640, 400), 99), ((0, 0), (640, 400), 2001),
                 ((0, 0), (640, 400), True)]
        with patch.object(device, "_check_shared_server") as check, \
                patch.object(device, "_execute") as execute:
            for start, end, duration in cases:
                with self.subTest(start=start, end=end, duration=duration), self.assertRaises(DeviceError):
                    device.swipe(start, end, duration)
            check.assert_not_called()
            execute.assert_not_called()

    def test_foreground_activity_fallback_and_no_invented_package(self):
        device = AdbDevice(config())
        with patch.object(device, "_run", side_effect=[b"mCurrentFocus=null\n",
                          b"mResumedActivity: ActivityRecord{ff u0 com.nexon.bluearchive/.Main t12}\n"]):
            self.assertEqual(device.foreground_package(), "com.nexon.bluearchive")
        with patch.object(device, "_run", return_value=b""):
            self.assertIsNone(device.foreground_package())

    def test_timeout_is_a_device_error(self):
        device = AdbDevice(config())
        with patch.object(device, "_check_shared_server"), \
                patch("ba_automator.adb.subprocess.run", side_effect=subprocess.TimeoutExpired("adb", 10)):
            with self.assertRaises(DeviceError):
                device.screenshot()


class LockTests(unittest.TestCase):
    def test_same_instance_excludes_another_runner_and_releases_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = config(lock_dir=Path(directory))
            with self.assertRaisesRegex(ValueError, "simulated crash"):
                with InstanceLock(selected):
                    with self.assertRaises(LockError):
                        with InstanceLock(selected):
                            self.fail("second runner acquired an already-owned device")
                    raise ValueError("simulated crash")
            with InstanceLock(selected):
                pass

    def test_different_instances_do_not_block_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = config(lock_dir=Path(directory))
            other = replace(selected, serial="127.0.0.1:5696")
            with InstanceLock(selected), InstanceLock(other):
                pass


if __name__ == "__main__":
    unittest.main()
