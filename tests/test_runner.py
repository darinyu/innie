from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from innie.harness import HarnessEvent, ScriptedHarnessAdapter
from innie.progress import SLACK_FINAL_TEXT_LIMIT, SLACK_TEXT_LIMIT
from innie.runner import ConsoleSlackClient, format_run_acceptance, run_forever_socket, run_once_payload, run_once_socket


def payload(text: str = "hello from slack") -> dict:
    return {
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "ts": "100.1",
            "text": text,
        },
    }


class RunnerTest(unittest.TestCase):
    def test_run_once_payload_routes_slack_shape_through_echo_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            printed: list[str] = []
            slack = ConsoleSlackClient(output=printed.append)

            result = run_once_payload(
                workspace,
                payload(),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=slack,
            )

            self.assertTrue(result.accepted)
            self.assertEqual("accepted", result.reason)
            self.assertIsNotNone(result.session_id)
            self.assertEqual("new", result.session_status)
            self.assertEqual("echo", result.harness_id)
            self.assertTrue((workspace / ".innie" / "innie.db").exists())
            self.assertIn("reaction D1 100.1 eyes", printed)
            self.assertIn("message D1 100.1 hello from slack", printed)

    def test_run_once_payload_verbose_logs_session_before_harness_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            slack = ConsoleSlackClient(output=lambda _line: None)

            result = run_once_payload(
                Path(tmp),
                payload(),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=slack,
                verbose=True,
                output=printed.append,
            )

            self.assertIn(f"accepted new session {result.session_id} via echo", printed)
            self.assertTrue(any(f"session {result.session_id} task " in line for line in printed))

    def test_run_once_payload_reports_rejected_event_without_running_adapter(self) -> None:
        ignored = payload(text="not for bot")
        ignored["event"]["channel_type"] = "channel"
        with tempfile.TemporaryDirectory() as tmp:
            result = run_once_payload(
                Path(tmp),
                ignored,
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=lambda _line: None),
            )

            self.assertFalse(result.accepted)
            self.assertEqual("not_for_innie", result.reason)

    def test_run_once_payload_expands_progress_details_interaction(self) -> None:
        class RecordingSlack(ConsoleSlackClient):
            def __init__(self) -> None:
                super().__init__(output=lambda _line: None)
                self.updates: list[tuple[str, str, str, list[dict] | None]] = []

            def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
                self.updates.append((channel, ts, text, blocks))

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            slack = RecordingSlack()
            result = run_once_payload(
                workspace,
                payload(),
                harness_id="scripted",
                bot_user_id="U_BOT",
                slack=slack,
                adapters={
                    "scripted": ScriptedHarnessAdapter(
                        events=[
                            HarnessEvent(type="progress", message="checking context"),
                            HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}),
                            HarnessEvent(type="usage"),
                            HarnessEvent(type="output", message="final answer"),
                            HarnessEvent(type="completed"),
                        ]
                    )
                },
            )
            db_path = workspace / ".innie" / "innie.db"
            db = sqlite3.connect(db_path)
            try:
                task_id = db.execute("SELECT id FROM tasks").fetchone()[0]
            finally:
                db.close()
            slack.updates.clear()

            interaction = {
                "type": "block_actions",
                "channel": {"id": "D1"},
                "message": {"ts": "900.1", "text": "final answer"},
                "actions": [{"action_id": "innie_show_progress_details", "value": task_id}],
            }

            interaction_result = run_once_payload(
                workspace,
                interaction,
                harness_id="scripted",
                bot_user_id="U_BOT",
                slack=slack,
                adapters={"scripted": ScriptedHarnessAdapter(events=[])},
            )

            self.assertTrue(interaction_result.accepted)
            self.assertEqual("progress_details", interaction_result.reason)
            self.assertEqual(("D1", "900.1", "final answer"), slack.updates[-1][:3])
            blocks = slack.updates[-1][3]
            self.assertIsNotNone(blocks)
            self.assertEqual("section", blocks[1]["type"])
            self.assertEqual("checking context", blocks[1]["text"]["text"])
            self.assertEqual("actions", blocks[2]["type"])
            self.assertEqual("innie_hide_progress_details", blocks[2]["elements"][0]["action_id"])
            self.assertNotIn("web search", blocks[1]["text"]["text"])

            interaction["actions"] = [{"action_id": "innie_hide_progress_details", "value": task_id}]
            run_once_payload(
                workspace,
                interaction,
                harness_id="scripted",
                bot_user_id="U_BOT",
                slack=slack,
                adapters={"scripted": ScriptedHarnessAdapter(events=[])},
            )

            folded_blocks = slack.updates[-1][3]
            self.assertIsNotNone(folded_blocks)
            self.assertEqual("actions", folded_blocks[1]["type"])
            self.assertEqual("innie_show_progress_details", folded_blocks[1]["elements"][0]["action_id"])
            self.assertNotIn("innie-progress-details", [block.get("block_id") for block in folded_blocks])

    def test_progress_details_interaction_does_not_resend_huge_slack_message_text(self) -> None:
        class SizeGuardSlack(ConsoleSlackClient):
            def __init__(self) -> None:
                super().__init__(output=lambda _line: None)
                self.updates: list[tuple[str, str, str, list[dict] | None]] = []

            def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
                if len(text) > SLACK_FINAL_TEXT_LIMIT:
                    raise RuntimeError("chat.update failed: msg_too_long")
                self.updates.append((channel, ts, text, blocks))

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            slack = SizeGuardSlack()
            first_line = "a" * (SLACK_TEXT_LIMIT - 5)
            second_line = "second slack message"
            result = run_once_payload(
                workspace,
                payload(),
                harness_id="scripted",
                bot_user_id="U_BOT",
                slack=slack,
                adapters={
                    "scripted": ScriptedHarnessAdapter(
                        events=[
                            HarnessEvent(type="progress", message="checking context"),
                            HarnessEvent(type="output", message=f"{first_line}\n{second_line}"),
                            HarnessEvent(type="completed"),
                        ]
                    )
                },
            )
            db_path = workspace / ".innie" / "innie.db"
            db = sqlite3.connect(db_path)
            try:
                task_id = db.execute("SELECT id FROM tasks").fetchone()[0]
            finally:
                db.close()
            slack.updates.clear()

            interaction = {
                "type": "block_actions",
                "channel": {"id": "D1"},
                "message": {"ts": "900.1", "text": "x" * (SLACK_TEXT_LIMIT + 1000)},
                "actions": [{"action_id": "innie_show_progress_details", "value": task_id}],
            }

            interaction_result = run_once_payload(
                workspace,
                interaction,
                harness_id="scripted",
                bot_user_id="U_BOT",
                slack=slack,
                adapters={"scripted": ScriptedHarnessAdapter(events=[])},
            )

            self.assertTrue(result.accepted)
            self.assertTrue(interaction_result.accepted)
            self.assertEqual("progress_details", interaction_result.reason)
            self.assertEqual(("D1", "900.1", first_line[:SLACK_FINAL_TEXT_LIMIT]), slack.updates[-1][:3])

    def test_progress_details_interaction_swallow_slack_update_failure(self) -> None:
        class FailingSlack(ConsoleSlackClient):
            def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
                raise RuntimeError("chat.update failed: msg_too_long")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            run_once_payload(
                workspace,
                payload(),
                harness_id="scripted",
                bot_user_id="U_BOT",
                slack=FailingSlack(output=lambda _line: None),
                adapters={
                    "scripted": ScriptedHarnessAdapter(
                        events=[
                            HarnessEvent(type="progress", message="checking context"),
                            HarnessEvent(type="output", message="final answer"),
                            HarnessEvent(type="completed"),
                        ]
                    )
                },
            )
            db = sqlite3.connect(workspace / ".innie" / "innie.db")
            try:
                task_id = db.execute("SELECT id FROM tasks").fetchone()[0]
            finally:
                db.close()

            interaction_result = run_once_payload(
                workspace,
                {
                    "type": "block_actions",
                    "channel": {"id": "D1"},
                    "message": {"ts": "900.1", "text": "final answer"},
                    "actions": [{"action_id": "innie_show_progress_details", "value": task_id}],
                },
                harness_id="scripted",
                bot_user_id="U_BOT",
                slack=FailingSlack(output=lambda _line: None),
                adapters={"scripted": ScriptedHarnessAdapter(events=[])},
            )

            self.assertTrue(interaction_result.accepted)
            self.assertEqual("progress_details", interaction_result.reason)

    def test_cli_event_file_is_supported_by_runner_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            event_path.write_text(json.dumps(payload()), encoding="utf-8")

            self.assertEqual("hello from slack", json.loads(event_path.read_text(encoding="utf-8"))["event"]["text"])

    def test_run_once_socket_routes_first_socket_payload(self) -> None:
        class FakeEventSource:
            async def receive_once(self) -> dict:
                return payload("hello from socket")

        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            result = run_once_socket(
                Path(tmp),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=printed.append),
                event_source=FakeEventSource(),
            )

            self.assertTrue(result.accepted)
            self.assertIn("message D1 100.1 hello from socket", printed)

    def test_run_once_socket_ignores_self_echo_until_first_accepted_event(self) -> None:
        class FakeEventSource:
            def __init__(self) -> None:
                self._payloads = [
                    {
                        "event_id": "EvSelf",
                        "event": {
                            "type": "message",
                            "channel": "C1",
                            "user": "U_BOT",
                            "bot_id": "B1",
                            "ts": "99.1",
                            "text": "Task completed.",
                        },
                    },
                    payload("hello after self echo"),
                ]

            async def receive_once(self) -> dict:
                return self._payloads.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            result = run_once_socket(
                Path(tmp),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=printed.append),
                event_source=FakeEventSource(),
                output=printed.append,
            )

            self.assertTrue(result.accepted)
            self.assertEqual("new", result.session_status)
            self.assertIn(
                "ignored event: self_echo event_id=EvSelf type=message channel=C1 ts=99.1 user=U_BOT bot_id=B1 text=Task completed.",
                printed,
            )
            self.assertIn("message D1 100.1 hello after self echo", printed)

    def test_run_forever_socket_processes_until_event_source_stops(self) -> None:
        class FakeEventSource:
            def __init__(self) -> None:
                self._payloads = [payload("first"), payload("second")]
                self._payloads[1]["event_id"] = "Ev2"
                self._payloads[1]["event"]["ts"] = "100.2"

            async def receive_once(self) -> dict:
                if not self._payloads:
                    raise KeyboardInterrupt
                return self._payloads.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            processed = run_forever_socket(
                Path(tmp),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=printed.append),
                event_source=FakeEventSource(),
                output=printed.append,
            )

            self.assertEqual(2, processed)
            self.assertIn("waiting for Slack event #1", printed)
            self.assertIn("waiting for Slack event #2", printed)
            self.assertIn("stopped after 2 accepted event(s)", printed)
            self.assertIn("message D1 100.1 first", printed)
            self.assertIn("message D1 100.2 second", printed)

    def test_run_forever_socket_reports_existing_session_for_thread_reply(self) -> None:
        class FakeEventSource:
            def __init__(self) -> None:
                self._payloads = [payload("root"), payload("reply")]
                self._payloads[1]["event_id"] = "Ev2"
                self._payloads[1]["event"]["ts"] = "100.2"
                self._payloads[1]["event"]["thread_ts"] = "100.1"

            async def receive_once(self) -> dict:
                if not self._payloads:
                    raise KeyboardInterrupt
                return self._payloads.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            run_forever_socket(
                Path(tmp),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=printed.append),
                event_source=FakeEventSource(),
                output=printed.append,
            )

            accepted_lines = [line for line in printed if line.startswith("accepted ")]
            self.assertEqual(2, len(accepted_lines))
            self.assertTrue(accepted_lines[0].startswith("accepted new session "))
            self.assertTrue(accepted_lines[1].startswith("accepted existing session "))
            self.assertEqual(accepted_lines[0].replace("accepted new session ", ""), accepted_lines[1].replace("accepted existing session ", ""))

    def test_run_forever_socket_verbose_reports_acceptance_once(self) -> None:
        class FakeEventSource:
            def __init__(self) -> None:
                self._payloads = [payload("root")]

            async def receive_once(self) -> dict:
                if not self._payloads:
                    raise KeyboardInterrupt
                return self._payloads.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            run_forever_socket(
                Path(tmp),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=printed.append),
                event_source=FakeEventSource(),
                output=printed.append,
                verbose=True,
            )

            accepted_lines = [line for line in printed if line.startswith("accepted ")]
            self.assertEqual(1, len(accepted_lines))

    def test_run_forever_socket_explains_ignored_events(self) -> None:
        class FakeEventSource:
            def __init__(self) -> None:
                self._payloads = [
                    {
                        "event_id": "Ignored1",
                        "event": {
                            "type": "message",
                            "channel": "C1",
                            "ts": "200.1",
                            "user": "U2",
                            "text": "ambient channel chatter",
                        },
                    }
                ]

            async def receive_once(self) -> dict:
                if not self._payloads:
                    raise KeyboardInterrupt
                return self._payloads.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            printed: list[str] = []
            processed = run_forever_socket(
                Path(tmp),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=printed.append),
                event_source=FakeEventSource(),
                output=printed.append,
            )

            self.assertEqual(0, processed)
            self.assertIn(
                "ignored event: not_for_innie event_id=Ignored1 type=message channel=C1 ts=200.1 user=U2 text=ambient channel chatter",
                printed,
            )

    def test_format_run_acceptance_includes_actual_session_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_once_payload(
                Path(tmp),
                payload(),
                harness_id="echo",
                bot_user_id="U_BOT",
                slack=ConsoleSlackClient(output=lambda _line: None),
            )

        self.assertEqual(f"accepted new session {result.session_id} via echo", format_run_acceptance(result))


if __name__ == "__main__":
    unittest.main()
