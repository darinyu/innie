from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.slack_events import normalize_slack_event, persist_trigger


class SlackEventIntakeTest(unittest.TestCase):
    def test_accepts_dm_and_persists_trigger(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = connect(Path(tmp.name) / "innie.db")
        initialize_schema(db)
        payload = {
            "event_id": "Ev1",
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": "D1",
                "user": "U1",
                "ts": "171.1",
                "text": "fix this",
            },
        }

        decision = normalize_slack_event(payload, bot_user_id="U_BOT")
        self.assertTrue(decision.accepted)
        self.assertEqual("dm", decision.trigger.trigger_type)

        persist_trigger(db, decision.trigger)
        row = db.execute("SELECT * FROM slack_triggers WHERE slack_event_id = 'Ev1'").fetchone()
        self.assertEqual("D1", row["slack_channel_id"])
        self.assertEqual("fix this", row["text"])

    def test_accepts_channel_mention_and_ignores_irrelevant_message(self) -> None:
        mention = {
            "event_id": "Ev2",
            "event": {
                "type": "message",
                "channel_type": "channel",
                "channel": "C1",
                "user": "U1",
                "ts": "171.2",
                "text": "<@U_BOT> inspect",
            },
        }
        ignored = {
            "event_id": "Ev3",
            "event": {
                "type": "message",
                "channel_type": "channel",
                "channel": "C1",
                "user": "U1",
                "ts": "171.3",
                "text": "not for you",
            },
        }

        self.assertEqual("channel_mention", normalize_slack_event(mention, bot_user_id="U_BOT").trigger.trigger_type)
        decision = normalize_slack_event(ignored, bot_user_id="U_BOT")
        self.assertFalse(decision.accepted)
        self.assertEqual("not_for_innie", decision.reason)

    def test_accepts_watched_user_mention_when_configured(self) -> None:
        payload = {
            "event_id": "EvUserMention",
            "event": {
                "type": "message",
                "channel_type": "channel",
                "channel": "C1",
                "user": "U1",
                "ts": "171.5",
                "text": "<@U_DARIN> can you look?",
            },
        }

        decision = normalize_slack_event(payload, bot_user_id="U_BOT", watched_user_id="U_DARIN")

        self.assertTrue(decision.accepted)
        self.assertEqual("user_mention", decision.trigger.trigger_type)

    def test_rejects_self_echo_and_duplicate_retry(self) -> None:
        payload = {
            "event_id": "Ev4",
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": "D1",
                "user": "U_BOT",
                "ts": "171.4",
                "text": "my own message",
            },
        }
        self.assertEqual("self_echo", normalize_slack_event(payload, bot_user_id="U_BOT").reason)

        payload["event"]["user"] = "U1"
        decision = normalize_slack_event(payload, bot_user_id="U_BOT", seen_event_ids={"Ev4"})
        self.assertFalse(decision.accepted)
        self.assertEqual("duplicate_retry", decision.reason)


if __name__ == "__main__":
    unittest.main()
