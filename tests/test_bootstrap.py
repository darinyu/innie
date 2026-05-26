from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import types
import unittest
from unittest import mock

from innie.bootstrap import check_dependencies, init_workspace


class BootstrapTest(unittest.TestCase):
    def test_init_creates_workspace_files_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = init_workspace(workspace, assume_yes=True)

            self.assertTrue(result.ok)
            self.assertTrue((workspace / ".innie" / "config.yaml").exists())
            self.assertTrue((workspace / ".innie" / "innie.db").exists())
            self.assertTrue((workspace / ".innie" / "artifacts").is_dir())

            db = sqlite3.connect(workspace / ".innie" / "innie.db")
            tables = {
                row[0]
                for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            self.assertLessEqual(
                {
                    "sessions",
                    "session_inbox",
                    "task_events",
                    "hook_events",
                    "artifacts",
                },
                tables,
            )

    def test_init_requires_confirmation_for_missing_optional_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with mock.patch("innie.bootstrap.shutil.which", return_value=None):
                result = init_workspace(workspace, input_fn=lambda _prompt: "n")

            self.assertFalse(result.ok)
            self.assertFalse((workspace / ".innie").exists())

    def test_init_defaults_to_continue_for_missing_optional_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            prompts: list[str] = []

            def input_fn(prompt: str) -> str:
                prompts.append(prompt)
                return ""

            with mock.patch("innie.bootstrap.shutil.which", return_value=None):
                result = init_workspace(workspace, input_fn=input_fn)

            self.assertTrue(result.ok)
            self.assertTrue((workspace / ".innie" / "innie.db").exists())
            self.assertEqual(["Continue and create Innie local state anyway? [Y/n] "], prompts)

    def test_init_allows_missing_slack_config_on_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with mock.patch("innie.bootstrap.shutil.which", return_value="/usr/local/bin/codex"):
                result = init_workspace(
                    workspace,
                    input_fn=lambda _prompt: self.fail("missing Slack config should not block init"),
                )

            self.assertTrue(result.ok)
            self.assertTrue((workspace / ".innie" / "innie.db").exists())
            self.assertIn("slack_config: missing", "\n".join(result.messages))

    def test_init_rerun_preserves_existing_config_and_reports_reused_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            first = init_workspace(workspace, assume_yes=True)
            config_path = workspace / ".innie" / "config.yaml"
            config_path.write_text("workspace_version: 1\ncustom: keep-me\n", encoding="utf-8")

            second = init_workspace(workspace, assume_yes=True)

            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertEqual("workspace_version: 1\ncustom: keep-me\n", config_path.read_text(encoding="utf-8"))
            messages = "\n".join(second.messages)
            self.assertIn("Using existing Innie local state", messages)
            self.assertIn("Using existing workspace config", messages)
            self.assertIn("Initialized or verified database", messages)

    def test_missing_harness_message_names_supported_opt_in_harnesses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with mock.patch("innie.bootstrap.shutil.which", return_value=None):
                result = init_workspace(workspace, assume_yes=True)

        messages = "\n".join(result.messages).lower()
        self.assertIn("none found: codex, claude", messages)
        self.assertNotIn("opencode", messages)
        self.assertNotIn("goose", messages)

    def test_python_310_is_supported(self) -> None:
        fake_version = types.SimpleNamespace(major=3, minor=10, micro=13)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("innie.bootstrap.sys.version_info", fake_version):
                statuses = check_dependencies(Path(tmp))

        python = next(status for status in statuses if status.name == "python")
        self.assertTrue(python.ok)


if __name__ == "__main__":
    unittest.main()
