from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import sqlite3
import uuid

from .harness import HarnessArtifact, HarnessCapabilities, HarnessEvent


@dataclass(frozen=True)
class TaskRecord:
    id: str
    session_id: str
    goal: str
    output_target: str
    harness_id: str
    execution_mode: str
    status: str


def create_task(
    db: sqlite3.Connection,
    *,
    session_id: str,
    goal: str,
    output_target: str,
    harness_id: str,
    execution_mode: str = "autonomous",
) -> TaskRecord:
    task_id = f"task_{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO tasks(id, session_id, status, goal, output_target, harness_id, execution_mode)
        VALUES(?, ?, 'created', ?, ?, ?, ?)
        """,
        (task_id, session_id, goal, output_target, harness_id, execution_mode),
    )
    return TaskRecord(task_id, session_id, goal, output_target, harness_id, execution_mode, "created")


def set_task_status(db: sqlite3.Connection, task_id: str, status: str) -> None:
    if status in {"completed", "failed", "canceled"}:
        db.execute(
            """
            UPDATE tasks
            SET status = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (status, task_id),
        )
    else:
        db.execute(
            """
            UPDATE tasks
            SET status = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (status, task_id),
        )


def append_harness_event(db: sqlite3.Connection, task: TaskRecord, event: HarnessEvent) -> None:
    payload = {
        "type": event.type,
        "message": event.message,
        "payload": event.payload,
        "usage": None if event.usage is None else asdict(event.usage),
    }
    db.execute(
        """
        INSERT INTO task_events(session_id, task_id, event_type, payload_json)
        VALUES(?, ?, ?, ?)
        """,
        (task.session_id, task.id, f"harness.{event.type}", json.dumps(payload, sort_keys=True)),
    )


def record_artifacts(db: sqlite3.Connection, task: TaskRecord, artifacts: list[HarnessArtifact]) -> None:
    for artifact in artifacts:
        db.execute(
            """
            INSERT INTO artifacts(session_id, task_id, kind, path, metadata_json)
            VALUES(?, ?, ?, ?, ?)
            """,
            (task.session_id, task.id, artifact.kind, artifact.path, json.dumps(artifact.metadata, sort_keys=True)),
        )


def record_adapter_capabilities(db: sqlite3.Connection, harness_id: str, capabilities: HarnessCapabilities) -> None:
    db.execute(
        """
        INSERT INTO harness_capabilities(harness_id, capabilities_json)
        VALUES(?, ?)
        ON CONFLICT(harness_id) DO UPDATE SET
            capabilities_json = excluded.capabilities_json,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (harness_id, json.dumps(asdict(capabilities), sort_keys=True)),
    )
