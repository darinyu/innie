from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.cleanup import apply_cleanup, preview_cleanup
from innie.db import connect, initialize_schema


def seed_task(
    db,
    *,
    session_id: str,
    task_id: str,
    status: str,
    completed_at: str | None = None,
    artifact_path: str | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO sessions(
            id, slack_channel_id, slack_root_ts, trigger_type, output_target, status, harness_id
        )
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
    if artifact_path is not None:
        db.execute(
            """
            INSERT INTO artifacts(session_id, task_id, kind, path, metadata_json)
            VALUES(?, ?, 'summary', ?, '{}')
            """,
            (session_id, task_id, artifact_path),
        )


def seed_slack_file(
    db,
    *,
    session_id: str,
    slack_file_id: str,
    local_path: str,
    byte_count: int,
) -> None:
    db.execute(
        """
        INSERT INTO slack_files(
            session_id,
            slack_event_id,
            slack_file_id,
            name,
            mimetype,
            filetype,
            url_private_download,
            local_path,
            byte_count,
            status,
            error
        )
        VALUES(?, 'EvFile', ?, ?, 'text/plain', 'text', 'https://files.example/' || ?, ?, ?, 'staged', NULL)
        """,
        (session_id, slack_file_id, Path(local_path).name, slack_file_id, local_path, byte_count),
    )


class CleanupTest(unittest.TestCase):
    def test_preview_only_includes_old_completed_tasks_and_innie_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            artifact = workspace / ".innie" / "artifacts" / "old.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("old artifact", encoding="utf-8")
            external = workspace / "external.txt"
            external.write_text("external", encoding="utf-8")

            seed_task(db, session_id="sess_old", task_id="task_old", status="completed", completed_at="2000-01-01T00:00:00.000Z", artifact_path=str(artifact))
            seed_task(db, session_id="sess_recent", task_id="task_recent", status="completed", completed_at="2999-01-01T00:00:00.000Z")
            seed_task(db, session_id="sess_failed", task_id="task_failed", status="failed", completed_at="2000-01-01T00:00:00.000Z")
            seed_task(db, session_id="sess_interrupted", task_id="task_interrupted", status="interrupted", completed_at="2000-01-01T00:00:00.000Z")
            seed_task(db, session_id="sess_external", task_id="task_external", status="completed", completed_at="2000-01-01T00:00:00.000Z", artifact_path=str(external))
            db.commit()

            preview = preview_cleanup(db, workspace)

            self.assertEqual(["task_external", "task_old"], preview.task_ids)
            self.assertEqual(2, preview.task_count)
            self.assertEqual(2, preview.event_count)
            self.assertEqual(1, preview.artifact_count)
            self.assertEqual(1, preview.file_count)
            self.assertEqual(len("old artifact"), preview.bytes_count)

    def test_preview_excludes_sessions_with_active_or_recoverable_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            seed_task(db, session_id="sess_active", task_id="task_old", status="completed", completed_at="2000-01-01T00:00:00.000Z")
            seed_task(db, session_id="sess_active", task_id="task_running", status="running")
            seed_task(db, session_id="sess_locked", task_id="task_locked", status="completed", completed_at="2000-01-01T00:00:00.000Z")
            db.execute("UPDATE sessions SET locked_by = 'worker-1' WHERE id = 'sess_locked'")
            db.commit()

            preview = preview_cleanup(db, workspace)

            self.assertEqual([], preview.task_ids)

    def test_apply_removes_eligible_task_data_and_records_cleanup_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            artifact = workspace / ".innie" / "artifacts" / "old.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("old artifact", encoding="utf-8")
            seed_task(db, session_id="sess_old", task_id="task_old", status="completed", completed_at="2000-01-01T00:00:00.000Z", artifact_path=str(artifact))
            seed_task(db, session_id="sess_recent", task_id="task_recent", status="completed", completed_at="2999-01-01T00:00:00.000Z")
            db.commit()

            result = apply_cleanup(db, workspace)

            self.assertEqual(["task_old"], result.task_ids)
            self.assertFalse(artifact.exists())
            remaining_tasks = [row["id"] for row in db.execute("SELECT id FROM tasks ORDER BY id")]
            self.assertEqual(["task_recent"], remaining_tasks)
            events = [
                row["event_type"]
                for row in db.execute("SELECT event_type FROM task_events WHERE session_id = 'sess_old' ORDER BY id")
            ]
            self.assertEqual(["cleanup.applied"], events)

    def test_preview_and_apply_include_old_staged_slack_files_under_innie_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            artifact = workspace / ".innie" / "artifacts" / "old.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("old artifact", encoding="utf-8")
            staged = workspace / ".innie" / "files" / "sess_old" / "EvFile" / "report.txt"
            staged.parent.mkdir(parents=True)
            staged.write_text("slack file", encoding="utf-8")
            seed_task(db, session_id="sess_old", task_id="task_old", status="completed", completed_at="2000-01-01T00:00:00.000Z", artifact_path=str(artifact))
            seed_slack_file(db, session_id="sess_old", slack_file_id="F1", local_path=str(staged), byte_count=staged.stat().st_size)
            db.commit()

            preview = preview_cleanup(db, workspace)

            self.assertEqual(["task_old"], preview.task_ids)
            self.assertEqual(2, preview.file_count)
            self.assertEqual(len("old artifact") + len("slack file"), preview.bytes_count)

            result = apply_cleanup(db, workspace)

            self.assertEqual(["task_old"], result.task_ids)
            self.assertFalse(artifact.exists())
            self.assertFalse(staged.exists())
            self.assertEqual(0, db.execute("SELECT COUNT(*) AS count FROM slack_files").fetchone()["count"])

    def test_cleanup_refuses_to_delete_slack_file_paths_outside_innie_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            outside = workspace / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            seed_task(db, session_id="sess_old", task_id="task_old", status="completed", completed_at="2000-01-01T00:00:00.000Z")
            seed_slack_file(db, session_id="sess_old", slack_file_id="F1", local_path=str(outside), byte_count=outside.stat().st_size)
            db.commit()

            preview = preview_cleanup(db, workspace)
            result = apply_cleanup(db, workspace)

            self.assertEqual(0, preview.file_count)
            self.assertEqual(0, preview.bytes_count)
            self.assertTrue(outside.exists())
            self.assertEqual(["task_old"], result.task_ids)
            self.assertEqual(1, db.execute("SELECT COUNT(*) AS count FROM slack_files").fetchone()["count"])

    def test_cleanup_keeps_session_files_when_session_has_recent_completed_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = connect(workspace / "innie.db")
            initialize_schema(db)
            staged = workspace / ".innie" / "files" / "sess_mixed" / "EvFile" / "report.txt"
            staged.parent.mkdir(parents=True)
            staged.write_text("slack file", encoding="utf-8")
            seed_task(db, session_id="sess_mixed", task_id="task_old", status="completed", completed_at="2000-01-01T00:00:00.000Z")
            seed_task(db, session_id="sess_mixed", task_id="task_recent", status="completed", completed_at="2999-01-01T00:00:00.000Z")
            seed_slack_file(db, session_id="sess_mixed", slack_file_id="F1", local_path=str(staged), byte_count=staged.stat().st_size)
            db.commit()

            preview = preview_cleanup(db, workspace)
            result = apply_cleanup(db, workspace)

            self.assertEqual(["task_old"], preview.task_ids)
            self.assertEqual(0, preview.file_count)
            self.assertEqual(0, preview.bytes_count)
            self.assertEqual(["task_old"], result.task_ids)
            self.assertTrue(staged.exists())
            self.assertEqual(1, db.execute("SELECT COUNT(*) AS count FROM slack_files").fetchone()["count"])


if __name__ == "__main__":
    unittest.main()
