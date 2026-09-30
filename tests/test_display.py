"""Native captures and canonical decisions must agree before any scaled input."""

from unittest.mock import patch

import cv2
import numpy as np
import pytest

from ba_automator.adb import AdbDevice, DeviceError
from ba_automator.config import Config
from ba_automator.display import GameDisplay, is_supported_size
from ba_automator.vision import decode_frame


def device():
    return AdbDevice(Config(serial="127.0.0.1:5695", package="com.nexon.bluearchive"))


def png(frame):
    return cv2.imencode(".png", frame)[1].tobytes()


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)])
def test_game_geometry_and_frame_normalization(size):
    assert is_supported_size(size)
    display = GameDisplay(*size)
    assert display.point(640, 360) == (size[0] // 2, size[1] // 2)
    assert display.point(1279, 719)[0] < size[0]
    original = np.zeros((720, 1280, 3), np.uint8)
    original[200:400, 300:600] = (50, 120, 200)
    native = cv2.resize(original, size, interpolation=cv2.INTER_NEAREST)
    assert np.array_equal(decode_frame(png(native)), original)


@pytest.mark.parametrize("size", [(720, 1280), (2560, 1600), (1280, 719), (5120, 2880),
                                  (640, 360), (1280.0, 720)])
def test_unsupported_layouts_are_rejected(size):
    assert not is_supported_size(size)
    with pytest.raises(ValueError):
        GameDisplay(*size)


def test_native_capture_remains_unchanged_and_inputs_scale_both_axes():
    dev = device()
    native = png(np.zeros((1440, 2560, 3), np.uint8))
    with patch.object(dev, "_run", return_value=native):
        assert dev.screenshot() == native
    with patch.object(dev, "_check_shared_server"), \
            patch.object(dev, "current_display_size", return_value=(2560, 1440)), \
            patch.object(dev, "_execute") as execute:
        assert dev.tap(123, 456)
        assert execute.call_args.args[0][-3:] == ["tap", "246", "912"]
        assert dev.swipe((100, 200), (600, 500), 400)
        assert execute.call_args.args[0][-6:] == ["swipe", "200", "400", "1200", "1000", "400"]


@pytest.mark.parametrize("frame,current", [(None, (2560, 1440)),
    ((1280, 720), (2560, 1440)), ((2560, 1440), (1280, 720)),
    ((1440, 2560), (2560, 1440)), ((2560, 1600), (2560, 1600))])
@pytest.mark.parametrize("gesture", ["tap", "swipe"])
def test_missing_stale_or_unsupported_geometry_never_sends_input(frame, current, gesture):
    dev = device()
    dev._frame_size = frame
    with patch.object(dev, "_check_shared_server"), \
            patch.object(dev, "current_display_size", return_value=current), \
            patch.object(dev, "_execute") as execute:
        with pytest.raises(DeviceError):
            if gesture == "tap":
                dev.tap(640, 360)
            else:
                dev.swipe((640, 360), (400, 360))
        execute.assert_not_called()


@pytest.mark.parametrize("gesture", ["tap", "swipe"])
def test_expiry_during_geometry_recheck_prevents_input(gesture):
    dev = device()
    dev._frame_size = (2560, 1440)
    now = [1.0]

    def delayed_size():
        now[0] = 6.0
        return (2560, 1440)

    with patch.object(dev, "_check_shared_server"), \
            patch.object(dev, "current_display_size", side_effect=delayed_size), \
            patch.object(dev, "_execute") as execute:
        if gesture == "tap":
            sent = dev.tap(640, 360, deadline=5, monotonic=lambda: now[0])
        else:
            sent = dev.swipe((640, 360), (400, 360), deadline=5, monotonic=lambda: now[0])
        assert not sent
        execute.assert_not_called()


def test_bad_capture_clears_old_geometry():
    dev = device()
    dev._frame_size = (1280, 720)
    with patch.object(dev, "_run", return_value=b"invalid"):
        with pytest.raises(DeviceError):
            dev.screenshot()
    assert dev._frame_size is None


def test_unobserved_billing_resolution_is_not_silently_enabled():
    dev = device()
    with patch.object(dev, "_execute") as execute:
        with pytest.raises(DeviceError, match="Unsupported Google Play"):
            dev.tap_billing(600, 1000, size=(1080, 1920), deadline=20)
        execute.assert_not_called()


@pytest.mark.parametrize("gesture", ["tap", "swipe"])
def test_rotation_with_unchanged_wm_size_invalidates_landscape_capture(gesture):
    dev = device()
    dev._frame_size = (2560, 1440)
    with patch.object(dev, "_check_shared_server"), \
            patch.object(dev, "display_size", return_value=(2560, 1440)), \
            patch.object(dev, "current_display_size", return_value=(1440, 2560)), \
            patch.object(dev, "_execute") as execute:
        with pytest.raises(DeviceError, match="changed since capture"):
            if gesture == "tap":
                dev.tap(640, 360)
            else:
                dev.swipe((640, 360), (400, 360))
        execute.assert_not_called()
        assert dev._frame_size is None


def test_current_geometry_reads_only_the_primary_display():
    dev = device()
    dump = (b"  Display: mDisplayId=2\n    init=800x600 320dpi cur=800x600\n"
            b"  Display: mDisplayId=0 (organized)\n"
            b"    init=1280x720 640dpi mMinSizeOfResizeableTaskDp=220 cur=1440x2560 app=1440x2560\n")
    with patch.object(dev, "_run", return_value=dump):
        assert dev.current_display_size() == (1440, 2560)


@pytest.mark.parametrize("dump", [b"", b"Physical size: 2560x1440",
    b"Display: mDisplayId=1\n init=1280x720 cur=1280x720\n",
    b"Display: mDisplayId=0\n cur=1280x720\nDisplay: mDisplayId=0\n cur=1280x720\n"])
def test_missing_or_ambiguous_logical_geometry_is_rejected(dump):
    dev = device()
    with patch.object(dev, "_run", return_value=dump):
        with pytest.raises(DeviceError, match="current display geometry"):
            dev.current_display_size()
