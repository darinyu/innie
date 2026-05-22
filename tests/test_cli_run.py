from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from innie.cli import main
from innie.runner import RunOnceResult


class CliRunTest(unittest.TestCase):
    def test_run_once_event_file_routes_through_echo_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            event_file = workspace / "event.json"
            event_file.write_text(
                json.dumps(
                    {
                        "event_id": "Ev1",
                        "event": {
                            "type": "message",
                            "channel_type": "im",
                            "channel": "D1",
                            "user": "U1",
                            "ts": "100.1",
                            "text": "hello from cli",
                        },
                    }
                ),
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "--workspace",
                        str(workspace),
                        "run",
                        "--once",
                        "--event-file",
                        str(event_file),
                        "--harness",
                        "echo",
                    ]
                )

            self.assertEqual(0, code)
            output = stdout.getvalue()
            self.assertIn("reaction D1 100.1 eyes", output)
            self.assertIn("message D1 100.1 Done:\nhello from cli", output)
            self.assertIn("accepted new session", output)
            self.assertIn("logs:", output)

    def test_run_once_without_event_file_uses_socket_mode_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = StringIO()
            with mock.patch(
                "innie.cli.run_once_socket",
                return_value=RunOnceResult(True, "accepted", "sess_1", session_status="new"),
            ) as run:
                with redirect_stdout(stdout):
                    code = main(["--workspace", tmp, "run", "--once", "--harness", "echo"])

            self.assertEqual(0, code)
            output = stdout.getvalue()
            self.assertIn("Innie run starting", output)
            self.assertIn("waiting for one Slack event", output)
            self.assertIn("accepted new session sess_1", output)
            self.assertIn("processed one event; exiting because --once was set", output)
            run.assert_called_once()

    def test_run_once_defaults_to_codex_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = StringIO()
            with mock.patch(
                "innie.cli.run_once_socket",
                return_value=RunOnceResult(True, "accepted", "sess_1", session_status="new"),
            ) as run:
                with redirect_stdout(stdout):
                    code = main(["--workspace", tmp, "run", "--once"])

            self.assertEqual(0, code)
            self.assertIn("Innie run starting: harness=codex", stdout.getvalue())
            self.assertEqual("codex", run.call_args.kwargs["harness_id"])

    def test_run_without_once_uses_continuous_socket_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = StringIO()
            with mock.patch("innie.cli.run_forever_socket", return_value=2) as run:
                with redirect_stdout(stdout):
                    code = main(["--workspace", tmp, "run", "--harness", "echo"])

            self.assertEqual(0, code)
            output = stdout.getvalue()
            self.assertIn("Innie run starting", output)
            self.assertIn("continuous=True", output)
            run.assert_called_once()

    def test_run_status_messages_flush_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("innie.cli.run_once_socket", return_value=RunOnceResult(True, "accepted", "sess_1", session_status="new")):
                with mock.patch("builtins.print") as print_mock:
                    self.assertEqual(0, main(["--workspace", tmp, "run", "--once", "--harness", "echo"]))

            self.assertIn(mock.call("Innie run starting: harness=echo once=True continuous=False", flush=True), print_mock.mock_calls)
            self.assertIn(mock.call("Socket Mode enabled; waiting for one Slack event...", flush=True), print_mock.mock_calls)


if __name__ == "__main__":
    unittest.main()
