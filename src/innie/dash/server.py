from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .health import health_summary
from .logs import LocalFileLogSource
from .store import SqliteInnieStore


class InnieDashServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, *, workspace: Path, web_dir: Path) -> None:
        super().__init__(server_address, handler_class)
        self.workspace = workspace.resolve()
        self.web_dir = web_dir.resolve()
        self.db_path = self.workspace / ".innie" / "innie.db"
        self.log_path = self.workspace / ".innie" / "logs" / "innie.log"
        self.store = SqliteInnieStore(self.db_path)
        self.logs = LocalFileLogSource(self.log_path)

    def serve_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread


class InnieDashHandler(BaseHTTPRequestHandler):
    server: InnieDashServer

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api(parsed.path, parse_qs(parsed.query))
            else:
                self._handle_static(parsed.path)
        except KeyError as exc:
            self._json({"error": f"not found: {exc.args[0]}"}, status=404)
        except Exception as exc:  # pragma: no cover - exercised through manual debugging.
            self._json({"error": str(exc)}, status=500)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _handle_api(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/overview":
            self._json(self.server.store.get_overview())
            return
        if path == "/api/sessions":
            self._json(
                self.server.store.list_sessions(
                    status=_one(query, "status"),
                    harness=_one(query, "harness"),
                    search=_one(query, "search"),
                    updated_after=_one(query, "updated_after"),
                    limit=_int(query, "limit", 100),
                )
            )
            return
        if path.startswith("/api/sessions/") and path.endswith("/events"):
            session_id = unquote(path.removeprefix("/api/sessions/").removesuffix("/events").strip("/"))
            self._json(
                self.server.store.list_session_events(
                    session_id,
                    after_id=_int(query, "after_id", 0),
                    limit=_int(query, "limit", 250),
                )
            )
            return
        if path.startswith("/api/sessions/"):
            session_id = unquote(path.removeprefix("/api/sessions/").strip("/"))
            self._json(self.server.store.get_session_detail(session_id))
            return
        if path == "/api/events":
            self._json(self.server.store.list_events(after_id=_int(query, "after_id", 0), limit=_int(query, "limit", 250)))
            return
        if path == "/api/logs":
            self._json(self.server.logs.tail(after_offset=_int(query, "after_offset", 0), limit_bytes=_int(query, "limit_bytes", 65536)))
            return
        if path == "/api/health":
            self._json(health_summary(self.server.workspace, self.server.db_path, self.server.log_path))
            return
        if path == "/api/config":
            self._json(
                {
                    "workspace": str(self.server.workspace),
                    "dbPath": str(self.server.db_path),
                    "logPath": str(self.server.log_path),
                    "refreshMs": 3000,
                }
            )
            return
        self._json({"error": f"unknown endpoint: {path}"}, status=404)

    def _handle_static(self, path: str) -> None:
        requested = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (self.server.web_dir / requested).resolve()
        if not str(candidate).startswith(str(self.server.web_dir)) or not candidate.exists() or candidate.is_dir():
            candidate = self.server.web_dir / "index.html"
        if not candidate.exists():
            self._json({"error": "web/index.html not found"}, status=404)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str, port: int, workspace: Path, *, web_dir: Path | None = None) -> InnieDashServer:
    root = Path(__file__).resolve().parent
    return InnieDashServer(
        (host, port),
        InnieDashHandler,
        workspace=workspace,
        web_dir=web_dir or root / "web",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Innie Dash web app.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Innie workspace containing .innie/innie.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = create_server(args.host, args.port, args.workspace)
    print(f"Innie Dash listening on http://{args.host}:{args.port}")
    print(f"workspace: {server.workspace}")
    server.serve_forever()


def _one(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[0] if values and values[0] else None


def _int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(_one(query, name) or default)
    except ValueError:
        return default


if __name__ == "__main__":
    main()
