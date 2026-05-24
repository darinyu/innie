from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from innie.cli import main


@dataclass(frozen=True)
class FakeSlackResult:
    ok: bool
    messages: list[str]


class CliInitTest(unittest.TestCase):
    def test_init_starts_slack_setup_by_default_after_state_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            out = StringIO()
            with mock.patch(
                "innie.cli.run_slack_setup",
                return_value=FakeSlackResult(True, ["slack setup started"]),
            ) as slack_setup:
                with redirect_stdout(out):
                    result = main(["--state-dir", str(workspace), "init", "--yes"])

            self.assertEqual(0, result)
            slack_setup.assert_called_once()
            self.assertEqual(workspace, slack_setup.call_args.args[0])
            self.assertIn("prompt_secret", slack_setup.call_args.kwargs)
            self.assertTrue((workspace / ".innie" / "innie.db").exists())
            self.assertIn("slack setup started", out.getvalue())

    def test_init_missing_slack_config_prompts_for_slack_setup_not_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            out = StringIO()
            with mock.patch(
                "innie.cli.run_slack_setup",
                return_value=FakeSlackResult(True, ["slack setup started"]),
            ) as slack_setup:
                with mock.patch("innie.bootstrap.shutil.which", return_value="/usr/local/bin/codex"):
                    with mock.patch("builtins.input", return_value="") as input_mock:
                        with redirect_stdout(out):
                            result = main(["--state-dir", str(workspace), "init"])

            self.assertEqual(0, result)
            slack_setup.assert_called_once()
            input_mock.assert_called_once_with("Set up Slack now? [Y/n] ")
            self.assertTrue((workspace / ".innie" / "innie.db").exists())
            self.assertIn("slack_config: missing", out.getvalue())
            self.assertNotIn("Canceled before creating local state", out.getvalue())

    def test_init_rerun_keeps_existing_slack_setup_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            innie_dir = workspace / ".innie"
            innie_dir.mkdir()
            (innie_dir / "config.yaml").write_text("workspace_version: 1\nslack:\n  configured: true\n", encoding="utf-8")
            out = StringIO()
            with mock.patch("innie.cli.run_slack_setup") as slack_setup:
                with mock.patch("innie.bootstrap.shutil.which", return_value="/usr/local/bin/codex"):
                    with mock.patch("builtins.input") as input_mock:
                        with redirect_stdout(out):
                            result = main(["--state-dir", str(workspace), "init"])

            self.assertEqual(0, result)
            slack_setup.assert_not_called()
            input_mock.assert_not_called()
            self.assertIn("Slack setup already configured", out.getvalue())

    def test_init_can_skip_slack_setup_for_local_state_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            out = StringIO()
            with mock.patch("innie.cli.run_slack_setup") as slack_setup:
                with redirect_stdout(out):
                    result = main(["--state-dir", str(workspace), "init", "--yes", "--skip-slack-setup"])

            self.assertEqual(0, result)
            slack_setup.assert_not_called()
            self.assertTrue((workspace / ".innie" / "innie.db").exists())


if __name__ == "__main__":
    unittest.main()
