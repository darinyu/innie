from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3


DEFAULT_COMPLETED_RETENTION_DAYS = 30


@dataclass(frozen=True)
class CleanupPreview:
    task_ids: list[str]
    event_count: int
    artifact_count: int
    file_count: int
    bytes_count: int

    @property
    def task_count(self) -> int:
        return len(self.task_ids)


def preview_cleanup(
    db: sqlite3.Connection,
    workspace: Path,
    *,
    completed_retention_days: int = DEFAULT_COMPLETED_RETENTION_DAYS,
) -> CleanupPreview:
    workspace = workspace.resolve()
    innie_root = workspace / ".innie"
    cutoff = _format_timestamp(datetime.now(timezone.utc) - timedelta(days=completed_retention_days))
    task_rows = db.execute(
        """
        SELECT t.id
        FROM tasks t
        JOIN sessions s ON s.id = t.session_id
        WHERE t.status = 'completed'
          AND t.completed_at IS NOT NULL
          AND t.completed_at < ?
          AND s.locked_by IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM tasks active
            WHERE active.session_id = t.session_id
              AND active.status IN ('created', 'running', 'interrupted')
          )
          AND NOT EXISTS (
            SELECT 1 FROM session_inbox i
            WHERE i.session_id = t.session_id
              AND i.status IN ('queued', 'processing')
          )
        ORDER BY t.completed_at ASC, t.id ASC
        """,
        (cutoff,),
    ).fetchall()
    task_ids = [row["id"] for row in task_rows]
    if not task_ids:
        return CleanupPreview([], 0, 0, 0, 0)

    placeholders = ",".join("?" for _ in task_ids)
    event_count = db.execute(
        f"SELECT COUNT(*) AS count FROM task_events WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchone()["count"]

    artifact_rows = db.execute(
        f"SELECT path FROM artifacts WHERE task_id IN ({placeholders}) ORDER BY id ASC",
        task_ids,
    ).fetchall()
    file_count = 0
    bytes_count = 0
    artifact_count = 0
    for row in artifact_rows:
        path = Path(row["path"]).expanduser()
        if not path.is_absolute():
            path = workspace / path
        path = path.resolve()
        if not _is_relative_to(path, innie_root):
            continue
        artifact_count += 1
        if path.exists() and path.is_file():
            file_count += 1
            bytes_count += path.stat().st_size
    return CleanupPreview(
        task_ids=task_ids,
        event_count=event_count,
        artifact_count=artifact_count,
        file_count=file_count,
        bytes_count=bytes_count,
    )


def apply_cleanup(
    db: sqlite3.Connection,
    workspace: Path,
    *,
    completed_retention_days: int = DEFAULT_COMPLETED_RETENTION_DAYS,
) -> CleanupPreview:
    workspace = workspace.resolve()
    innie_root = workspace / ".innie"
    preview = preview_cleanup(db, workspace, completed_retention_days=completed_retention_days)
    if not preview.task_ids:
        return preview

    placeholders = ",".join("?" for _ in preview.task_ids)
    artifact_rows = db.execute(
        f"SELECT id, session_id, task_id, path FROM artifacts WHERE task_id IN ({placeholders}) ORDER BY id ASC",
        preview.task_ids,
    ).fetchall()
    deletable_artifact_ids: list[int] = []
    for row in artifact_rows:
        path = Path(row["path"]).expanduser()
        if not path.is_absolute():
            path = workspace / path
        path = path.resolve()
        if not _is_relative_to(path, innie_root):
            continue
        if path.exists() and path.is_file():
            path.unlink()
        deletable_artifact_ids.append(row["id"])

    session_ids = [
        row["session_id"]
        for row in db.execute(
            f"SELECT DISTINCT session_id FROM tasks WHERE id IN ({placeholders}) ORDER BY session_id ASC",
            preview.task_ids,
        ).fetchall()
    ]
    if deletable_artifact_ids:
        artifact_placeholders = ",".join("?" for _ in deletable_artifact_ids)
        db.execute(f"DELETE FROM artifacts WHERE id IN ({artifact_placeholders})", deletable_artifact_ids)
    db.execute(f"DELETE FROM task_events WHERE task_id IN ({placeholders})", preview.task_ids)
    db.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", preview.task_ids)
    for session_id in session_ids:
        db.execute(
            """
            INSERT INTO task_events(session_id, event_type, payload_json)
            VALUES(?, 'cleanup.applied', ?)
            """,
            (
                session_id,
                json.dumps(
                    {
                        "deleted_task_ids": preview.task_ids,
                        "event_count": preview.event_count,
                        "artifact_count": preview.artifact_count,
                        "file_count": preview.file_count,
                        "bytes_count": preview.bytes_count,
                    },
                    sort_keys=True,
                ),
            ),
        )
    db.commit()
    return preview


def format_cleanup_preview(preview: CleanupPreview, *, applied: bool = False) -> str:
    action = "Deleted" if applied else "Would delete"
    lines = [
        f"{action} {preview.task_count} completed task(s), {preview.event_count} event(s), "
        f"{preview.artifact_count} artifact row(s), {preview.file_count} file(s), {preview.bytes_count} byte(s).",
    ]
    if preview.task_ids:
        lines.append("task_ids: " + ", ".join(preview.task_ids))
    else:
        lines.append("No cleanup candidates.")
    if not applied:
        lines.append("Dry run only. Pass --apply to delete eligible local state.")
    return "\n".join(lines)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return True
