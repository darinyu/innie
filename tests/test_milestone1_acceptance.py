from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from innie.control import cancel_session, summarize_session
from innie.db import connect, initialize_schema
from innie.harness import HarnessEvent, ScriptedHarnessAdapter
from innie.pipeline import accept_slack_event
from innie.runtime import SessionManager


class FakeSlack:
    def __init__(self) -> None:
        self.reactions: list[tuple[str, str, str]] = []
        self.messages: list[tuple[str, str, str]] = []
        self.updates: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self._next_ts = 1

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self.reactions.append((channel, timestamp, name))

    def post_message(
        self,
        *,
        channel: str,
        thread_ts: str | None,
        text: str,
        blocks: list[dict] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> str:
        self.messages.append((channel, thread_ts, text))
        ts = f"900.{self._next_ts}"
        self._next_ts += 1
        return ts

    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
        self.updates.append((channel, ts, text))

    def delete_message(self, *, channel: str, ts: str) -> None:
        self.deletes.append((channel, ts))


def event(event_id: str, ts: str, text: str, thread_ts: str | None = None) -> dict:
    payload = {
        "event_id": event_id,
        "event": {
            "type": "message",
            "channel_type": "channel",
            "channel": "C1",
            "user": "U1",
            "ts": ts,
            "text": text if thread_ts else f"<@U_DARIN> {text}",
        },
    }
    if thread_ts:
        payload["event"]["thread_ts"] = thread_ts
    return payload


class Milestone1AcceptanceTest(unittest.TestCase):
    def test_slack_to_durable_session_loop_end_to_end_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "innie.db"
            db = connect(db_path)
            initialize_schema(db)
            slack = FakeSlack()

            first = accept_slack_event(
                db,
                event("Ev1", "100.1", "first request"),
                bot_user_id="U_BOT",
                watched_user_id="U_DARIN",
                slack=slack,
                harness_id="codex",
            )
            second = accept_slack_event(
                db,
                event("Ev2", "100.2", "follow up", thread_ts="100.1"),
                bot_user_id="U_BOT",
                watched_user_id="U_DARIN",
                slack=slack,
                harness_id="codex",
            )

            self.assertTrue(first.decision.accepted)
            self.assertEqual(first.session.id, second.session.id)
            self.assertEqual([], slack.reactions)
            inbox_text = [
                row["text"]
                for row in db.execute("SELECT text FROM session_inbox WHERE session_id = ? ORDER BY id", (first.session.id,))
            ]
            self.assertEqual(["<@U_DARIN> first request", "follow up"], inbox_text)
            db.execute("UPDATE sessions SET status = 'running' WHERE id = ?", (first.session.id,))
            db.commit()
            db.close()

            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="output", message="placeholder agent work completed"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(db_path, adapters={"codex": adapter}, slack=slack, workspace=Path(tmp))
            try:
                self.assertEqual([first.session.id], manager.hydrate())
                asyncio.run(manager.run_until_idle())
                summary = summarize_session(manager.db, first.session.id)
                output_count = manager.db.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM task_events
                    WHERE session_id = ? AND event_type = 'harness.output'
                    """,
                    (first.session.id,),
                ).fetchone()["count"]
                cancel_text = cancel_session(manager.db, first.session.id)
            finally:
                manager.close()

            self.assertIn("status: idle", summary)
            self.assertEqual(2, output_count)
            self.assertIn("Canceled", cancel_text)


if __name__ == "__main__":
    unittest.main()
