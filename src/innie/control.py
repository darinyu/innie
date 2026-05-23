from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Protocol


class SlackReplyClient(Protocol):
    def post_message(
        self,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> str | None:
        ...

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        ...

    def delete_message(self, *, channel: str, ts: str) -> None:
        ...


@dataclass(frozen=True)
class ControlResult:
    handled: bool
    action: str | None
    text: str | None


def summarize_session(db: sqlite3.Connection, session_id: str) -> str:
    session = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        return f"Session {session_id} not found."
    queued = db.execute(
        "SELECT COUNT(*) AS count FROM session_inbox WHERE session_id = ? AND status = 'queued'",
        (session_id,),
    ).fetchone()["count"]
    last_event = db.execute(
        """
        SELECT event_type, created_at
        FROM task_events
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    last_event_text = "none" if last_event is None else f"{last_event['event_type']} at {last_event['created_at']}"
    current_task = db.execute(
        """
        SELECT id, status, harness_id
        FROM tasks
        WHERE session_id = ? AND status NOT IN ('completed', 'failed', 'canceled')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    current_task_text = (
        "none"
        if current_task is None
        else f"{current_task['id']} {current_task['status']} via {current_task['harness_id']}"
    )
    return (
        f"Innie session {session_id}\n"
        f"status: {session['status']}\n"
        f"queued_inputs: {queued}\n"
        f"current_task: {current_task_text}\n"
        f"last_event: {last_event_text}\n"
        f"output_target: {session['output_target']}"
    )


def cancel_session(db: sqlite3.Connection, session_id: str) -> str:
    session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        return f"Session {session_id} not found."
    db.execute(
        """
        UPDATE sessions
        SET status = 'canceled',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (session_id,),
    )
    db.execute(
        """
        UPDATE session_inbox
        SET status = 'canceled',
            processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE session_id = ? AND status IN ('queued', 'processing')
        """,
        (session_id,),
    )
    db.execute(
        """
        INSERT INTO task_events(session_id, event_type, payload_json)
        VALUES(?, 'session.canceled', '{}')
        """,
        (session_id,),
    )
    db.commit()
    return f"Canceled Innie session {session_id}."


def handle_control_message(
    db: sqlite3.Connection,
    *,
    session_id: str,
    text: str,
    slack: SlackReplyClient,
    channel: str,
    thread_ts: str,
) -> ControlResult:
    command = text.strip().lower()
    if command not in {"status", "cancel"}:
        return ControlResult(False, None, None)
    if command == "status":
        response = summarize_session(db, session_id)
    else:
        response = cancel_session(db, session_id)
    slack.post_message(channel=channel, thread_ts=thread_ts, text=response)
    return ControlResult(True, command, response)
