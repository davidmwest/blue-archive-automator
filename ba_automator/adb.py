"""Target-specific ADB transport that leaves other emulator connections alone."""

from __future__ import annotations

import errno
import logging
import os
import re
import socket
import struct
import subprocess
import time
from typing import Callable

from .config import Config

LOGGER = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    """An ADB operation failed or could disturb an incompatible shared server."""


class AdbDevice:
    SERVER_HOST = "127.0.0.1"
    SERVER_PORT = 5037

    def __init__(self, config: Config):
        self.config = config
        self._client_protocol: int | None = None

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        for name in ("ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS", "ANDROID_ADB_SERVER_PORT"):
            environment.pop(name, None)
        return environment

    def _execute(self, arguments: list[str], timeout: float = 30) -> bytes:
        try:
            result = subprocess.run(
                arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=timeout, check=False, env=self._environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeviceError(f"ADB command failed: {exc}") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = result.stdout.decode("utf-8", errors="replace").strip()
            raise DeviceError(f"ADB command exited with {result.returncode}: {detail[:1000]}")
        return result.stdout

    @staticmethod
    def _receive(connection: socket.socket, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            part = connection.recv(size - len(output))
            if not part:
                raise DeviceError("Shared ADB server closed the protocol-version response")
            output.extend(part)
        return bytes(output)

    def _check_shared_server(self) -> None:
        # `adb version` is entirely local and never attempts to start/restart a server.
        if self._client_protocol is None:
            output = self._execute([self.config.adb_path, "version"], timeout=10)
            match = re.search(rb"Android Debug Bridge version 1\.0\.(\d+)", output)
            if not match:
                raise DeviceError("Cannot determine ADB client protocol; refusing shared-server access")
            self._client_protocol = int(match.group(1))
        try:
            connection = socket.create_connection((self.SERVER_HOST, self.SERVER_PORT), timeout=2)
        except OSError as exc:
            if exc.errno in (errno.ECONNREFUSED, 10061):
                return  # No shared server exists; this client may start its own compatible server.
            raise DeviceError(f"Cannot inspect shared ADB server without changing it: {exc}") from exc
        try:
            with connection:
                request = b"host:version"
                connection.sendall(f"{len(request):04x}".encode("ascii") + request)
                status = self._receive(connection, 4)
                if status != b"OKAY":
                    raise DeviceError("Shared ADB server rejected the version check; leaving it untouched")
                size = int(self._receive(connection, 4), 16)
                if not 1 <= size <= 32:
                    raise DeviceError("Shared ADB server sent an invalid version response")
                protocol = int(self._receive(connection, size), 16)
        except (OSError, ValueError) as exc:
            raise DeviceError(f"Cannot inspect shared ADB server without changing it: {exc}") from exc
        if protocol != self._client_protocol:
            raise DeviceError(
                f"Shared ADB server protocol {protocol} differs from client {self._client_protocol}; "
                "use the same ADB executable as the Azur Lane daemon. The server was left untouched."
            )

    def _command(self, *arguments: str) -> list[str]:
        return [
            self.config.adb_path, "-H", self.SERVER_HOST, "-P", str(self.SERVER_PORT),
            "-s", self.config.serial, *arguments,
        ]

    def _run(self, *arguments: str, timeout: float = 30) -> bytes:
        self._check_shared_server()
        return self._execute(self._command(*arguments), timeout=timeout)

    def connect(self) -> None:
        self._run("connect", self.config.serial, timeout=15)
        state = self._run("get-state", timeout=10).decode("utf-8", errors="replace").strip()
        if state != "device":
            raise DeviceError(f"Configured Blue Archive device is not ready: {state}")

    def verify_package(self) -> None:
        output = self._run("shell", "pm", "path", self.config.package).decode("utf-8", errors="replace")
        if not any(line.startswith("package:") for line in output.splitlines()):
            raise DeviceError(f"Configured Blue Archive package is not installed: {self.config.package}")

    def force_stop(self) -> None:
        self._run("shell", "am", "force-stop", self.config.package)

    def launch(self) -> None:
        output = self._run(
            "shell", "cmd", "package", "resolve-activity", "--brief", "-a",
            "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", self.config.package,
        ).decode("utf-8", errors="replace")
        component = None
        for line in reversed(output.splitlines()):
            line = line.strip()
            if re.fullmatch(re.escape(self.config.package) + r"/[A-Za-z0-9_.$]+", line):
                component = line
                break
        if not component:
            raise DeviceError("Could not resolve the configured game's launcher activity")
        result = self._run("shell", "am", "start", "-W", "-n", component, timeout=45)
        text = result.decode("utf-8", errors="replace")
        if re.search(r"(?im)^[ \t]*(?:error|exception|status:[ \t]*error)\b", text):
            raise DeviceError(f"Android could not launch Blue Archive: {text[:1000].strip()}")
        if re.search(r"(?im)^status:[ \t]*timeout\b", text):
            # Android's activity-draw wait can expire while the game is already
            # foreground. This is not proof of readiness: restart still applies
            # its own screen recognition, time limits, and one recovery attempt.
            # Accept only the observed advisory status, never a transport timeout
            # or a failed launch that leaves another app in the foreground.
            statuses = re.findall(r"(?im)^status:[ \t]*([^\r\n]*)", text)
            if ([status.strip().lower() for status in statuses] != ["timeout"]
                    or self.foreground_package() != self.config.package):
                raise DeviceError(f"Android could not launch Blue Archive: {text[:1000].strip()}")
            LOGGER.warning(
                "Android launch wait timed out with Blue Archive in the foreground; "
                "continuing bounded startup recognition"
            )

    def screenshot(self) -> bytes:
        output = self._run("exec-out", "screencap", "-p", timeout=20)
        if not output.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DeviceError("ADB screenshot did not return PNG data")
        return output

    def tap(
        self, x: int, y: int, *, deadline: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> bool:
        """Send a tap, or return False if its frame expired during server preflight."""
        if (type(x) is not int or type(y) is not int
                or not 0 <= x < self.config.expected_width or not 0 <= y < self.config.expected_height):
            raise DeviceError("Tap coordinates must be integers inside the fixed 1280×720 display")
        self._check_shared_server()
        if deadline is not None and monotonic() > deadline:
            return False
        self._execute(self._command("shell", "input", "tap", str(x), str(y)), timeout=10)
        return True

    def foreground_package(self) -> str | None:
        output = self._run("shell", "dumpsys", "window", "windows", timeout=15).decode(
            "utf-8", errors="replace",
        )
        patterns = (
            r"mCurrentFocus=Window\{[^\n}]*?\s([A-Za-z][A-Za-z0-9_.]*)/[^\s}]+",
            r"mFocusedApp=[^\n]*?\s([A-Za-z][A-Za-z0-9_.]*)/[^\s}]+",
        )
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                return match.group(1)
        output = self._run("shell", "dumpsys", "activity", "activities", timeout=15).decode(
            "utf-8", errors="replace",
        )
        match = re.search(
            r"(?:mResumedActivity|topResumedActivity|ResumedActivity):?[^\n]*?\s"
            r"([A-Za-z][A-Za-z0-9_.]*)/[^\s}]+", output,
        )
        return match.group(1) if match else None

    def tap_billing(self, x: int, y: int, *, size: tuple[int, int], deadline: float,
                    monotonic=time.monotonic) -> bool:
        """Explicit Google Play input; ordinary game taps remain landscape-only."""
        if (size != (720, 1280) or type(x) is not int or type(y) is not int
                or not 0 <= x < size[0] or not 0 <= y < size[1]):
            raise DeviceError('Unsupported Google Play checkout display or target')
        if self.foreground_package() != 'com.android.vending':
            raise DeviceError('Google Play is not the foreground checkout')
        png = self.screenshot()
        if len(png) < 24 or struct.unpack('>II', png[16:24]) != size:
            raise DeviceError('Google Play orientation changed before input')
        if self.foreground_package() != 'com.android.vending':
            raise DeviceError('Google Play foreground changed before input')
        self._check_shared_server()
        if monotonic() > deadline:
            return False
        self._execute(self._command('shell', 'input', 'tap', str(x), str(y)), timeout=10)
        return True

    def swipe(self, start: tuple[int, int], end: tuple[int, int], duration_ms=400,
              *, deadline=None, monotonic=time.monotonic) -> bool:
        for x, y in (start, end):
            if (type(x) is not int or type(y) is not int
                    or not 0 <= x < 1280 or not 0 <= y < 720):
                raise DeviceError("Swipe coordinates must be integers inside the display")
        if type(duration_ms) is not int or not 100 <= duration_ms <= 2000:
            raise DeviceError("Swipe duration must be from 100 to 2000 milliseconds")
        self._check_shared_server()
        if deadline is not None and monotonic() > deadline:
            return False
        self._execute(self._command("shell", "input", "swipe", *(str(n) for n in (*start, *end)),
                                    str(duration_ms)), timeout=10)
        return True

    def display_size(self) -> tuple[int, int]:
        output = self._run("shell", "wm", "size", timeout=10).decode("utf-8", errors="replace")
        sizes = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", output)
        if not sizes:
            raise DeviceError("Cannot read the selected emulator's display size")
        return tuple(map(int, sizes[-1]))
