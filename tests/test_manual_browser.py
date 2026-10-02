"""Optional real-browser regression: install Playwright and Chromium to run it."""
from pathlib import Path
import shutil
import threading

import cv2
import numpy as np
import pytest

from ba_automator.server import DashboardController, create_server


def test_click_during_refresh_forwards_once_even_when_game_changes(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    config = tmp_path / "local.toml"
    config.write_text('[device]\nserial="127.0.0.1:5695"\npackage="com.nexon.bluearchive"\n'
                      '[storage]\nrun_dir="runs"\nlock_dir="locks"\n')
    capturing = threading.Event()
    release = threading.Event()
    release.set()

    class Device:
        image = np.full((1440, 2560, 3), 180, dtype=np.uint8)

        def __init__(self, selected):
            self.config = selected
            self.taps = []

        def connect(self):
            pass

        def verify_package(self):
            pass

        def foreground_package(self):
            return self.config.package

        def screenshot(self):
            capturing.set()
            assert release.wait(15), "test did not release the read-only capture"
            return cv2.imencode(".png", self.image)[1].tobytes()

        def swipe(self, start, end, duration_ms, *, deadline):
            assert start == end and duration_ms == 150
            self.taps.append(start)
            return True

    devices = []

    def factory(selected):
        if not devices:
            devices.append(Device(selected))
        return devices[0]

    controller = DashboardController(config, device_factory=factory)
    controller.capture()
    server = create_server(controller, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as p:
            mac_chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            executable = str(mac_chrome) if mac_chrome.exists() else (
                shutil.which('chromium') or shutil.which('google-chrome') or p.chromium.executable_path)
            if not Path(executable).is_file():
                pytest.skip("Chromium is not installed")
            browser = p.chromium.launch(executable_path=executable, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.post_data_json)
                    if request.url.endswith('/api/manual-tap') else None)
            page.goto(f'http://127.0.0.1:{server.server_port}/#screen', wait_until='networkidle')
            # Simulate a tab retaining the old crosshair stylesheet.
            page.add_style_tag(content='.manual-playing #game-frame {cursor:crosshair}')
            page.locator('#screen-stage').scroll_into_view_if_needed()
            page.locator('#play-here').click()

            def ready():
                page.wait_for_function("""() =>
                    document.querySelector('#screen-stage').classList.contains('manual-playing')
                    && !document.querySelector('#capture').disabled""")

            ready()
            image = page.locator('#game-frame')
            playwright.expect(image).to_have_css('cursor', 'pointer')
            device = devices[0]
            # Hold a background capture open, then click twice on the displayed
            # picture. The click must wait for the capture without being lost
            # or replayed, and without adopting the refreshed image's token.
            for changed in (False, True):
                ready()
                previous = len(device.taps)
                if changed:
                    device.image[:] = 30  # Game moved on behind the old preview.
                capturing.clear()
                release.clear()
                page.locator('#capture').click()
                assert capturing.wait(5)
                playwright.expect(image).to_have_css('cursor', 'pointer')
                box = image.bounding_box()
                image.dblclick(position={"x": box['width'] / 2, "y": box['height'] / 2})
                playwright.expect(page.locator('#manual-play-hint')).to_contain_text('tap saved')
                assert len(device.taps) == previous
                with page.expect_response('**/api/manual-tap') as response:
                    release.set()
                outcome = response.value.json()
                assert outcome['sent'] is True
                assert len(device.taps) == previous + 1
                assert len(requests) == (2 if changed else 1)
                assert abs(device.taps[-1][0] - 640) <= 2
                assert abs(device.taps[-1][1] - 360) <= 2
            ready()
            page.locator('#play-here').click()
            playwright.expect(image).to_have_css('cursor', 'default')
            assert controller.status()['queue_paused']
            assert not errors
            browser.close()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        controller.close()
        thread.join(timeout=5)
