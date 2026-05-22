from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.harness import HarnessArtifact, HarnessEvent, ScriptedHarnessAdapter, TokenUsage
from innie.pipeline import accept_slack_event
from innie.runtime import SessionManager


class FakeSlack:
    def __init__(self) -> None:
        self.reactions: list[tuple[str, str, str]] = []
        self.messages: list[tuple[str, str, str]] = []

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self.reactions.append((channel, timestamp, name))

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        self.messages.append((channel, thread_ts, text))


def event() -> dict:
    return {
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "ts": "100.1",
            "text": "run the milestone 2 test",
        },
    }


class Milestone2AcceptanceTest(unittest.TestCase):
    def test_one_harness_adapter_streams_progress_output_usage_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "innie.db"
            db = connect(db_path)
            initialize_schema(db)
            slack = FakeSlack()
            accepted = accept_slack_event(db, event(), bot_user_id="U_BOT", slack=slack, harness_id="scripted")
            db.close()

            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="progress", message="checking repository"),
                    HarnessEvent(type="usage", usage=TokenUsage(input_tokens=20, output_tokens=5, cache_read_tokens=10)),
                    HarnessEvent(type="output", message="milestone 2 complete"),
                    HarnessEvent(type="completed"),
                ],
                artifacts=[HarnessArtifact(kind="summary", path=str(Path(tmp) / "summary.md"))],
            )
            manager = SessionManager(db_path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
                task_events = [
                    row["event_type"]
                    for row in manager.db.execute(
                        "SELECT event_type FROM task_events WHERE session_id = ?",
                        (accepted.session.id,),
                    )
                ]
                artifact_count = manager.db.execute(
                    "SELECT COUNT(*) AS count FROM artifacts WHERE session_id = ?",
                    (accepted.session.id,),
                ).fetchone()["count"]
                capabilities = manager.db.execute(
                    "SELECT capabilities_json FROM harness_capabilities WHERE harness_id = 'scripted'"
                ).fetchone()
            finally:
                manager.close()

            self.assertIn(("D1", "100.1", "eyes"), slack.reactions)
            self.assertIn(("D1", "100.1", "Progress: checking repository"), slack.messages)
            self.assertIn(("D1", "100.1", "milestone 2 complete"), slack.messages)
            self.assertIn("harness.usage", task_events)
            self.assertIn("harness.completed", task_events)
            self.assertEqual(1, artifact_count)
            self.assertIn("supports_structured_artifacts", capabilities["capabilities_json"])

    def test_followup_in_existing_innie_thread_is_routed_without_mention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "innie.db"
            db = connect(db_path)
            initialize_schema(db)
            slack = FakeSlack()
            root = accept_slack_event(
                db,
                event(),
                bot_user_id="U_BOT",
                slack=slack,
                harness_id="scripted",
            )
            reply_payload = event()
            reply_payload["event_id"] = "Ev2"
            reply_payload["event"]["ts"] = "100.2"
            reply_payload["event"]["thread_ts"] = "100.1"
            reply_payload["event"]["text"] = "more context"

            reply = accept_slack_event(
                db,
                reply_payload,
                bot_user_id="U_BOT",
                slack=slack,
                harness_id="scripted",
            )

            self.assertTrue(reply.decision.accepted)
            self.assertEqual("thread_reply", reply.decision.trigger.trigger_type)
            self.assertEqual(root.session.id, reply.session.id)


if __name__ == "__main__":
    unittest.main()
