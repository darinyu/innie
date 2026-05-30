from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.hooks import run_trigger_accepted_hook
from innie.sessions import resolve_session_for_trigger
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

    def test_user_mention_skips_public_eyes_reaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            slack = FakeSlack()
            trigger = SlackTrigger(
                event_id="EvUserMention",
                trigger_type="user_mention",
                channel_id="C1",
                message_ts="100.1",
                thread_ts=None,
                sender_user_id="U1",
                text="<@U_DARIN> draft this",
                payload={"event_id": "EvUserMention"},
            )

            result = run_trigger_accepted_hook(db, trigger=trigger, slack=slack)

            self.assertEqual("skipped", result.status)
            self.assertTrue(result.skipped)
            self.assertEqual([], slack.reactions)
            row = db.execute("SELECT status, payload_json FROM hook_events").fetchone()
            self.assertEqual("skipped", row["status"])
            self.assertIn("user_mention_private_mode", row["payload_json"])

    def test_bot_mention_in_thread_reacts_to_reply_not_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            slack = FakeSlack()
            trigger = SlackTrigger(
                event_id="EvBotThread",
                trigger_type="bot_mention",
                channel_id="C1",
                message_ts="100.2",
                thread_ts="100.1",
                sender_user_id="U1",
                text="<@U_BOT> what do you think?",
                payload={"event_id": "EvBotThread"},
            )

            result = run_trigger_accepted_hook(db, trigger=trigger, slack=slack)

            self.assertEqual("ok", result.status)
            self.assertEqual([("C1", "100.2", "eyes")], slack.reactions)
            row = db.execute("SELECT payload_json FROM hook_events").fetchone()
            self.assertIn('"timestamp": "100.2"', row["payload_json"])

    def test_thread_reply_in_user_mention_session_skips_public_eyes_reaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            slack = FakeSlack()
            root = SlackTrigger(
                event_id="EvRoot",
                trigger_type="user_mention",
                channel_id="C1",
                message_ts="100.1",
                thread_ts=None,
                sender_user_id="U1",
                text="<@U_DARIN> draft this",
                payload={"event_id": "EvRoot"},
            )
            session = resolve_session_for_trigger(db, root)
            reply = SlackTrigger(
                event_id="EvReply",
                trigger_type="thread_reply",
                channel_id="C1",
                message_ts="100.2",
                thread_ts="100.1",
                sender_user_id="U1",
                text="more context",
                payload={"event_id": "EvReply"},
            )

            result = run_trigger_accepted_hook(db, trigger=reply, slack=slack, session_id=session.id)

            self.assertEqual("skipped", result.status)
            self.assertEqual([], slack.reactions)


if __name__ == "__main__":
    unittest.main()
