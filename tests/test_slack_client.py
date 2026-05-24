from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from innie.slack_client import SlackApiError, SlackWebClient


def run_server(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class SlackWebClientTest(unittest.TestCase):
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

                byte_count = SlackWebClient(token).download_file(
                    f"http://127.0.0.1:{redirect_server.server_port}/download/test.txt",
                    destination,
                )

                self.assertEqual(len(actual_body), byte_count)
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

                with self.assertRaisesRegex(SlackApiError, "slack_login_redirect"):
                    SlackWebClient("xoxb-test-token").download_file(
                        f"http://127.0.0.1:{server.server_port}/download/test.txt",
                        destination,
                    )

                self.assertFalse(destination.exists())
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
