from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.inbox import claim_next_inbox_row, enqueue_trigger, mark_inbox_done, queued_inbox_rows
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


if __name__ == "__main__":
    unittest.main()
