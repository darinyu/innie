import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from innie.dash.server import create_server
from tests.dash_fixtures import create_sample_db


class ApiServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        (self.workspace / ".innie").mkdir()
        self.db_path = self.workspace / ".innie" / "innie.db"
        create_sample_db(self.db_path)
        log_dir = self.workspace / ".innie" / "logs"
        log_dir.mkdir()
        (log_dir / "innie.log").write_text("first log\nsecond log\n", encoding="utf-8")
        self.server = create_server("127.0.0.1", 0, self.workspace)
        self.port = self.server.server_address[1]
        self.thread = self.server.serve_in_thread()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def test_json_endpoints(self) -> None:
        overview = self._json("/api/overview")
        sessions = self._json("/api/sessions?status=running&search=checkout")
        detail = self._json("/api/sessions/sess_running")
        events = self._json("/api/events?after_id=2")
        logs = self._json("/api/logs?after_offset=0")
        health = self._json("/api/health")

        self.assertEqual(overview["counts"]["sessions"], 3)
        self.assertEqual([row["id"] for row in sessions["items"]], ["sess_running"])
        self.assertEqual(detail["session"]["id"], "sess_running")
        self.assertEqual([row["id"] for row in events["items"]], [3, 4])
        self.assertEqual(logs["lines"], ["first log", "second log"])
        self.assertTrue(health["store"]["exists"])

    def test_missing_session_returns_404(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self._json("/api/sessions/missing")

        self.assertEqual(raised.exception.code, 404)

    def _json(self, path: str) -> dict:
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
