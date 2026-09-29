#!/usr/bin/env python3
"""Live dashboard for kalshi-lp runs. Standard library only.

    python dashboard/server.py                 # http://127.0.0.1:8050
    python dashboard/server.py --runs runs --port 8050

Serves two things:

* The journal API below. It reads the run journals the bot writes
  (``runs/<run_id>/events.jsonl`` and ``state.json``).
* The built React app from ``dashboard/dist`` (``npm run build``). In
  development, run ``npm run dev`` instead: Vite serves the app with hot
  reload and proxies ``/api`` here.

Binds to localhost only: the journal shows your positions and orders.

API
    GET /api/runs                           runs, newest first
    GET /api/runs/<id>/state                latest snapshot
    GET /api/runs/<id>/events?offset=N      events appended since byte offset N
"""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_CHUNK = 2_000_000  # bytes of events per response
RUNNING_WITHIN = 5.0  # seconds since last state write to count as running


NOT_BUILT = b"""<!doctype html><title>KLP Terminal</title>
<body style="background:#000;color:#ffa200;font:14px monospace;padding:20px">
<p>The dashboard app isn't built yet. Run:</p>
<pre>cd dashboard &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>or use <code>make dashboard</code>, or <code>npm run dev</code> for hot reload.</p></body>"""


class Handler(BaseHTTPRequestHandler):
    runs_dir: Path = Path("runs")
    static_dir: Path = HERE / "dist"

    def log_message(self, fmt: str, *args: object) -> None:  # keep the terminal quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: object, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _run_dir(self, run_id: str) -> Path | None:
        if not RUN_ID.match(run_id):
            return None
        path = self.runs_dir / run_id
        return path if path.is_dir() else None

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        if parts[:1] != ["api"]:
            self.send_static(parts)
        elif parts == ["api", "runs"]:
            self._json(self.list_runs())
        elif len(parts) == 4 and parts[:2] == ["api", "runs"]:
            run = self._run_dir(parts[2])
            if run is None:
                self._json({"error": "unknown run"}, HTTPStatus.NOT_FOUND)
            elif parts[3] == "state":
                self.send_state(run)
            elif parts[3] == "events":
                offset = int(parse_qs(url.query).get("offset", ["0"])[0])
                self.send_events(run, offset)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def send_static(self, parts: list[str]) -> None:
        root = self.static_dir.resolve()
        index = root / "index.html"
        if not index.exists():
            self._send(HTTPStatus.OK, NOT_BUILT, "text/html; charset=utf-8")
            return
        path = (root / "/".join(parts)).resolve() if parts else index
        if not path.is_relative_to(root):  # no escaping the build directory
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not path.is_file():
            path = index  # single-page app: unknown paths get the app
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)

    def list_runs(self) -> list[dict[str, object]]:
        runs = []
        if self.runs_dir.is_dir():
            for path in sorted(self.runs_dir.iterdir(), reverse=True):
                if not path.is_dir():
                    continue
                state = path / "state.json"
                updated = state.stat().st_mtime if state.exists() else path.stat().st_mtime
                status = "unknown"
                with contextlib.suppress(OSError, ValueError):
                    status = json.loads(state.read_text()).get("status", "unknown")
                if status == "running" and time.time() - updated > RUNNING_WITHIN:
                    status = "stale"
                runs.append({"id": path.name, "updated_at": updated, "status": status})
        return runs

    def send_state(self, run: Path) -> None:
        try:
            self._send(HTTPStatus.OK, (run / "state.json").read_bytes(), "application/json")
        except FileNotFoundError:
            self._json({"status": "starting"})

    def send_events(self, run: Path, offset: int) -> None:
        path = run / "events.jsonl"
        if not path.exists():
            self._json({"events": [], "offset": 0})
            return
        with path.open("rb") as f:
            f.seek(max(offset, 0))
            chunk = f.read(MAX_CHUNK)
        end = chunk.rfind(b"\n") + 1  # only complete lines; the rest comes next poll
        events = []
        for line in chunk[:end].splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        self._json({"events": events, "offset": max(offset, 0) + end})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", default="runs", help="run journal directory (default: runs)")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()
    Handler.runs_dir = Path(args.runs).resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"kalshi-lp dashboard: http://127.0.0.1:{args.port}  (runs: {Handler.runs_dir})")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
