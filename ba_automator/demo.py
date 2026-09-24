"""Read-only portfolio demo: ``python3 -m ba_automator.demo --port 8766``.

Uses only the Python standard library and the shipped web UI/home map. All run
records are fictional, all images are original schematics, and every mutation is
rejected. This module deliberately imports no device, config, or runner code.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit


_ROOT = Path(__file__).resolve().parent
_CONTENT_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
_READ_ONLY = {"error": "Read-only demo: no device is connected and no changes are allowed."}


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _schematic(home_map: dict, *, popup: bool = False) -> bytes:
    """Draw original geometry; never load or reproduce a game screenshot."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">',
        '<rect width="1280" height="720" fill="#ecf4fc"/>',
        '<path d="M0 360H1280M640 0V720" stroke="#dce8f5" stroke-width="2"/>',
        '<rect x="218" y="110" width="836" height="432" rx="28" fill="#f8fbff" stroke="#d4e2f0" stroke-width="2"/>',
        '<circle cx="640" cy="256" r="61" fill="#d8eafa"/>',
        '<path d="M609 268 640 217l31 51M623 248h34" fill="none" stroke="#4d7aab" stroke-width="7" stroke-linecap="round"/>',
        '<g font-family="system-ui, sans-serif" text-anchor="middle">',
        '<text x="640" y="368" fill="#375a7b" font-size="32" font-weight="600">home navigation, mapped.</text>',
        '<text x="640" y="407" fill="#5b7693" font-size="19">original schematic · sample data · no device</text>',
        '<text x="640" y="470" fill="#6484a5" font-size="17">toggle the home button map to inspect a target</text>',
    ]
    for button in home_map["buttons"]:
        x1, y1, x2, y2 = button["bounds"]
        label = "Event" if button["kind"] == "carousel" else button["label"]
        width, height = x2 - x1, y2 - y1
        font_size = 12 if width < 100 else 15
        parts.append(
            f'<rect x="{x1}" y="{y1}" width="{width}" height="{height}" rx="10" fill="#e0ecf8" stroke="#b8cfe7"/>'
            f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 + 5}" fill="#355675" font-size="{font_size}">{escape(label)}</text>'
        )
    parts.append('</g>')
    if popup:
        parts.extend([
            '<rect width="1280" height="720" fill="#1a2b43" opacity=".5"/>',
            '<rect x="320" y="165" width="640" height="350" rx="20" fill="#fff"/>',
            '<g font-family="system-ui, sans-serif" text-anchor="middle" fill="#355675">',
            '<text x="640" y="280" font-size="32">sample announcement</text>',
            '<text x="640" y="327" font-size="19">fictional popup, original artwork</text>',
            '<text x="640" y="405" font-size="17">the log keeps the before and after frames.</text>',
            '<text x="919" y="214" font-size="32">×</text></g>',
        ])
    parts.append('</svg>')
    return "".join(parts).encode("utf-8")


