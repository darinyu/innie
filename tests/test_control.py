from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.control import cancel_session, handle_control_message, summarize_session
from innie.db import connect, initialize_schema
from innie.harness import HarnessEvent
from innie.inbox import enqueue_trigger
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger
from innie.tasks import append_harness_event, create_task, set_task_status


class FakeSlackReplies:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        self.messages.append((channel, thread_ts, text))


def make_session(db):
    trigger = SlackTrigger(
        event_id="Ev1",
        trigger_type="dm",
        channel_id="D1",
        message_ts="100.1",
        thread_ts=None,
        sender_user_id="U1",
        text="work",
        payload={"event_id": "Ev1"},
    )
    persist_trigger(db, trigger)
    session = resolve_session_for_trigger(db, trigger)
    enqueue_trigger(db, session=session, trigger=trigger)
    return session


class ControlTest(unittest.TestCase):
    def test_status_posts_durable_session_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            session = make_session(db)
            task = create_task(
                db,
                session_id=session.id,
                goal="work",
                output_target=session.output_target,
                harness_id="codex",
            )
            append_harness_event(db, task, HarnessEvent(type="progress", message="running tests"))
            set_task_status(db, task.id, "running")
            slack = FakeSlackReplies()

            result = handle_control_message(
                db,
                session_id=session.id,
                text="status",
                slack=slack,
                channel="D1",
                thread_ts="100.1",
            )

            self.assertTrue(result.handled)
            self.assertIn("queued_inputs: 1", result.text)
            self.assertIn(f"current_task: {task.id} running via codex", result.text)
            self.assertIn("last_event: harness.progress", result.text)
            self.assertEqual("D1", slack.messages[0][0])

    def test_cancel_marks_session_and_inbox_canceled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            session = make_session(db)

            text = cancel_session(db, session.id)

            self.assertIn("Canceled", text)
            status = db.execute("SELECT status FROM sessions WHERE id = ?", (session.id,)).fetchone()["status"]
            inbox_status = db.execute("SELECT status FROM session_inbox WHERE session_id = ?", (session.id,)).fetchone()[
                "status"
            ]
            self.assertEqual("canceled", status)
            self.assertEqual("canceled", inbox_status)
            self.assertIn("status: canceled", summarize_session(db, session.id))


if __name__ == "__main__":
    unittest.main()
