from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3

from .slack_events import SlackTrigger


@dataclass(frozen=True)
class SessionRecord:
    id: str
    slack_channel_id: str
    slack_root_ts: str
    slack_thread_ts: str
    trigger_type: str
    output_target: str
    status: str
    harness_id: str | None


def root_ts_for_trigger(trigger: SlackTrigger) -> str:
    return trigger.thread_ts or trigger.message_ts


def session_id_for_slack(channel_id: str, root_ts: str) -> str:
    digest = hashlib.sha256(f"{channel_id}:{root_ts}".encode("utf-8")).hexdigest()[:16]
    return f"sess_{digest}"


def resolve_session_for_trigger(
    db: sqlite3.Connection,
    trigger: SlackTrigger,
    *,
    harness_id: str | None = None,
) -> SessionRecord:
    root_ts = root_ts_for_trigger(trigger)
    session_id = session_id_for_slack(trigger.channel_id, root_ts)
    output_target = f"slack:{trigger.channel_id}:{root_ts}"
    db.execute(
        """
        INSERT INTO sessions(
            id,
            slack_channel_id,
            slack_root_ts,
            slack_thread_ts,
            trigger_type,
            output_target,
            status,
            harness_id
        )
        VALUES(?, ?, ?, ?, ?, ?, 'new', ?)
        ON CONFLICT(slack_channel_id, slack_root_ts) DO UPDATE SET
            slack_thread_ts = excluded.slack_thread_ts,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (
            session_id,
            trigger.channel_id,
            root_ts,
            trigger.thread_ts,
            trigger.trigger_type,
            output_target,
            harness_id,
        ),
    )
    db.execute(
        "UPDATE slack_triggers SET session_id = ? WHERE slack_event_id = ?",
        (session_id, trigger.event_id),
    )
    return get_session(db, session_id)


def get_session(db: sqlite3.Connection, session_id: str) -> SessionRecord:
    row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise KeyError(session_id)
    return SessionRecord(
        id=row["id"],
        slack_channel_id=row["slack_channel_id"],
        slack_root_ts=row["slack_root_ts"],
        slack_thread_ts=row["slack_thread_ts"],
        trigger_type=row["trigger_type"],
        output_target=row["output_target"],
        status=row["status"],
        harness_id=row["harness_id"],
    )
