"""Atomic replacement that tolerates brief Windows sharing violations."""

from __future__ import annotations

from pathlib import Path
import time

ATTEMPTS = 40
DELAY = 0.05


def replace_file(source, destination, *, sleep=time.sleep) -> None:
    """Replace ``destination`` with ``source``; retry while a reader holds it open.

    Windows refuses to replace a file another process has open: the dashboard
    reading a frame or state file, an indexer, a backup agent. POSIX systems do
    not raise here, so only the first attempt runs. Other errors propagate at once.
    """
    for remaining in range(ATTEMPTS - 1, -1, -1):
        try:
            Path(source).replace(destination)
            return
        except PermissionError:
            if not remaining:
                raise
            sleep(DELAY)
