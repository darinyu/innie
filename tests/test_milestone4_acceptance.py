from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from innie.cli import main
from innie.db import connect, initialize_schema


def insert_task(db, *, session_id: str, task_id: str, status: str, completed_at: str | None) -> None:
    db.execute(
        """
        INSERT INTO sessions(id, slack_channel_id, slack_root_ts, trigger_type, output_target, status, harness_id)
        VALUES(?, ?, ?, 'dm', ?, 'idle', 'codex')
        ON CONFLICT(id) DO NOTHING
        """,
        (session_id, f"D-{session_id}", f"100-{session_id}", f"slack:D:{session_id}"),
    )
    db.execute(
        """
        INSERT INTO tasks(id, session_id, status, goal, output_target, harness_id, execution_mode, completed_at)
        VALUES(?, ?, ?, ?, ?, 'codex', 'autonomous', ?)
        """,
        (task_id, session_id, status, task_id, f"slack:D:{session_id}", completed_at),
    )
    db.execute(
        """
        INSERT INTO task_events(session_id, task_id, event_type, payload_json)
        VALUES(?, ?, 'harness.output', '{}')
        """,
        (session_id, task_id),
    )


class Milestone4AcceptanceTest(unittest.TestCase):
    def test_cleanup_dry_run_apply_and_logs_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / ".innie" / "innie.db"
            db_path.parent.mkdir(parents=True)
            db = connect(db_path)
            initialize_schema(db)
            insert_task(db, session_id="sess_old", task_id="task_old_completed", status="completed", completed_at="2000-01-01T00:00:00.000Z")
            insert_task(db, session_id="sess_recent", task_id="task_recent_completed", status="completed", completed_at="2999-01-01T00:00:00.000Z")
            insert_task(db, session_id="sess_failed", task_id="task_failed", status="failed", completed_at="2000-01-01T00:00:00.000Z")
            insert_task(db, session_id="sess_interrupted", task_id="task_interrupted", status="interrupted", completed_at="2000-01-01T00:00:00.000Z")
            insert_task(db, session_id="sess_running", task_id="task_running", status="running", completed_at=None)
            db.commit()
            db.close()

            dry_run = StringIO()
            with redirect_stdout(dry_run):
                self.assertEqual(0, main(["--workspace", str(workspace), "cleanup"]))
            self.assertIn("Would delete 1 completed task(s)", dry_run.getvalue())
            self.assertIn("task_old_completed", dry_run.getvalue())
            self.assertNotIn("task_recent_completed", dry_run.getvalue())
            self.assertNotIn("task_failed", dry_run.getvalue())

            apply = StringIO()
            with redirect_stdout(apply):
                self.assertEqual(0, main(["--workspace", str(workspace), "cleanup", "--apply"]))
            self.assertIn("Deleted 1 completed task(s)", apply.getvalue())

            db = connect(db_path)
            try:
                remaining = [row["id"] for row in db.execute("SELECT id FROM tasks ORDER BY id")]
            finally:
                db.close()
            self.assertEqual(
                ["task_failed", "task_interrupted", "task_recent_completed", "task_running"],
                remaining,
            )

            logs = StringIO()
            with redirect_stdout(logs):
                self.assertEqual(0, main(["--workspace", str(workspace), "logs", "sess_old"]))
            self.assertIn("cleanup.applied", logs.getvalue())

            second_apply = StringIO()
            with redirect_stdout(second_apply):
                self.assertEqual(0, main(["--workspace", str(workspace), "cleanup", "--apply"]))
            self.assertIn("Deleted 0 completed task(s)", second_apply.getvalue())


if __name__ == "__main__":
    unittest.main()
