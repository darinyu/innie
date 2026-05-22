from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger


def trigger(event_id: str, ts: str, thread_ts: str | None = None) -> SlackTrigger:
    return SlackTrigger(
        event_id=event_id,
        trigger_type="dm",
        channel_id="D1",
        message_ts=ts,
        thread_ts=thread_ts,
        sender_user_id="U1",
        text="fix",
        payload={"event_id": event_id},
    )


class SessionTest(unittest.TestCase):
    def test_same_slack_thread_resolves_to_same_session_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            root = trigger("Ev1", "100.1")
            reply = trigger("Ev2", "100.2", thread_ts="100.1")
            persist_trigger(db, root)
            first = resolve_session_for_trigger(db, root, harness_id="codex")
            db.commit()
            db.close()

            db = connect(path)
            initialize_schema(db)
            persist_trigger(db, reply)
            second = resolve_session_for_trigger(db, reply, harness_id="codex")

            self.assertEqual(first.id, second.id)
            self.assertEqual("D1", second.slack_channel_id)
            self.assertEqual("100.1", second.slack_root_ts)
            self.assertEqual("slack:D1:100.1", second.output_target)
            linked = db.execute("SELECT session_id FROM slack_triggers WHERE slack_event_id = 'Ev2'").fetchone()
            self.assertEqual(first.id, linked["session_id"])


if __name__ == "__main__":
    unittest.main()
