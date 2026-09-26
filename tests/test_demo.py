from __future__ import annotations

import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

import pytest

from ba_automator import demo
from ba_automator.demo import make_server


@pytest.fixture
def demo_server():
    server = make_server(0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def request(server, path="/api/status", method="GET", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_demo_runs_with_stdlib_only_and_no_device_config_or_process_access(tmp_path):
    """Isolated Python disables site packages; audit hooks reject side effects."""
    root = Path(__file__).resolve().parents[1]
    script = r'''
import json
import os
import sys
sys.path.insert(0, sys.argv[1])
forbidden = {"numpy", "cv2", "rapidocr", "onnxruntime", "ba_automator.adb", "ba_automator.config", "ba_automator.server", "ba_automator.cli"}
def audit(event, args):
    if event == "import" and any(args[0] == name or args[0].startswith(name + ".") for name in forbidden):
        raise AssertionError("device/runtime dependency imported: " + args[0])
    if event in {"subprocess.Popen", "os.system", "socket.connect", "socket.gethostbyaddr", "socket.getaddrinfo", "socket.gethostbyname"}:
        raise AssertionError("external interaction: " + event)
    if event == "open":
        path, mode, flags = args
        if isinstance(mode, str) and any(flag in mode for flag in "wax+"):
            raise AssertionError("demo wrote a file")
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            raise AssertionError("demo opened a file for writing")
        if isinstance(path, str) and os.path.abspath(path).startswith(os.getcwd() + os.sep):
            raise AssertionError("demo read machine-local data")
sys.dont_write_bytecode = True
sys.addaudithook(audit)
from ba_automator.demo import make_server
server = make_server(0)
assert server.server_address[0] == "127.0.0.1"
server.server_close()
assert {name for name in sys.modules if name.startswith("ba_automator.")} == {"ba_automator.demo"}
print(json.dumps({"stdlib_only": True, "no_external_interaction": True}))
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == {"stdlib_only": True, "no_external_interaction": True}
    assert not list(tmp_path.iterdir())


def test_demo_bind_does_not_resolve_any_hostname(monkeypatch):
    def reject_lookup(*args, **kwargs):
        raise AssertionError("loopback demo startup must not resolve hostnames")

    for name in ("getfqdn", "gethostbyaddr", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, reject_lookup)
    server = make_server(0)
    try:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1]
        assert server.server_port > 0
    finally:
        server.server_close()


def test_status_is_explicitly_fictional_and_cannot_schedule_jobs(demo_server):
    assert demo_server.server_address[0] == "127.0.0.1"
    code, headers, body = request(demo_server)
    assert code == 200
    status = json.loads(body)
    assert status["demo"] is True
    assert status["sample_data"] is True
    assert status["current_job"] is None
    assert status["queue_paused"] is True
    assert status["config"]["serial"] == "no device · demo"
    assert status["config"]["lessons_strategy"] == "relationship"
    assert status["config"]["lessons_max_tickets"] == 0
    assert status["config"]["lessons_locations"] == []
    assert status["config"]["lessons_enabled_in_daily"] is True
    assert status["config"]["total_assault_difficulty"] == "hardcore"
    assert status["config"]["total_assault_comfort_seconds"] == 30
    assert status["config"]["total_assault_enabled_in_daily"] is False
    assert not status["schedule"]["cafe"]["enabled"]
    assert status["config"]["checkin_schedule_enabled"] is False
    assert status["config"]["checkin_interval_minutes"] == 30
    assert status["schedule"]["checkin"]["enabled"] is False
    assert status["schedule"]["checkin"]["next_due_at"] is None
    assert "csrf_token" not in status
    assert all(job["id"].startswith("sample-") for job in status["queue"] + status["history"])
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("path", [
    "/api/run", "/api/stop", "/api/pause", "/api/resume", "/api/cancel",
    "/api/settings", "/api/capture", "/", "/anything-else",
])
def test_every_post_rejects_and_preserves_the_sample_state(demo_server, path):
    before = request(demo_server)[2]
    actions_before = request(demo_server, "/api/actions")[2]
    code, _, body = request(demo_server, path, "POST", b'{"task":"cafe","serial":"127.0.0.1:5695"}', {"Content-Type": "application/json"})
    assert code == 405
    assert "Read-only demo" in json.loads(body)["error"]
    assert request(demo_server)[2] == before
    assert request(demo_server, "/api/actions")[2] == actions_before


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS"])
def test_other_mutating_methods_are_rejected(demo_server, method):
    assert request(demo_server, "/api/settings", method, b"{}")[0] == 405


@pytest.mark.parametrize("host", ["example.org", "127.0.0.1:8765", "localhost", "evil.localhost:8766"])
def test_demo_only_accepts_its_own_loopback_host(demo_server, host):
    assert request(demo_server, headers={"Host": host})[0] == 403


def test_localhost_alias_works_but_duplicate_host_headers_do_not(demo_server):
    assert request(demo_server, headers={"Host": f"localhost:{demo_server.server_port}"})[0] == 200
    connection = http.client.HTTPConnection("127.0.0.1", demo_server.server_port, timeout=3)
    try:
        connection.putrequest("GET", "/api/status")
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
        response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("path", ["/../config/local.toml", "/%2e%2e/config/local.toml", "/data/state/important-actions.jsonl", "/assets/home_left.png", "/api/popups/real-run/before"])
def test_no_arbitrary_files_or_real_images_are_served(demo_server, path):
    assert request(demo_server, path)[0] == 404


def test_ui_map_history_and_original_popup_schematics_are_self_contained(demo_server):
    code, _, page = request(demo_server, "/")
    assert code == 200
    assert b'demo \xc2\xb7 sample data \xc2\xb7 no device' in page
    assert b'id="demo-banner" class="demo-banner" role="note" hidden' not in page
    assert b'Maid in Schale' in page and b'Blue Archive Automator' in page
    for path, content_type in [("/app.js", "text/javascript"), ("/style.css", "text/css"),
                               ("/maid-arisu.png", "image/png"), ("/maid-arisu-checklist.png", "image/png"),
                               ("/maid-arisu-tea.png", "image/png"), ("/maid-arisu-loot.png", "image/png"),
                               ("/api/map", "application/json")]:
        code, headers, _ = request(demo_server, path)
        assert code == 200
        assert headers["Content-Type"].startswith(content_type)
    for asset in ("maid-arisu.png", "maid-arisu-checklist.png", "maid-arisu-tea.png", "maid-arisu-loot.png"):
        assert request(demo_server, "/" + asset)[2].startswith(b"\x89PNG\r\n\x1a\n")
    home_map = json.loads(request(demo_server, "/api/map")[2])
    assert (home_map["width"], home_map["height"]) == (1280, 720)
    assert home_map["buttons"]
    actions = json.loads(request(demo_server, "/api/actions")[2])["actions"]
    assert actions and all(action["detail"].startswith("Sample:") for action in actions)
    lesson = next(action for action in actions if action["action"] == "lesson_completed")
    assert lesson["id"].startswith("sample-")
    assert lesson["location"] == "Sample Academy"
    assert lesson["tickets_before"] - lesson["tickets_after"] == 1
    popups = json.loads(request(demo_server, "/api/popups")[2])["popups"]
    assert len(popups) == 1
    urls = ["/api/frame?v=demo-schematic-v1", popups[0]["before_url"], popups[0]["after_url"]]
    frames = []
    for url in urls:
        code, headers, image = request(demo_server, url)
        assert code == 200
        assert headers["Content-Type"].startswith("image/svg+xml")
        svg = ET.fromstring(image)
        assert svg.attrib["viewBox"] == "0 0 1280 720"
        assert not svg.findall(".//{http://www.w3.org/2000/svg}image")
        assert not svg.findall(".//{http://www.w3.org/2000/svg}script")
        assert "sample data" in image.decode()
        frames.append(image)
    assert frames[0] == frames[2]
    assert frames[1] != frames[2]


def test_head_exposes_only_the_response_headers(demo_server):
    code, headers, body = request(demo_server, method="HEAD")
    assert code == 200
    assert int(headers["Content-Length"]) > 0
    assert body == b""


def test_daily_log_is_fictional_plain_text_with_a_dated_filename(demo_server):
    code, _, body = request(demo_server, "/api/logs")
    assert code == 200
    index = json.loads(body)
    assert index["dates"] == [index["today"]]
    filename = f'{index["today"]}.log'
    code, headers, today = request(demo_server, "/api/logs/today.log")
    assert code == 200
    assert headers["Content-Type"] == "text/plain; charset=utf-8"
    assert headers["Content-Disposition"] == f'inline; filename="{filename}"'
    assert "fictional sample data" in today.decode()
    assert "[runtime] Sample:" in today.decode()
    assert "[lesson_completed] Sample:" in today.decode()
    assert request(demo_server, f"/api/logs/{filename}")[2] == today
    assert request(demo_server, "/api/logs/2000-01-01.log")[0] == 404
    code, headers, body = request(demo_server, "/api/logs/today.log", method="HEAD")
    assert code == 200 and body == b""
    assert int(headers["Content-Length"]) == len(today)


@pytest.mark.parametrize("port", [True, -1, 65536, 12.5, "8766", 8765])
def test_invalid_or_live_reserved_ports_fail_before_binding(port):
    with pytest.raises(ValueError):
        make_server(port)


@pytest.mark.parametrize("port", ["8765", "0", "1023", "65536", "nope"])
def test_cli_rejects_reserved_and_invalid_ports(port):
    result = subprocess.run([sys.executable, "-S", "-m", "ba_automator.demo", "--port", port], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert "error:" in result.stderr


def test_cli_defaults_to_demo_port_and_closes_on_interrupt(monkeypatch, capsys):
    calls = []

    class Server:
        server_port = 8766

        def serve_forever(self):
            calls.append("serve")
            raise KeyboardInterrupt

        def server_close(self):
            calls.append("close")

    def make(port):
        calls.append(port)
        return Server()

    monkeypatch.setattr(demo, "make_server", make)
    assert demo.main([]) == 0
    assert calls == [8766, "serve", "close"]
    assert "http://127.0.0.1:8766/" in capsys.readouterr().out
