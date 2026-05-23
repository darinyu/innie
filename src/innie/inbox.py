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
