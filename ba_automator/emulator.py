"""Optional recovery of a BlueStacks 5 instance on Windows.

Off unless ``BA_BLUESTACKS_INSTANCE`` names the instance, for example
``Pie64_4``. With it set, a job that finds the emulator unreachable or its
Android storage read-only restarts that one instance before the game is
launched. Other instances are never touched.

A host I/O stall can make the guest remount ``/data`` read-only. The game then
dies on launch with nothing on screen to explain it, and only an instance
restart brings the filesystem back.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .adb import DeviceError

LOGGER = logging.getLogger(__name__)
DEFAULT_INSTALL = r"C:\Program Files\BlueStacks_nxt"
DEFAULT_CONF = r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf"
STOP_SECONDS = 60
BOOT_SECONDS = 300
# After a reboot the sign-in task starts the instance and the manager
# together, so a job can find the player window up but Android still booting.
BOOTING_SECONDS = 240
READ_ONLY = "Android storage is read-only"
BOOTING = "Android has not finished booting"
ADB_SERVER = "shared ADB server is not responding"


def configured_instance() -> str | None:
    name = os.environ.get("BA_BLUESTACKS_INSTANCE", "").strip()
    return name if re.fullmatch(r"[A-Za-z0-9_]+", name) else None


def _install_dir() -> Path:
    return Path(os.environ.get("BA_BLUESTACKS_DIR", DEFAULT_INSTALL))


def display_name(name: str) -> str | None:
    """The window title BlueStacks gives this instance, from its own configuration."""
    conf = Path(os.environ.get("BA_BLUESTACKS_CONF", DEFAULT_CONF))
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(rf'^bst\.instance\.{re.escape(name)}\.display_name="([^"\r\n]+)"\s*$', text, re.M)
    return match.group(1) if match else None


def player_pids(name: str, run=subprocess.run) -> list[int]:
    """Process IDs of the player window that belongs to this instance only.

    The player's command line is not readable from an ordinary process, so the
    window is matched by the instance's display name instead.
    """
    title = display_name(name)
    if not title:
        return []
    result = run(["tasklist", "/v", "/fi", "imagename eq HD-Player.exe", "/fo", "csv"],
                 stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                 errors="replace", timeout=60, check=False)
    rows = list(csv.reader(io.StringIO(result.stdout)))
    return [int(row[1]) for row in rows[1:]
            if len(row) >= 3 and row[1].isdigit() and row[-1].strip() == title]


def restart_instance(name: str, *, run=subprocess.run, spawn=subprocess.Popen,
                     monotonic=time.monotonic, sleep=time.sleep) -> None:
    """Close this instance's player (gently, then forcibly) and start it again."""
    if sys.platform != "win32":
        raise DeviceError("Emulator recovery is only implemented for BlueStacks 5 on Windows")
    player = _install_dir() / "HD-Player.exe"
    if not player.is_file():
        raise DeviceError(f"Cannot restart the emulator instance: {player} was not found")
    for pid in player_pids(name, run):
        # Without /F this sends a close request, so the VM can shut down cleanly.
        run(["taskkill", "/PID", str(pid)], stdin=subprocess.DEVNULL, capture_output=True, check=False)
    deadline = monotonic() + STOP_SECONDS
    while player_pids(name, run) and monotonic() < deadline:
        sleep(3)
    for pid in player_pids(name, run):
        run(["taskkill", "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL, capture_output=True, check=False)
        sleep(3)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    spawn([str(player), "--instance", name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)


def _problem(device) -> str | None:
    """Why this device cannot run a job, or None when it looks healthy."""
    try:
        device.connect()
    except DeviceError as exc:
        if "shared ADB server" in str(exc):
            return f"{ADB_SERVER}: {str(exc)[:200]}"
        return f"emulator is unreachable: {str(exc)[:200]}"
    writable = getattr(device, "storage_writable", None)
    if writable is not None and not writable():
        return READ_ONLY
    return None


def _ready(device) -> str | None:
    problem = _problem(device)
    if problem is None and not getattr(device, "boot_completed", lambda: True)():
        return BOOTING
    return problem


def ensure_ready(device, record=lambda *args, **fields: None, *, restart=restart_instance,
                 running=player_pids, monotonic=time.monotonic, sleep=time.sleep) -> bool:
    """Connect to the configured device; recover its instance once when allowed.

    Returns True when the instance had to be restarted.
    """
    name = configured_instance()
    problem = _ready(device) if name else _problem(device)
    if problem is None:
        return False
    if problem.startswith(ADB_SERVER):
        # Live, the shared server stopped answering for two hours and each job
        # restarted this instance in vain. The server serves every emulator
        # worker; it is never restarted from here, so wait for the next job.
        raise DeviceError(f"The {problem}. Blue Archive was left running; the next job will try again")
    if name is None:
        raise DeviceError(f"The {problem}. Restart the emulator instance and run the task again")
    if problem != READ_ONLY and running(name):
        # Its window is up: let it finish starting before deciding it is stuck.
        record("emulator_booting", instance=name, reason=problem)
        deadline = monotonic() + BOOTING_SECONDS
        while monotonic() < deadline and problem != READ_ONLY:
            sleep(6)
            problem = _ready(device)
            if problem is None:
                record("emulator_booted", instance=name)
                return False
    LOGGER.warning("emulator: %s; restarting BlueStacks instance %s", problem, name)
    record("emulator_recovery", instance=name, reason=problem)
    restart(name)
    deadline = monotonic() + BOOT_SECONDS
    while True:
        sleep(6)
        problem = _ready(device)
        if problem is None:
            record("emulator_recovered", instance=name)
            LOGGER.warning("emulator: BlueStacks instance %s is ready again", name)
            return True
        if monotonic() >= deadline:
            raise DeviceError(f"BlueStacks instance {name} did not recover after a restart: {problem}")
