from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.harness import HarnessArtifact, HarnessCapabilities, HarnessEvent, TokenUsage
from innie.tasks import append_harness_event, create_task, record_adapter_capabilities, record_artifacts, set_task_status


class TaskStorageTest(unittest.TestCase):
    def test_task_events_usage_artifacts_and_capabilities_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)
            db.execute(
                """
                INSERT INTO sessions(id, status, trigger_type, output_target, harness_id)
                VALUES('sess_1', 'new', 'dm', 'slack:D1:100.1', 'codex')
                """
            )

            task = create_task(
                db,
                session_id="sess_1",
                goal="run tests",
                output_target="slack:D1:100.1",
                harness_id="codex",
                execution_mode="autonomous",
            )
            append_harness_event(
                db,
                task,
                HarnessEvent(
                    type="usage",
                    usage=TokenUsage(input_tokens=10, output_tokens=5, cache_read_tokens=2),
                    payload={"raw": "kept"},
                ),
            )
            record_artifacts(db, task, [HarnessArtifact(kind="summary", path="summary.md", metadata={"lines": 3})])
            record_adapter_capabilities(db, "codex", HarnessCapabilities(supports_streaming=True))
            set_task_status(db, task.id, "completed")
            db.commit()

            stored_task = db.execute("SELECT * FROM tasks WHERE id = ?", (task.id,)).fetchone()
            stored_event = db.execute("SELECT * FROM task_events WHERE task_id = ?", (task.id,)).fetchone()
            stored_artifact = db.execute("SELECT * FROM artifacts WHERE task_id = ?", (task.id,)).fetchone()
            stored_caps = db.execute("SELECT * FROM harness_capabilities WHERE harness_id = 'codex'").fetchone()

            self.assertEqual("completed", stored_task["status"])
            self.assertEqual("harness.usage", stored_event["event_type"])
            self.assertIn('"input_tokens": 10', stored_event["payload_json"])
            self.assertEqual("summary", stored_artifact["kind"])
            self.assertIn("supports_streaming", stored_caps["capabilities_json"])


if __name__ == "__main__":
    unittest.main()
