from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.inbox import (
    available_queued_session_ids,
    claim_next_inbox_row,
    enqueue_trigger,
    mark_inbox_done,
    queued_inbox_rows,
)
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger


def make_trigger(event_id: str, channel: str, ts: str, text: str, thread_ts: str | None = None) -> SlackTrigger:
    return SlackTrigger(
        event_id=event_id,
        trigger_type="dm",
        channel_id=channel,
        message_ts=ts,
        thread_ts=thread_ts,
        sender_user_id="U1",
        text=text,
        payload={"event_id": event_id},
    )


class InboxTest(unittest.TestCase):
    def test_preserves_order_within_session_and_keeps_followup_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            first = make_trigger("Ev1", "D1", "100.1", "first")
            second = make_trigger("Ev2", "D1", "100.2", "second", thread_ts="100.1")
            persist_trigger(db, first)
            persist_trigger(db, second)
            session = resolve_session_for_trigger(db, first)

            enqueue_trigger(db, session=session, trigger=first)
            enqueue_trigger(db, session=session, trigger=second)
            claimed = claim_next_inbox_row(db, session.id)

            self.assertEqual("first", claimed.text)
            queued = queued_inbox_rows(db, session.id)
            self.assertEqual(["second"], [row.text for row in queued])
            mark_inbox_done(db, claimed.id)
            self.assertEqual("second", claim_next_inbox_row(db, session.id).text)

    def test_different_sessions_have_independent_queues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            one = make_trigger("Ev1", "D1", "100.1", "one")
            two = make_trigger("Ev2", "D2", "200.1", "two")
            for item in (one, two):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item)
                enqueue_trigger(db, session=session, trigger=item)

            sessions = db.execute("SELECT id FROM sessions ORDER BY slack_channel_id").fetchall()
            self.assertEqual("one", claim_next_inbox_row(db, sessions[0]["id"]).text)
            self.assertEqual("two", claim_next_inbox_row(db, sessions[1]["id"]).text)

    def test_available_queued_sessions_skip_locked_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            old_locked = make_trigger("Ev1", "D1", "100.1", "old locked")
            newer = make_trigger("Ev2", "D2", "200.1", "newer")
            for item in (old_locked, newer):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item)
                enqueue_trigger(db, session=session, trigger=item)

            locked_session_id = db.execute(
                "SELECT id FROM sessions WHERE slack_channel_id = 'D1'"
            ).fetchone()["id"]
            db.execute(
                """
                UPDATE sessions
                SET locked_by = 'worker-busy',
                    locked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    lock_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '+60 seconds')
                WHERE id = ?
                """,
                (locked_session_id,),
            )

            available = available_queued_session_ids(db)

            self.assertEqual(
                [db.execute("SELECT id FROM sessions WHERE slack_channel_id = 'D2'").fetchone()["id"]],
                available,
            )

    def test_available_queued_sessions_include_expired_session_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            item = make_trigger("Ev1", "D1", "100.1", "stale")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item)
            enqueue_trigger(db, session=session, trigger=item)
            db.execute(
                """
                UPDATE sessions
                SET locked_by = 'dead-worker',
                    locked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-120 seconds'),
                    lock_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-60 seconds')
                WHERE id = ?
                """,
                (session.id,),
            )

            available = available_queued_session_ids(db)

            self.assertEqual([session.id], available)


if __name__ == "__main__":
    unittest.main()
