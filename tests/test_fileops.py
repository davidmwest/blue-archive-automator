"""Atomic replacement retries only the Windows sharing violation."""

from pathlib import Path

import pytest

from ba_automator import fileops
from ba_automator.fileops import replace_file


def test_replaces_destination(tmp_path):
    source, destination = tmp_path / "new", tmp_path / "current"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    replace_file(source, destination)
    assert destination.read_text(encoding="utf-8") == "new"
    assert not source.exists()


def test_retries_while_a_reader_holds_the_destination(tmp_path, monkeypatch):
    source, destination = tmp_path / "new", tmp_path / "current"
    source.write_text("new", encoding="utf-8")
    original, calls, naps = Path.replace, [], []

    def busy_twice(self, target):
        calls.append(target)
        if len(calls) <= 2:
            raise PermissionError(13, "Access is denied")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", busy_twice)
    replace_file(source, destination, sleep=naps.append)
    assert len(calls) == 3 and naps == [fileops.DELAY] * 2
    assert destination.read_text(encoding="utf-8") == "new"


def test_persistent_sharing_violation_is_reported(tmp_path, monkeypatch):
    def busy(self, target):
        raise PermissionError(13, "Access is denied")

    naps = []
    monkeypatch.setattr(Path, "replace", busy)
    with pytest.raises(PermissionError):
        replace_file(tmp_path / "new", tmp_path / "current", sleep=naps.append)
    assert len(naps) == fileops.ATTEMPTS - 1


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    calls = []

    def full(self, target):
        calls.append(target)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", full)
    with pytest.raises(OSError, match="disk full"):
        replace_file(tmp_path / "new", tmp_path / "current", sleep=lambda _: None)
    assert len(calls) == 1
