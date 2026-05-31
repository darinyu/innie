from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from innie.slack_client import SlackApiError, SlackWebClient


def run_server(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class SlackWebClientTest(unittest.TestCase):
    def test_post_direct_message_uses_user_id_and_returns_dm_channel(self) -> None:
        calls: list[tuple[str, dict]] = []

        class FakeClient(SlackWebClient):
            def api_call(self, method: str, payload: dict) -> dict:
                calls.append((method, payload))
                if method == "conversations.open":
                    return {"ok": True, "channel": {"id": "D123"}}
                return {"ok": True, "channel": "D123", "ts": "171.2"}

        result = FakeClient("xoxb-test-token").post_direct_message(
            user="U123",
            text="handoff",
            unfurl_links=False,
            unfurl_media=False,
        )

        self.assertEqual("D123", result.channel)
        self.assertEqual("171.2", result.ts)
        self.assertEqual(
            [
                (
                    "conversations.open",
                    {"users": "U123"},
                ),
                (
                    "chat.postMessage",
                    {"channel": "D123", "text": "handoff", "unfurl_links": False, "unfurl_media": False},
                )
            ],
            calls,
        )

    def test_slack_api_error_includes_missing_scope_details(self) -> None:
        class FakeClient(SlackWebClient):
            def api_call(self, method: str, payload: dict) -> dict:
                return {
                    "ok": False,
                    "error": "missing_scope",
                    "needed": "im:write",
                    "provided": "chat:write",
                }

        with self.assertRaisesRegex(
            SlackApiError,
            r"chat.postMessage failed: missing_scope \(needed=im:write, provided=chat:write\)",
        ):
            FakeClient("xoxb-test-token").post_message(channel="U123", thread_ts=None, text="handoff")

    def test_workspace_url_uses_auth_test_once(self) -> None:
        calls: list[tuple[str, dict]] = []

        class FakeClient(SlackWebClient):
            def api_call(self, method: str, payload: dict) -> dict:
                calls.append((method, payload))
                return {"ok": True, "url": "https://paofuanddddd.slack.com/"}

        client = FakeClient("xoxb-test-token")

        self.assertEqual("https://paofuanddddd.slack.com/", client.workspace_url())
        self.assertEqual("https://paofuanddddd.slack.com/", client.workspace_url())
        self.assertEqual([("auth.test", {})], calls)

    def test_download_file_keeps_authorization_across_redirects(self) -> None:
        token = "xoxb-test-token"
        actual_body = b"actual file contents\n"
        seen_authorization: list[str | None] = []

        class FileHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen_authorization.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                if self.headers.get("Authorization") == f"Bearer {token}":
                    self.wfile.write(actual_body)
                else:
                    self.wfile.write(b"<html><title>Slack</title></html>")

            def log_message(self, format: str, *args: object) -> None:
                pass

        file_server, file_thread = run_server(FileHandler)
        file_url = f"http://127.0.0.1:{file_server.server_port}/files-pri/download/test.txt"

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header("Location", file_url)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        redirect_server, redirect_thread = run_server(RedirectHandler)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "test.txt"

                result = SlackWebClient(token).download_file(
                    f"http://127.0.0.1:{redirect_server.server_port}/download/test.txt",
                    destination,
                )

                self.assertIsNone(result.error)
                self.assertEqual(len(actual_body), result.byte_count)
                self.assertEqual(actual_body, destination.read_bytes())
                self.assertEqual([f"Bearer {token}"], seen_authorization)
        finally:
            redirect_server.shutdown()
            file_server.shutdown()
            redirect_thread.join(timeout=2)
            file_thread.join(timeout=2)
            redirect_server.server_close()
            file_server.server_close()

    def test_download_file_rejects_slack_login_html(self) -> None:
        class LoginHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b'<!DOCTYPE html><html><head><title>Slack</title></head><body data-props="'
                    b"{&quot;loggedInTeams&quot;:[],&quot;redirectURL&quot;:&quot;\\/files-pri\\/T-F\\/download\\/test.txt&quot;}"
                    b'"></body></html>'
                )

            def log_message(self, format: str, *args: object) -> None:
                pass

        server, thread = run_server(LoginHandler)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "test.txt"

                result = SlackWebClient("xoxb-test-token").download_file(
                    f"http://127.0.0.1:{server.server_port}/download/test.txt",
                    destination,
                )

                self.assertEqual("slack_login_redirect", result.error)
                self.assertFalse(destination.exists())
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_download_file_reports_missing_files_read_scope(self) -> None:
        class FakeResponse:
            status = 200
            headers = {}

            def __init__(self, body: bytes) -> None:
                self._body = io.BytesIO(body)

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self) -> bytes:
                return self._body.read()

        login_body = (
            b'<!DOCTYPE html><html><head><title>Slack</title></head><body data-props="'
            b"{&quot;loggedInTeams&quot;:[],&quot;redirectURL&quot;:&quot;\\/files-pri\\/T-F123ABC\\/download\\/test.txt&quot;}"
            b'"></body></html>'
        )
        info_body = json.dumps({"ok": False, "error": "missing_scope", "needed": "files:read"}).encode("utf-8")

        def fake_urlopen(req: object, timeout: int) -> FakeResponse:
            full_url = req.full_url
            if full_url == "https://slack.com/api/files.info":
                return FakeResponse(info_body)
            return FakeResponse(login_body)

        with tempfile.TemporaryDirectory() as tmp, mock.patch("innie.slack_client.request.urlopen", side_effect=fake_urlopen):
            destination = Path(tmp) / "test.txt"

            result = SlackWebClient("xoxb-test-token").download_file(
                "https://files.slack.com/files-pri/T-F123ABC/download/test.txt",
                destination,
            )

            self.assertEqual("missing_scope: files:read", result.error)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
