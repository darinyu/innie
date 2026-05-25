from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "access_token",
    "app_token",
    "bot_token",
    "client_secret",
    "cookie",
    "password",
    "refresh_token",
    "secret",
    "slack_app_token",
    "slack_bot_token",
    "token",
}


class SqliteInnieStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def get_overview(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {
                "exists": False,
                "dbPath": str(self.db_path),
                "counts": _empty_counts(),
                "recentSessions": [],
                "latest_event_id": 0,
            }

        with self._connect() as conn:
            counts = {
                "sessions": _scalar(conn, "SELECT COUNT(*) FROM sessions"),
                "running_sessions": _scalar(conn, "SELECT COUNT(*) FROM sessions WHERE status = 'running'"),
                "queued_inputs": _scalar(conn, "SELECT COUNT(*) FROM session_inbox WHERE status = 'queued'"),
                "queued_sessions": _scalar(conn, "SELECT COUNT(DISTINCT session_id) FROM session_inbox WHERE status = 'queued'"),
                "failed_tasks": _scalar(conn, "SELECT COUNT(*) FROM tasks WHERE status = 'failed'"),
                "locked_sessions": _scalar(conn, "SELECT COUNT(*) FROM sessions WHERE locked_by IS NOT NULL"),
                "active_workers": _scalar(
                    conn,
                    """
                    SELECT COUNT(*)
                    FROM sessions
                    WHERE locked_by IS NOT NULL
                      AND (
                        lock_expires_at IS NULL
                        OR lock_expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                      )
                    """,
                ),
                "stale_sessions": _scalar(
                    conn,
                    """
                    SELECT COUNT(*)
                    FROM sessions
                    WHERE locked_by IS NOT NULL
                      AND lock_expires_at IS NOT NULL
                      AND lock_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                ),
            }
            return {
                "exists": True,
                "dbPath": str(self.db_path),
                "counts": counts,
                "recentSessions": self._session_rows(conn, limit=12),
                "latest_event_id": _scalar(conn, "SELECT COALESCE(MAX(id), 0) FROM task_events"),
            }

    def list_sessions(
        self,
        *,
        status: str | None = None,
        harness: str | None = None,
        search: str | None = None,
        updated_after: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"items": [], "next": {"updatedAfter": None}}

        clauses: list[str] = []
        params: list[Any] = []
        if status == "queued":
            clauses.append(
                """EXISTS (
                    SELECT 1 FROM session_inbox i
                    WHERE i.session_id = s.id AND i.status = 'queued'
                )"""
            )
        elif status == "failed":
            clauses.append(
                """(
                    s.status = 'failed'
                    OR EXISTS (
                        SELECT 1 FROM tasks t
                        WHERE t.session_id = s.id AND t.status = 'failed'
                    )
                )"""
            )
        elif status and status != "all":
            clauses.append("s.status = ?")
            params.append(status)
        if harness and harness != "all":
            clauses.append("s.harness_id = ?")
            params.append(harness)
        if updated_after:
            clauses.append("s.updated_at > ?")
            params.append(updated_after)
        if search:
            clauses.append(
                """(
                    s.id LIKE ?
                    OR s.output_target LIKE ?
                    OR s.harness_id LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM tasks t
                        WHERE t.session_id = s.id AND t.goal LIKE ?
                    )
                    OR EXISTS (
                        SELECT 1 FROM session_inbox i
                        WHERE i.session_id = s.id AND i.text LIKE ?
                    )
                )"""
            )
            token = f"%{search}%"
            params.extend([token, token, token, token, token])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        query = _session_query(where)

        with self._connect() as conn:
            items = [_decode_payloads(dict(row)) for row in conn.execute(query, params).fetchall()]

        next_updated = max((row["updated_at"] for row in items), default=updated_after)
        return {"items": items, "next": {"updatedAfter": next_updated}}

    def get_session_detail(self, session_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise KeyError(session_id)
            return {
                "session": dict(session),
                "worker": _worker_summary(conn, session_id),
                "tasks": _rows(conn, "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at ASC", session_id),
                "inbox": _decoded_rows(
                    conn,
                    "SELECT * FROM session_inbox WHERE session_id = ? ORDER BY id ASC",
                    session_id,
                ),
                "task_events": _decoded_rows(
                    conn,
                    "SELECT * FROM task_events WHERE session_id = ? ORDER BY id ASC",
                    session_id,
                ),
                "hook_events": _decoded_rows(
                    conn,
                    "SELECT * FROM hook_events WHERE session_id = ? ORDER BY id ASC",
                    session_id,
                ),
                "artifacts": _decoded_rows(
                    conn,
                    "SELECT * FROM artifacts WHERE session_id = ? ORDER BY id ASC",
                    session_id,
                ),
            }

    def list_events(self, *, after_id: int = 0, limit: int = 250) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"items": [], "next": {"lastEventId": after_id}}
        with self._connect() as conn:
            items = _decoded_rows(
                conn,
                "SELECT * FROM task_events WHERE id > ? ORDER BY id ASC LIMIT ?",
                after_id,
                limit,
            )
        return {"items": items, "next": {"lastEventId": max((row["id"] for row in items), default=after_id)}}

    def list_session_events(self, session_id: str, *, after_id: int = 0, limit: int = 250) -> dict[str, Any]:
        with self._connect() as conn:
            items = _decoded_rows(
                conn,
                "SELECT * FROM task_events WHERE session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                session_id,
                after_id,
                limit,
            )
        return {"items": items, "next": {"lastEventId": max((row["id"] for row in items), default=after_id)}}

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 1000")
        return conn

    def _session_rows(self, conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
        return [_decode_payloads(dict(row)) for row in conn.execute(_session_query(""), (limit,)).fetchall()]


def _session_query(where: str) -> str:
    return f"""
        SELECT
            s.*,
            (
                SELECT goal FROM tasks t
                WHERE t.session_id = s.id
                ORDER BY t.created_at DESC
                LIMIT 1
            ) AS latest_task_goal,
            (
                SELECT status FROM tasks t
                WHERE t.session_id = s.id
                ORDER BY t.created_at DESC
                LIMIT 1
            ) AS latest_task_status,
            (
                SELECT text FROM session_inbox i
                WHERE i.session_id = s.id
                ORDER BY i.created_at DESC, i.id DESC
                LIMIT 1
            ) AS latest_user_message,
            (
                SELECT COUNT(*) FROM session_inbox i
                WHERE i.session_id = s.id AND i.status = 'queued'
            ) AS queued_inputs,
            CASE
                WHEN s.locked_by IS NULL THEN 'idle'
                WHEN s.lock_expires_at IS NOT NULL
                 AND s.lock_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now') THEN 'stale'
                ELSE 'active'
            END AS lock_state,
            (
                SELECT event_type FROM task_events e
                WHERE e.session_id = s.id
                ORDER BY e.id DESC
                LIMIT 1
            ) AS last_event_type,
            (
                SELECT event_type FROM task_events e
                WHERE e.session_id = s.id AND e.event_type LIKE 'worker.%'
                ORDER BY e.id DESC
                LIMIT 1
            ) AS latest_worker_event_type,
            (
                SELECT payload_json FROM task_events e
                WHERE e.session_id = s.id AND e.event_type LIKE 'worker.%'
                ORDER BY e.id DESC
                LIMIT 1
            ) AS latest_worker_event_payload_json,
            (
                SELECT id FROM task_events e
                WHERE e.session_id = s.id
                ORDER BY e.id DESC
                LIMIT 1
            ) AS last_event_id
        FROM sessions s
        {where}
        ORDER BY s.updated_at DESC
        LIMIT ?
    """


def _scalar(conn: sqlite3.Connection, query: str, *params: Any) -> int:
    row = conn.execute(query, params).fetchone()
    return int(row[0] if row else 0)


def _rows(conn: sqlite3.Connection, query: str, *params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _decoded_rows(conn: sqlite3.Connection, query: str, *params: Any) -> list[dict[str, Any]]:
    return [_decode_payloads(row) for row in _rows(conn, query, *params)]


def _decode_payloads(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("payload_json", "metadata_json"):
        if key in row:
            try:
                decoded = _redact(json.loads(row[key] or "{}"))
                row[key.removesuffix("_json")] = decoded
                row[key] = json.dumps(decoded, sort_keys=True)
            except json.JSONDecodeError:
                row[key.removesuffix("_json")] = {"raw": row[key]}
    return row


def _worker_summary(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    session = conn.execute(
        "SELECT locked_by, locked_at, lock_expires_at, status FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    queue_depth = _scalar(
        conn,
        "SELECT COUNT(*) FROM session_inbox WHERE session_id = ? AND status = 'queued'",
        session_id,
    )
    current_task = conn.execute(
        """
        SELECT id, status, goal
        FROM tasks
        WHERE session_id = ? AND status = 'running'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    latest_event = conn.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM task_events
        WHERE session_id = ? AND event_type LIKE 'worker.%'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    lock_state = "idle"
    if session is not None and session["locked_by"]:
        lock_state = "active"
        expires_at = session["lock_expires_at"]
        if expires_at is not None:
            stale = _scalar(
                conn,
                "SELECT ? <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                expires_at,
            )
            if stale:
                lock_state = "stale"
    return {
        "status": lock_state,
        "queue_depth": queue_depth,
        "lock_owner": None if session is None else session["locked_by"],
        "locked_at": None if session is None else session["locked_at"],
        "lock_expires_at": None if session is None else session["lock_expires_at"],
        "current_task_id": None if current_task is None else current_task["id"],
        "current_task_status": None if current_task is None else current_task["status"],
        "latest_event": None if latest_event is None else _decode_payloads(dict(latest_event)),
    }


def _empty_counts() -> dict[str, int]:
    return {
        "sessions": 0,
        "running_sessions": 0,
        "queued_inputs": 0,
        "queued_sessions": 0,
        "failed_tasks": 0,
        "locked_sessions": 0,
        "active_workers": 0,
        "stale_sessions": 0,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            if key.lower() in SENSITIVE_KEYS:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact(child)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
