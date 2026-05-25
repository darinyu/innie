from __future__ import annotations

import json
import sqlite3


def set_session_status(db: sqlite3.Connection, session_id: str, status: str) -> None:
    db.execute(
        """
        UPDATE sessions
        SET status = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (status, session_id),
    )


def session_status(db: sqlite3.Connection, session_id: str) -> str:
    row = db.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return "" if row is None else row["status"]


def session_lock_expires_at(db: sqlite3.Connection, session_id: str) -> str | None:
    row = db.execute("SELECT lock_expires_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return None if row is None else row["lock_expires_at"]


def append_event(db: sqlite3.Connection, session_id: str, event_type: str, payload: dict) -> None:
    db.execute(
        """
        INSERT INTO task_events(session_id, event_type, payload_json)
        VALUES(?, ?, ?)
        """,
        (session_id, event_type, json.dumps(payload, sort_keys=True)),
    )


def append_runtime_event(
    db: sqlite3.Connection,
    session_id: str,
    task_id: str | None,
    event_type: str,
    payload: dict,
) -> None:
    db.execute(
        """
        INSERT INTO task_events(session_id, task_id, event_type, payload_json)
        VALUES(?, ?, ?, ?)
        """,
        (session_id, task_id, event_type, json.dumps(payload, sort_keys=True)),
    )
