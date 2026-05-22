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
            slack_setup.assert_called_once_with(workspace)
            self.assertTrue((workspace / ".innie" / "innie.db").exists())
            self.assertIn("slack setup started", out.getvalue())

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
