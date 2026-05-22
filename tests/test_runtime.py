from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.harness import HarnessEvent, ScriptedHarnessAdapter
from innie.inbox import enqueue_trigger
from innie.runtime import SessionManager
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger


class FakeSlackReplies:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        self.messages.append((channel, thread_ts, text))


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
                session = resolve_session_for_trigger(db, item, harness_id="scripted")
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="progress", message="working"),
                    HarnessEvent(type="output", message="done"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
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
            self.assertEqual(2, events.count("harness.output"))
            self.assertIn(("D1", "100.1", "Progress: working"), slack.messages)
            self.assertIn(("D2", "200.1", "Done:\ndone"), slack.messages)

    def test_manager_posts_task_started_even_when_adapter_does_not_emit_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
                task_id = manager.db.execute("SELECT id FROM tasks WHERE session_id = ?", (session.id,)).fetchone()["id"]
                started_count = manager.db.execute(
                    "SELECT COUNT(*) AS count FROM task_events WHERE task_id = ? AND event_type = 'harness.started'",
                    (task_id,),
                ).fetchone()["count"]
            finally:
                manager.close()

            self.assertEqual(1, started_count)
            self.assertIn(("D1", "100.1", f"Started task {task_id}."), slack.messages)

    def test_manager_does_not_duplicate_adapter_started_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
                started_messages = [message for _, _, message in slack.messages if message.startswith("Started task ")]
            finally:
                manager.close()

            self.assertEqual(1, len(started_messages))

    def test_manager_rehydrates_running_session_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.execute("UPDATE sessions SET status = 'running' WHERE id = ?", (session.id,))
            db.commit()
            db.close()

            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="output", message="done"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, workspace=Path(tmp))
            try:
                self.assertEqual([session.id], manager.hydrate())
                asyncio.run(manager.run_until_idle())
                row = manager.db.execute("SELECT status FROM sessions WHERE id = ?", (session.id,)).fetchone()
            finally:
                manager.close()

            self.assertEqual("idle", row["status"])


if __name__ == "__main__":
    unittest.main()
