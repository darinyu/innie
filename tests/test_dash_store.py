import sqlite3
import tempfile
import unittest
from pathlib import Path

from innie.dash.store import SqliteInnieStore
from tests.dash_fixtures import create_sample_db


class SqliteInnieStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "innie.db"
        create_sample_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_overview_counts_current_state(self) -> None:
        store = SqliteInnieStore(self.db_path)

        overview = store.get_overview()

        self.assertEqual(overview["counts"]["sessions"], 3)
        self.assertEqual(overview["counts"]["running_sessions"], 1)
        self.assertEqual(overview["counts"]["queued_inputs"], 2)
        self.assertEqual(overview["counts"]["failed_tasks"], 1)
        self.assertEqual(overview["counts"]["locked_sessions"], 1)
        self.assertEqual(overview["latest_event_id"], 4)

    def test_list_sessions_supports_status_filter_and_search(self) -> None:
        store = SqliteInnieStore(self.db_path)

        rows = store.list_sessions(status="running", search="checkout")["items"]

        self.assertEqual([row["id"] for row in rows], ["sess_running"])
        self.assertEqual(rows[0]["latest_task_goal"], "debug checkout flow")
        self.assertEqual(rows[0]["latest_user_message"], "also inspect tests")
        self.assertEqual(rows[0]["queued_inputs"], 2)
        self.assertEqual(rows[0]["last_event_type"], "harness.progress")

    def test_list_sessions_supports_queued_inbox_filter(self) -> None:
        store = SqliteInnieStore(self.db_path)

        rows = store.list_sessions(status="queued")["items"]

        self.assertEqual([row["id"] for row in rows], ["sess_running"])
        self.assertEqual(rows[0]["queued_inputs"], 2)

    def test_list_sessions_supports_failed_task_filter(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sess_idle_failed_task",
                "C3",
                "4",
                "4",
                "slack",
                "slack",
                "idle",
                "codex",
                "2026-01-01T00:06:00Z",
                "2026-01-01T00:07:00Z",
                None,
                None,
                None,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "task_idle_failed",
                "sess_idle_failed_task",
                "failed",
                "debug failed turn",
                "slack",
                "codex",
                "autonomous",
                "2026-01-01T00:06:00Z",
                "2026-01-01T00:07:00Z",
                "2026-01-01T00:07:00Z",
            ),
        )
        conn.commit()
        conn.close()
        store = SqliteInnieStore(self.db_path)

        rows = store.list_sessions(status="failed")["items"]

        self.assertEqual([row["id"] for row in rows], ["sess_idle_failed_task", "sess_failed"])

    def test_session_detail_includes_related_rows(self) -> None:
        store = SqliteInnieStore(self.db_path)

        detail = store.get_session_detail("sess_running")

        self.assertEqual(detail["session"]["id"], "sess_running")
        self.assertEqual(len(detail["tasks"]), 1)
        self.assertEqual(len(detail["inbox"]), 2)
        self.assertEqual(len(detail["task_events"]), 2)
        self.assertEqual(len(detail["hook_events"]), 1)

    def test_event_cursors_return_next_cursor(self) -> None:
        store = SqliteInnieStore(self.db_path)

        page = store.list_events(after_id=2, limit=10)

        self.assertEqual([row["id"] for row in page["items"]], [3, 4])
        self.assertEqual(page["next"]["lastEventId"], 4)

    def test_payloads_redact_sensitive_values(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO task_events(id, session_id, task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                5,
                "sess_running",
                "task_running",
                "harness.progress",
                '{"token":"secret-token","nested":{"access_token":"secret-access","safe":"visible"}}',
                "2026-01-01T00:06:00Z",
            ),
        )
        conn.commit()
        conn.close()
        store = SqliteInnieStore(self.db_path)

        event = store.list_events(after_id=4)["items"][0]

        self.assertEqual(event["payload"]["token"], "[redacted]")
        self.assertEqual(event["payload"]["nested"]["access_token"], "[redacted]")
        self.assertEqual(event["payload"]["nested"]["safe"], "visible")
        self.assertNotIn("secret-token", event["payload_json"])

if __name__ == "__main__":
    unittest.main()
