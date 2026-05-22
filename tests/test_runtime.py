from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.inbox import enqueue_trigger
from innie.runtime import SessionManager
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger


def make_trigger(event_id: str, channel: str = "D1", ts: str = "100.1") -> SlackTrigger:
    return SlackTrigger(
        event_id=event_id,
        trigger_type="dm",
        channel_id=channel,
        message_ts=ts,
        thread_ts=None,
        sender_user_id="U1",
        text=f"text {event_id}",
        payload={"event_id": event_id},
    )


class RuntimeTest(unittest.TestCase):
    def test_manager_processes_sessions_concurrently_until_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            for item in (make_trigger("Ev1", "D1", "100.1"), make_trigger("Ev2", "D2", "200.1")):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item)
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            manager = SessionManager(path)
            try:
                asyncio.run(manager.run_until_idle())
                statuses = [row["status"] for row in manager.db.execute("SELECT status FROM sessions ORDER BY id")]
                events = [
                    row["event_type"]
                    for row in manager.db.execute("SELECT event_type FROM task_events WHERE session_id IS NOT NULL")
                ]
            finally:
                manager.close()

            self.assertEqual(["idle", "idle"], statuses)
            self.assertEqual(2, events.count("harness.placeholder.output"))

    def test_manager_rehydrates_running_session_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item)
            enqueue_trigger(db, session=session, trigger=item)
            db.execute("UPDATE sessions SET status = 'running' WHERE id = ?", (session.id,))
            db.commit()
            db.close()

            manager = SessionManager(path)
            try:
                self.assertEqual([session.id], manager.hydrate())
                asyncio.run(manager.run_until_idle())
                row = manager.db.execute("SELECT status FROM sessions WHERE id = ?", (session.id,)).fetchone()
            finally:
                manager.close()

            self.assertEqual("idle", row["status"])


if __name__ == "__main__":
    unittest.main()
