"""Process-lifetime runner locks, independently scoped to each emulator/game."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import BinaryIO

from .config import Config

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class LockError(RuntimeError):
    """Another runner owns the configured game instance or the lock is unavailable."""


class InstanceLock:
    def __init__(self, config: Config):
        identity = f"{config.serial}\0{config.package}".encode("utf-8")
        self.path: Path = config.lock_dir / f"instance-{hashlib.sha256(identity).hexdigest()[:24]}.lock"
        self._stream: BinaryIO | None = None

    def __enter__(self) -> InstanceLock:
        if self._stream is not None:
            raise LockError("This instance lock is already held by this runner")
        stream = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.open("a+b")
            # Windows byte-range locks need at least one byte to lock.
            if os.name == "nt":
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if stream is not None:
                stream.close()
            raise LockError(f"Cannot acquire instance lock {self.path}; another runner may be active") from exc
        self._stream = stream
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._stream is not None:
            try:
                if os.name == "nt":
                    self._stream.seek(0)
                    msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            finally:
                self._stream.close()
                self._stream = None
        # Keep the inode: deleting a lock file can allow two processes to own
        # different locked inodes at the same path. OS locks expire on process exit.
