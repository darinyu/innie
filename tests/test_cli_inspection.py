from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from innie.cli import main
from innie.db import connect, initialize_schema
from innie.harness import HarnessArtifact, HarnessCapabilities, HarnessEvent
from innie.inbox import enqueue_trigger
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger
from innie.tasks import append_harness_event, create_task, record_adapter_capabilities, record_artifacts, set_task_status


def seed_session(workspace: Path) -> str:
    db_path = workspace / ".innie" / "innie.db"
    db_path.parent.mkdir(parents=True)
    db = connect(db_path)
    initialize_schema(db)
    trigger = SlackTrigger(
        event_id="Ev1",
        trigger_type="dm",
        channel_id="D1",
        message_ts="100.1",
        thread_ts=None,
        sender_user_id="U1",
        text="work",
        payload={"event_id": "Ev1"},
    )
    persist_trigger(db, trigger)
    session = resolve_session_for_trigger(db, trigger, harness_id="codex")
    enqueue_trigger(db, session=session, trigger=trigger)
    task = create_task(
        db,
        session_id=session.id,
        goal="work",
        output_target=session.output_target,
        harness_id="codex",
    )
    append_harness_event(db, task, HarnessEvent(type="progress", message="running tests"))
    set_task_status(db, task.id, "running")
    record_artifacts(db, task, [HarnessArtifact(kind="summary", path="summary.md")])
    record_adapter_capabilities(db, "codex", HarnessCapabilities(supports_streaming=True))
    db.commit()
    db.close()
    return session.id


class CliInspectionTest(unittest.TestCase):
    def test_status_logs_and_cancel_commands_read_local_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            session_id = seed_session(workspace)

            status_out = StringIO()
            with redirect_stdout(status_out):
                self.assertEqual(0, main(["--workspace", str(workspace), "status", session_id]))
            self.assertIn("queued_inputs: 1", status_out.getvalue())

            logs_out = StringIO()
            with redirect_stdout(logs_out):
                self.assertEqual(0, main(["--workspace", str(workspace), "logs", session_id]))
            self.assertIn("inbox:", logs_out.getvalue())
            self.assertIn("work", logs_out.getvalue())
            self.assertIn("tasks:", logs_out.getvalue())
            self.assertIn("artifacts:", logs_out.getvalue())
            self.assertIn("harness_capabilities:", logs_out.getvalue())
            self.assertIn("codex", logs_out.getvalue())

            cancel_out = StringIO()
            with redirect_stdout(cancel_out):
                self.assertEqual(0, main(["--workspace", str(workspace), "cancel", session_id]))
            self.assertIn("Canceled", cancel_out.getvalue())


if __name__ == "__main__":
    unittest.main()
