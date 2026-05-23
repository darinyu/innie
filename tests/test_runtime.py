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
        self.updates: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self._next_ts = 1

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> str:
        self.messages.append((channel, thread_ts, text))
        ts = f"900.{self._next_ts}"
        self._next_ts += 1
        return ts

    def update_message(self, *, channel: str, ts: str, text: str) -> None:
        self.updates.append((channel, ts, text))

    def delete_message(self, *, channel: str, ts: str) -> None:
        self.deletes.append((channel, ts))


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
            self.assertIn(("D2", "200.1", "done"), slack.messages)

    def test_manager_updates_one_slack_progress_message_and_deletes_it_before_final_output(self) -> None:
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
                    HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}),
                    HarnessEvent(
                        type="tool_use",
                        message="Cerebras OpenAI partnership AWS 2026 official Cerebras ...",
                        payload={"tool_name": "web_search"},
                    ),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual(
                [
                    ("D1", "100.1", "*Innie is searching the web*\n> web search"),
                    ("D1", "100.1", "final answer"),
                ],
                slack.messages,
            )
            self.assertEqual(
                [
                    (
                        "D1",
                        "900.1",
                        "*Innie is searching the web*\n> Cerebras OpenAI partnership AWS 2026 official Cerebras ...",
                    ),
                ],
                slack.updates,
            )
            self.assertEqual([("D1", "900.1")], slack.deletes)

    def test_manager_logs_task_started_to_terminal_not_slack(self) -> None:
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
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp), event_output=terminal.append)
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
            self.assertNotIn(("D1", "100.1", f"Started task {task_id}."), slack.messages)
            self.assertIn(f"session {session.id} task {task_id} started", terminal)
            self.assertIn(f"session {session.id} task {task_id} completed", terminal)

    def test_manager_does_not_duplicate_adapter_started_terminal_event(self) -> None:
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
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp), event_output=terminal.append)
            try:
                asyncio.run(manager.run_until_idle())
                started_messages = [message for message in terminal if message.endswith(" started")]
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
