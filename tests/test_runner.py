from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

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
