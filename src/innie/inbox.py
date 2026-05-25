from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from .sessions import SessionRecord
from .slack_events import SlackTrigger


@dataclass(frozen=True)
class InboxRow:
    id: int
    session_id: str
    slack_event_id: str | None
    slack_channel_id: str
    slack_message_ts: str
    slack_thread_ts: str | None
    status: str
    text: str


def enqueue_trigger(db: sqlite3.Connection, *, session: SessionRecord, trigger: SlackTrigger) -> InboxRow:
    db.execute(
        """
        INSERT OR IGNORE INTO session_inbox(
            session_id,
            slack_event_id,
            slack_channel_id,
            slack_message_ts,
            slack_thread_ts,
            sender_user_id,
            text,
            payload_json,
            status
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'queued')
        """,
        (
            session.id,
            trigger.event_id,
            trigger.channel_id,
            trigger.message_ts,
            trigger.thread_ts,
            trigger.sender_user_id,
            trigger.text,
            json.dumps(trigger.payload, sort_keys=True),
        ),
    )
    return _row_for_event(db, session.id, trigger.event_id)


def find_row_for_trigger_message(db: sqlite3.Connection, *, trigger: SlackTrigger) -> InboxRow | None:
    row = db.execute(
        """
        SELECT i.*
        FROM session_inbox i
        JOIN sessions s ON s.id = i.session_id
        WHERE s.slack_channel_id = ?
          AND s.slack_root_ts = ?
          AND i.slack_channel_id = ?
          AND i.slack_message_ts = ?
        """,
        (
            trigger.channel_id,
            trigger.thread_ts or trigger.message_ts,
            trigger.channel_id,
            trigger.message_ts,
        ),
    ).fetchone()
    return _to_inbox_row(row) if row is not None else None


def queued_inbox_rows(db: sqlite3.Connection, session_id: str) -> list[InboxRow]:
    rows = db.execute(
        """
        SELECT * FROM session_inbox
        WHERE session_id = ? AND status = 'queued'
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    return [_to_inbox_row(row) for row in rows]


def claim_next_inbox_row(db: sqlite3.Connection, session_id: str) -> InboxRow | None:
    row = db.execute(
        """
        SELECT * FROM session_inbox
        WHERE session_id = ? AND status = 'queued'
        ORDER BY id ASC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    db.execute(
        """
        UPDATE session_inbox
        SET status = 'processing'
        WHERE id = ? AND status = 'queued'
        """,
        (row["id"],),
    )
    return _to_inbox_row(db.execute("SELECT * FROM session_inbox WHERE id = ?", (row["id"],)).fetchone())


def queued_session_ids(db: sqlite3.Connection, *, limit: int | None = None) -> list[str]:
    query = """
        SELECT session_id, MIN(id) AS first_inbox_id
        FROM session_inbox
        WHERE status = 'queued'
        GROUP BY session_id
        ORDER BY first_inbox_id ASC
    """
    params: tuple[int, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    return [row["session_id"] for row in db.execute(query, params).fetchall()]


def acquire_session_lock(
    db: sqlite3.Connection,
    session_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> bool:
    return (
        db.execute(
            """
            UPDATE sessions
            SET locked_by = ?,
                locked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                lock_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?),
                status = 'running',
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
              AND (
                locked_by IS NULL
                OR locked_by = ?
                OR lock_expires_at IS NULL
                OR lock_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
              )
            """,
            (worker_id, f"+{lease_seconds} seconds", session_id, worker_id),
        ).rowcount
        == 1
    )


def claim_next_available_inbox_row(
    db: sqlite3.Connection,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> InboxRow | None:
    row = db.execute(
        """
        SELECT i.*
        FROM session_inbox i
        JOIN sessions s ON s.id = i.session_id
        WHERE i.status = 'queued'
          AND (
            s.locked_by IS NULL
            OR s.lock_expires_at IS NULL
            OR s.lock_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
          )
        ORDER BY i.id ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None

    updated_session = db.execute(
        """
        UPDATE sessions
        SET locked_by = ?,
            locked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            lock_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?),
            status = 'running',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
          AND (
            locked_by IS NULL
            OR lock_expires_at IS NULL
            OR lock_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
          )
        """,
        (worker_id, f"+{lease_seconds} seconds", row["session_id"]),
    ).rowcount
    if updated_session != 1:
        return None

    updated_inbox = db.execute(
        """
        UPDATE session_inbox
        SET status = 'processing'
        WHERE id = ? AND status = 'queued'
        """,
        (row["id"],),
    ).rowcount
    if updated_inbox != 1:
        release_session_lock(db, row["session_id"], worker_id=worker_id)
        return None

    claimed = db.execute("SELECT * FROM session_inbox WHERE id = ?", (row["id"],)).fetchone()
    return _to_inbox_row(claimed)


def renew_session_lock(
    db: sqlite3.Connection,
    session_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> bool:
    return (
        db.execute(
            """
            UPDATE sessions
            SET lock_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND locked_by = ?
            """,
            (f"+{lease_seconds} seconds", session_id, worker_id),
        ).rowcount
        == 1
    )


def release_session_lock(db: sqlite3.Connection, session_id: str, *, worker_id: str | None = None) -> None:
    if worker_id is None:
        db.execute(
            """
            UPDATE sessions
            SET locked_by = NULL,
                locked_at = NULL,
                lock_expires_at = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (session_id,),
        )
        return
    db.execute(
        """
        UPDATE sessions
        SET locked_by = NULL,
            locked_at = NULL,
            lock_expires_at = NULL,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ? AND locked_by = ?
        """,
        (session_id, worker_id),
    )


def mark_inbox_done(db: sqlite3.Connection, inbox_id: int) -> None:
    db.execute(
        """
        UPDATE session_inbox
        SET status = 'done',
            processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (inbox_id,),
    )


def _row_for_event(db: sqlite3.Connection, session_id: str, event_id: str) -> InboxRow:
    row = db.execute(
        "SELECT * FROM session_inbox WHERE session_id = ? AND slack_event_id = ?",
        (session_id, event_id),
    ).fetchone()
    if row is None:
        raise KeyError(event_id)
    return _to_inbox_row(row)


def _to_inbox_row(row: sqlite3.Row) -> InboxRow:
    return InboxRow(
        id=row["id"],
        session_id=row["session_id"],
        slack_event_id=row["slack_event_id"],
        slack_channel_id=row["slack_channel_id"],
        slack_message_ts=row["slack_message_ts"],
        slack_thread_ts=row["slack_thread_ts"],
        status=row["status"],
        text=row["text"],
    )