def _routes(now: datetime) -> dict[str, tuple[str, bytes]]:
    """Load a fixed allowlist of shipped files, never user config or run data."""
    home_map = json.loads((_ROOT / "assets" / "home_map.json").read_text(encoding="utf-8"))

    def stamp(seconds: int) -> str:
        return (now + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

    status = {
        "demo": True,
        "sample_data": True,
        "state": "success",
        "task": "cafe",
        "phase": "sample run completed. this demo doesn’t control a device.",
        "started_at": stamp(-300),
        "completed_at": stamp(-60),
        "duration_seconds": 240,
        "app_closed": True,
        "has_frame": True,
        "frame_version": "demo-schematic-v1",
        "current_job": None,
        "queue_paused": True,
        "queue": [
            {"id": "sample-next-restart", "task": "restart", "created_at": stamp(-40)},
            {"id": "sample-next-cafe", "task": "cafe", "created_at": stamp(-35)},
        ],
        "history": [
            {"id": "sample-cafe", "task": "cafe", "state": "success", "completed_at": stamp(-60)},
            {"id": "sample-restart", "task": "restart", "state": "success", "completed_at": stamp(-300)},
        ],
        "config": {
            "serial": "no device · demo",
            "auto_download": True,
            "poll_interval": 1.5,
            "startup_timeout": 300,
            "download_timeout": 1800,
            "unknown_timeout": 60,
            "close_app_when_idle": True,
            "cafe_schedule_enabled": False,
            "cafe_invite_enabled": False,
            "cafe_invite_student": "",
        },
        "schedule": {"cafe": {"enabled": False, "next_due_at": None, "retry_paused": False}},
        "logs": [
            {"time": stamp(-360), "message": "Sample: restart started; explicit device target checked."},
            {"time": stamp(-330), "message": "Sample: announcement recognized; before and after frames saved."},
            {"time": stamp(-300), "message": "Sample: stable home verified; next job may start."},
            {"time": stamp(-240), "message": "Sample: Cafe earnings receipt verified."},
            {"time": stamp(-120), "message": "Sample: both floors scanned; relationship feedback verified."},
            {"time": stamp(-60), "message": "Sample: returned home; queue finished; game closed."},
        ],
    }
    actions = {
        "actions": [
            {"id": "sample-closed", "time": stamp(-60), "task": "system", "action": "app_closed", "detail": "Sample: closed the game after the queue finished."},
            {"id": "sample-relationship", "time": stamp(-120), "task": "cafe", "action": "relationship_increased", "detail": "Sample: a new heart was detected after the tap.", "cafe": "2"},
            {"id": "sample-earnings", "time": stamp(-240), "task": "cafe", "action": "earnings_collected", "detail": "Sample: receipt confirmed 60 AP and 45,000 credits.", "cafe": "1"},
        ]
    }
    popups = {
        "popups": [{
            "id": "sample-announcement",
            "job_id": "sample-restart",
            "time": stamp(-330),
            "detail": "Sample: shaded overlay and a single close control recognized.",
            "detector": "sample / shaded_overlay",
            "result": "changed",
            "after_state": "home (sample)",
            "before_url": "/api/popups/sample-announcement/before",
            "after_url": "/api/popups/sample-announcement/after",
        }]
    }
    page = (_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    # Visible before JavaScript runs, including when scripts are disabled.
    page = page.replace('id="demo-banner" class="demo-banner" role="note" hidden', 'id="demo-banner" class="demo-banner" role="note"')
    page = page.replace('<title>Blue Archive Automator</title>', '<title>Demo · Blue Archive Automator</title>')
    routes = {
        "/": ("text/html; charset=utf-8", page.encode("utf-8")),
        "/app.js": ("text/javascript; charset=utf-8", (_ROOT / "web" / "app.js").read_bytes()),
        "/style.css": ("text/css; charset=utf-8", (_ROOT / "web" / "style.css").read_bytes()),
        "/api/frame": ("image/svg+xml; charset=utf-8", _schematic(home_map)),
        "/api/popups/sample-announcement/before": ("image/svg+xml; charset=utf-8", _schematic(home_map, popup=True)),
        "/api/popups/sample-announcement/after": ("image/svg+xml; charset=utf-8", _schematic(home_map)),
    }
    for path, value in (("/api/status", status), ("/api/map", home_map), ("/api/actions", actions), ("/api/popups", popups)):
        routes[path] = ("application/json; charset=utf-8", _json(value))
    return routes


def make_server(port: int = 8766) -> ThreadingHTTPServer:
    """Construct a loopback server; port 0 is available for isolated tests."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    if port == 8765:
        raise ValueError("port 8765 is reserved for the live dashboard; use 8766 for the demo")
    routes = _routes(datetime.now(timezone.utc))

    class Handler(BaseHTTPRequestHandler):
        server_version = "BlueArchiveDemo/1.0"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def _reply(self, code: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", _CONTENT_POLICY)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            self.close_connection = True

        def _valid_host(self) -> bool:
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            hosts = self.headers.get_all("Host", [])
            return len(hosts) == 1 and hosts[0] in allowed

        def do_GET(self) -> None:
            if not self._valid_host():
                self._reply(403, _json({"error": "Demo requests must use its loopback host and port."}))
                return
            try:
                path = urlsplit(self.path).path
            except ValueError:
                self._reply(400, _json({"error": "Invalid request path."}))
                return
            route = routes.get(path)
            if route is None:
                self._reply(404, _json({"error": "Not found in the read-only demo."}))
                return
            content_type, body = route
            self._reply(200, body, content_type)

        do_HEAD = do_GET

        def do_POST(self) -> None:
            self._reply(405, _json(_READ_ONLY))

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST
        do_OPTIONS = do_POST

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be a number") from exc
    if not 1024 <= port <= 65535 or port == 8765:
        raise argparse.ArgumentTypeError("choose port 1024–65535, except 8765 (the live dashboard)")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only dashboard demo with sample data; no emulator or dependencies needed.")
    parser.add_argument("--port", type=_port, default=8766, help="loopback port (default: 8766; live port 8765 is reserved)")
    args = parser.parse_args(argv)
    try:
        server = make_server(args.port)
    except OSError as exc:
        parser.exit(1, f"Cannot start demo: {exc}\n")
    print(f"Read-only demo: http://127.0.0.1:{server.server_port}/ · sample data · no device", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
