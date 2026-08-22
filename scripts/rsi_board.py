#!/usr/bin/env python3
"""TensorBoard for RSI: point it at a runs/ directory, get a local web board.

Same shape as TensorBoard: a local HTTP server on the stdlib, auto-discovering
every campaign store under ``--runs``, live-updating as a campaign writes new
artifacts, rendering comparable scalar timelines. It only ever READS runs/ --
sealed evidence -- through the pure parse layer in ``board/store.py``.

    python scripts/rsi_board.py --runs runs/ --port 6006

Opens http://127.0.0.1:6006 in the browser. Ctrl-C to stop.

The server is a thin stdlib http.server over the parse layer; every endpoint is
one call into board.store, so all the logic worth testing lives there and is
unit-tested against fake stores (tests/test_rsi_board.py) with no server.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board import report as br
from board import store as bs

_HTML = Path(__file__).resolve().parent.parent / "board" / "index.html"


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _head_sha(repo: Path) -> str:
    """git HEAD sha for the footer; empty string if git is unavailable (report still builds)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _build_report(runs: Path, status_path: Path, progress_path: Path,
                  live_threshold_s: float) -> str:
    """Assemble the full self-contained report HTML from the sealed runs/ tree."""
    return br.build_report(
        runs, status_text=_read_text(status_path), progress_text=_read_text(progress_path),
        head_sha=_head_sha(runs.parent), live_threshold_s=live_threshold_s)


class Handler(BaseHTTPRequestHandler):
    # Set on the class before serve() (see main).
    runs_dir: Path
    status_path: Path
    progress_path: Path
    live_threshold: float = 3600.0

    def log_message(self, *args) -> None:
        pass  # quiet by default

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(json.dumps(obj, default=str).encode(), "application/json", code)

    def _safe_store(self, name: str) -> Path | None:
        """Resolve a store name to a direct child of runs_dir, rejecting traversal."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        path = (self.runs_dir / name).resolve()
        if path.parent != self.runs_dir.resolve() or not bs.is_store(path):
            return None
        return path

    def do_GET(self) -> None:  # http.server dispatch API
        parsed = urlparse(self.path)
        route = parsed.path
        qs = parse_qs(parsed.query)
        name = (qs.get("name") or [""])[0]
        try:
            if route in ("/", "/index.html"):
                self._send(_read_text(_HTML).encode(), "text/html; charset=utf-8")
            elif route == "/api/report":
                html = _build_report(self.runs_dir, self.status_path, self.progress_path,
                                     self.live_threshold)
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="rsi-report-{time.strftime("%Y-%m-%d")}.html"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            elif route == "/api/stores":
                self._json({"runs_dir": str(self.runs_dir),
                            "stores": bs.list_stores(self.runs_dir)})
            elif route == "/api/store":
                path = self._safe_store(name)
                self._json(bs.store_detail(path)) if path else self._json(
                    {"error": "unknown store"}, 404)
            elif route == "/api/heldout":
                path = self._safe_store(name)
                self._json(bs.heldout_blocks(self.runs_dir, name)) if path else self._json(
                    {"error": "unknown store"}, 404)
            elif route == "/api/ledger":
                self._json({"ranges": bs.parse_ledger(_read_text(self.status_path))})
            elif route == "/api/rounds":
                self._json({"rounds": bs.parse_rounds(_read_text(self.progress_path))})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001 - a mid-write race must not kill the server
            self._json({"error": str(exc)}, 500)

    do_HEAD = do_GET


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runs", type=Path, default=Path("runs"),
                        help="campaign runs directory (default: runs)")
    parser.add_argument("--port", type=int, default=6006, help="listen port (default: 6006)")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    parser.add_argument("--status", type=Path, default=None,
                        help="STATUS.md for the seed ledger (default: <runs>/../STATUS.md)")
    parser.add_argument("--progress", type=Path, default=None,
                        help="progress.md for the rounds feed (default: <runs>/../progress.md)")
    parser.add_argument("--report", type=Path, default=None,
                        help="write the self-contained HTML report to this path and exit "
                             "(headless, for cron/scripted use)")
    parser.add_argument("--live-threshold", type=float, default=3600.0,
                        help="seconds since last write to flag a store 'in progress' (default: 3600)")
    parser.add_argument("--no-browser", action="store_true", help="do not auto-open a browser")
    args = parser.parse_args(argv)

    runs = args.runs.resolve()
    if not runs.is_dir():
        parser.error(f"runs directory not found: {runs}")

    Handler.runs_dir = runs
    Handler.status_path = (args.status or runs.parent / "STATUS.md").resolve()
    Handler.progress_path = (args.progress or runs.parent / "progress.md").resolve()
    Handler.live_threshold = args.live_threshold

    if args.report is not None:
        html = _build_report(runs, Handler.status_path, Handler.progress_path, args.live_threshold)
        args.report.write_text(html)
        print(f"wrote report: {args.report.resolve()} ({len(html)} bytes)")
        return 0

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"RSI board on {url}  (runs: {runs})\nCtrl-C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
