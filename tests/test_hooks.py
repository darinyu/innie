from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.hooks import run_trigger_accepted_hook
from innie.slack_events import SlackTrigger


class FakeSlack:
    def __init__(self) -> None:
        self.reactions: list[tuple[str, str, str]] = []

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self.reactions.append((channel, timestamp, name))


class HookTest(unittest.TestCase):
    def test_trigger_accepted_posts_eyes_once_and_records_hook_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            slack = FakeSlack()
            trigger = SlackTrigger(
                event_id="Ev1",
                trigger_type="dm",
                channel_id="D1",
                message_ts="100.1",
                thread_ts=None,
                sender_user_id="U1",
                text="fix",
                payload={"event_id": "Ev1"},
            )

            first = run_trigger_accepted_hook(db, trigger=trigger, slack=slack)
            second = run_trigger_accepted_hook(db, trigger=trigger, slack=slack)

            self.assertEqual("ok", first.status)
            self.assertTrue(second.skipped)
            self.assertEqual([("D1", "100.1", "eyes")], slack.reactions)
            rows = list(db.execute("SELECT hook_name, status FROM hook_events"))
            self.assertEqual([("trigger.accepted", "ok")], [tuple(row) for row in rows])


if __name__ == "__main__":
    unittest.main()
