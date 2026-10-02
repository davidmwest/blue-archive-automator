from __future__ import annotations

import http.client
import json
import threading
import time

import cv2
import numpy as np
import pytest

from ba_automator.locking import InstanceLock, LockError
from ba_automator.manual_control import compare_tap_frames
from ba_automator.server import ApiError, DashboardController, create_server


def png(image):
    return cv2.imencode(".png", image)[1].tobytes()


@pytest.fixture
def screen():
    image = np.full((1440, 2560, 3), 180, dtype=np.uint8)
    cv2.rectangle(image, (150, 100), (600, 200), (25, 100, 200), -1)
    cv2.rectangle(image, (1100, 600), (1400, 840), (250, 200, 40), -1)
    return image


def test_frame_guard_tolerates_small_animation_but_protects_target(screen):
    animated = screen.copy()
    cv2.circle(animated, (100, 100), 20, (0, 0, 0), -1)
    assert compare_tap_frames(png(screen), png(animated), 620, 360).matches
    assert not compare_tap_frames(png(screen), png(animated), 50, 50).matches
    assert compare_tap_frames(png(screen), png(screen), 0, 0).matches
    assert compare_tap_frames(png(screen), png(screen), 1279, 719).matches


def test_frame_guard_rejects_modal_dimming_and_geometry_change(screen):
    assert not compare_tap_frames(png(screen), png(screen // 2), 620, 360).matches
    assert compare_tap_frames(png(screen), png(screen[::2, ::2]), 620, 360).reason == "display size changed"
    modal = screen.copy()
    modal[300:1100, 500:2000] = 250
    assert not compare_tap_frames(png(screen), png(modal), 50, 50).matches


def test_background_motion_allows_stationary_controls_but_not_moving_targets():
    before = np.full((1440, 2560, 3), 180, dtype=np.uint8)
    after = before.copy()
    before[100:460, 500:1100] = 0
    after[100:460, 650:1250] = 0
    assert compare_tap_frames(png(before), png(after), 100, 620).matches
    assert not compare_tap_frames(png(before), png(after), 280, 150).matches
    # Widespread subtle shading still invalidates the screen.
    assert not compare_tap_frames(png(after), png(np.maximum(after, 10) - 10), 100, 620).matches


def test_bright_lobby_animation_does_not_block_an_unchanged_mail_button():
    before = np.zeros((720, 1280, 3), dtype=np.uint8)
    after = before.copy()
    # Less than 10% of the screen changes, but bright sprites exceed the old
    # global-mean cutoff. The mail button and surrounding top bar stay put.
    after[100:300, 300:650] = 255
    after[58:70, 1119:1192] = 255  # Animated scenery beneath the top bar.
    for frame in (before, after):
        frame[14:55, 1130:1180] = 180
    assert compare_tap_frames(png(before), png(after), 1155, 34).matches
    # A changed button still fails, even with the same background animation.
    after[20:48, 1140:1170] = 0
    assert not compare_tap_frames(png(before), png(after), 1155, 34).matches


@pytest.fixture
def manual(tmp_path, screen):
    path = tmp_path / "local.toml"
    path.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                    '[storage]\nrun_dir="runs"\nlock_dir="locks"\n')

    class Device:
        frame = png(screen)
        calls = []
        reads = 0
        package = "com.nexon.bluearchive"
        after_tap_error = False
        tap_sent = True

        def connect(self):
            pass

        def verify_package(self):
            pass

        def foreground_package(self):
            return self.package

        def screenshot(self):
            self.reads += 1
            if self.calls and self.after_tap_error:
                raise RuntimeError("device disconnected after input")
            return self.frame

        def swipe(self, start, end, duration_ms, *, deadline):
            assert deadline > time.monotonic()
            assert start == end and duration_ms == 150
            self.calls.append(start)
            return self.tap_sent

    device = Device()
    controller = DashboardController(path, device_factory=lambda _: device)
    controller.pause()
    controller._save_capture(controller.config, device.frame)
    try:
        yield controller, device
    finally:
        controller.close()


def test_tap_uses_exact_displayed_bytes_and_token_once(manual, screen):
    controller, device = manual
    displayed, token = controller.preview_frame()
    assert displayed == device.frame
    result = controller.manual_tap(token, 640, 360)
    assert result == {"sent": True, "captured": True}
    assert device.calls == [(640, 360)]
    assert controller.manual_tap(token, 640, 360)["sent"] is False
    assert len(device.calls) == 1

    _, stale = controller.preview_frame()
    device.frame = png(screen // 2)
    # The mutable screenshot path changed after the browser loaded its image.
    controller._save_capture(controller.config, device.frame)
    assert controller.manual_tap(stale, 640, 360) == {"sent": False, "reason": "screen changed"}
    assert len(device.calls) == 1
    assert controller.frame_bytes() == device.frame


@pytest.mark.parametrize("x,y", [(-1, 0), (1280, 0), (0, 720), (1.5, 2), (True, 2), (None, 2)])
def test_tap_rejects_invalid_coordinates_without_device_access(manual, x, y):
    controller, device = manual
    _, token = controller.preview_frame()
    with pytest.raises(ApiError) as error:
        controller.manual_tap(token, x, y)
    assert error.value.status == 400
    assert not device.reads and not device.calls


def test_expired_or_evicted_preview_never_sends_input(manual):
    controller, device = manual
    _, token = controller.preview_frame()
    entry = controller._preview_frames[-1]
    controller._preview_frames[-1] = (token, time.monotonic() - 121, entry[2])
    assert controller.manual_tap(token, 640, 360)["sent"] is False
    _, token = controller.preview_frame()
    for _ in range(4):
        controller.preview_frame()
    assert controller.manual_tap(token, 640, 360)["sent"] is False
    assert not device.reads and not device.calls


def test_foreground_lock_and_queue_guards(manual):
    controller, device = manual
    _, token = controller.preview_frame()
    controller.resume()
    with pytest.raises(ApiError, match="Pause the queue"):
        controller.manual_tap(token, 640, 360)
    controller.pause()
    with InstanceLock(controller.config), pytest.raises(LockError):
        controller.manual_tap(token, 640, 360)
    assert not controller._capturing
    _, token = controller.preview_frame()
    device.package = "com.android.launcher"
    with pytest.raises(ApiError, match="Open Blue Archive"):
        controller.manual_tap(token, 640, 360)
    assert not device.reads and not device.calls


def test_resume_during_preflight_aborts_input_and_capture_excludes_concurrent_input(manual):
    controller, device = manual
    _, token = controller.preview_frame()
    screenshot = device.screenshot

    def concurrent_requests():
        with pytest.raises(ApiError):
            controller.capture()
        with pytest.raises(ApiError):
            controller.manual_tap(token, 100, 100)
        controller.resume()
        return screenshot()

    device.screenshot = concurrent_requests
    with pytest.raises(ApiError, match="queue resumed"):
        controller.manual_tap(token, 640, 360)
    assert not device.calls and not controller._capturing


def test_foreground_change_during_comparison_aborts(manual):
    controller, device = manual
    _, token = controller.preview_frame()

    def screenshot():
        device.package = "com.android.launcher"
        return device.frame

    device.screenshot = screenshot
    with pytest.raises(ApiError, match="foreground app changed"):
        controller.manual_tap(token, 640, 360)
    assert not device.calls


def test_successful_input_remains_success_when_followup_capture_fails(manual):
    controller, device = manual
    _, token = controller.preview_frame()
    device.after_tap_error = True
    assert controller.manual_tap(token, 640, 360) == {"sent": True, "captured": False}
    assert controller.manual_tap(token, 640, 360)["sent"] is False
    assert len(device.calls) == 1


def test_device_deadline_rejection_is_not_reported_as_sent(manual):
    controller, device = manual
    _, token = controller.preview_frame()
    device.tap_sent = False
    assert controller.manual_tap(token, 640, 360) == {"sent": False, "reason": "fresh frame expired"}


def test_http_preview_token_and_csrf_protection(manual):
    controller, device = manual
    server = create_server(controller, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()

    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(method, path, json.dumps(body) if body else None,
                           {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), data

    try:
        code, headers, frame = request("GET", "/api/frame")
        assert code == 200 and frame == device.frame
        payload = {"frame_token": headers["X-Game-Frame"], "x": 640, "y": 360}
        assert request("POST", "/api/manual-tap", payload)[0] == 403
        csrf = {"X-CSRF-Token": controller.csrf_token}
        assert request("POST", "/api/manual-tap", payload, {**csrf, "Origin": "https://evil.example"})[0] == 403
        assert request("POST", "/api/manual-tap", {**payload, "command": "shell"}, csrf)[0] == 400
        code, _, data = request("POST", "/api/manual-tap", payload, csrf)
        assert code == 200 and json.loads(data)["sent"] is True
        assert device.calls == [(640, 360)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
