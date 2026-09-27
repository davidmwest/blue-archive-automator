"""Diagnostic commands retain native evidence while validating game geometry."""

import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ba_automator import cli
from ba_automator.config import Config


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    config = Config(serial='127.0.0.1:5695', package='com.nexon.bluearchive',
                    run_dir=tmp_path / 'runs', state_dir=tmp_path / 'state',
                    lock_dir=tmp_path / 'locks')
    frame = np.full((1440, 2560, 3), (80, 120, 170), dtype=np.uint8)
    native = cv2.imencode('.png', frame)[1].tobytes()
    device = SimpleNamespace(connect=lambda: None, verify_package=lambda: None,
                             display_size=lambda: (2560, 1440),
                             foreground_package=lambda: config.package,
                             screenshot=lambda: native)
    monkeypatch.setattr(cli.Config, 'from_file', lambda _: config)
    monkeypatch.setattr(cli, 'AdbDevice', lambda _: device)
    return device, native


def test_probe_reports_native_display_size(diagnostic, capsys):
    assert cli.main(['probe']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['display'] == [2560, 1440]
    assert result['recognition_display'] == [1280, 720]


def test_capture_preserves_original_1440p_png(diagnostic, tmp_path):
    _, native = diagnostic
    destination = tmp_path / 'capture.png'
    assert cli.main(['capture', '--output', str(destination)]) == 0
    assert destination.read_bytes() == native
    assert cv2.imread(str(destination)).shape == (1440, 2560, 3)


def test_probe_rejects_a_different_aspect_ratio(diagnostic, capsys):
    device, _ = diagnostic
    device.display_size = lambda: (1920, 1200)
    assert cli.main(['probe']) == 1
    assert '16:9' in capsys.readouterr().err
