from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from innie.cli import main
from innie.db import connect, initialize_schema


def seed_completed_task(workspace: Path) -> tuple[str, Path]:
    db_path = workspace / ".innie" / "innie.db"
    db_path.parent.mkdir(parents=True)
    db = connect(db_path)
    initialize_schema(db)
    artifact = workspace / ".innie" / "artifacts" / "old.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("old artifact", encoding="utf-8")
    db.execute(
        """
        INSERT INTO sessions(id, slack_channel_id, slack_root_ts, trigger_type, output_target, status, harness_id)
        VALUES('sess_old', 'D1', '100.1', 'dm', 'slack:D1:100.1', 'idle', 'codex')
        """
    )
    db.execute(
        """
        INSERT INTO tasks(id, session_id, status, goal, output_target, harness_id, execution_mode, completed_at)
        VALUES('task_old', 'sess_old', 'completed', 'old work', 'slack:D1:100.1', 'codex', 'autonomous', '2000-01-01T00:00:00.000Z')
        """
    )
    db.execute(
        """
        INSERT INTO task_events(session_id, task_id, event_type, payload_json)
        VALUES('sess_old', 'task_old', 'harness.output', '{}')
        """
    )
    db.execute(
        """
        INSERT INTO artifacts(session_id, task_id, kind, path, metadata_json)
        VALUES('sess_old', 'task_old', 'summary', ?, '{}')
        """,
        (str(artifact),),
    )
    db.commit()
    db.close()
    return "sess_old", artifact


class CliCleanupTest(unittest.TestCase):
    def test_cleanup_help_explains_dry_run_and_apply(self) -> None:
        out = StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            main(["cleanup", "--help"])

        self.assertEqual(0, raised.exception.code)
        help_text = " ".join(out.getvalue().lower().split())
        self.assertIn("dry run is the default", help_text)
        self.assertIn("older than 30 days", help_text)
        self.assertIn("--apply", help_text)

    def test_cleanup_dry_run_prints_preview_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _session_id, artifact = seed_completed_task(workspace)

            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, main(["--workspace", str(workspace), "cleanup"]))

            self.assertIn("Would delete 1 completed task(s)", out.getvalue())
            self.assertIn("task_old", out.getvalue())
            self.assertTrue(artifact.exists())

    def test_cleanup_apply_deletes_and_logs_cleanup_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            session_id, artifact = seed_completed_task(workspace)

            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, main(["--workspace", str(workspace), "cleanup", "--apply"]))

            self.assertIn("Deleted 1 completed task(s)", out.getvalue())
            self.assertFalse(artifact.exists())

            logs = StringIO()
            with redirect_stdout(logs):
                self.assertEqual(0, main(["--workspace", str(workspace), "logs", session_id]))
            self.assertIn("cleanup.applied", logs.getvalue())
            self.assertIn("task_old", logs.getvalue())


if __name__ == "__main__":
    unittest.main()
