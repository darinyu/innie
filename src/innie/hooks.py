from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import time
from typing import Protocol

from .slack_events import SlackTrigger


class SlackReactionClient(Protocol):
    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        ...


@dataclass(frozen=True)
class HookResult:
    hook_name: str
    status: str
    duration_ms: int
    skipped: bool = False


def run_trigger_accepted_hook(
    db: sqlite3.Connection,
    *,
    trigger: SlackTrigger,
    slack: SlackReactionClient,
    session_id: str | None = None,
) -> HookResult:
    hook_name = "trigger.accepted"
    reaction_ts = trigger.message_ts
    dedupe_key = f"{hook_name}:{trigger.event_id}:{trigger.channel_id}:{reaction_ts}"
    existing = db.execute(
        "SELECT status, duration_ms FROM hook_events WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    if existing and existing["status"] == "ok":
        return HookResult(hook_name=hook_name, status="ok", duration_ms=existing["duration_ms"], skipped=True)

    started = time.monotonic()
    if _reaction_should_be_skipped(db, trigger=trigger, session_id=session_id):
        duration_ms = int((time.monotonic() - started) * 1000)
        payload = {
            "slack_event_id": trigger.event_id,
            "channel": trigger.channel_id,
            "timestamp": reaction_ts,
            "skipped_reason": "user_mention_private_mode",
        }
        db.execute(
            """
            INSERT OR REPLACE INTO hook_events(
                session_id,
                hook_name,
                dedupe_key,
                status,
                duration_ms,
                payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                hook_name,
                dedupe_key,
                "skipped",
                duration_ms,
                json.dumps(payload, sort_keys=True),
            ),
        )
        return HookResult(hook_name=hook_name, status="skipped", duration_ms=duration_ms, skipped=True)

    payload = {
        "slack_event_id": trigger.event_id,
        "channel": trigger.channel_id,
        "timestamp": reaction_ts,
        "reaction": "eyes",
    }
    try:
        slack.add_reaction(channel=trigger.channel_id, timestamp=reaction_ts, name="eyes")
        status = "ok"
    except Exception as exc:  # pragma: no cover - exercised through test failure path
        status = "error"
        payload["error"] = str(exc)
    duration_ms = int((time.monotonic() - started) * 1000)
    db.execute(
        """
        INSERT OR REPLACE INTO hook_events(
            session_id,
            hook_name,
            dedupe_key,
            status,
            duration_ms,
            payload_json
        )
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            hook_name,
            dedupe_key,
            status,
            duration_ms,
            json.dumps(payload, sort_keys=True),
        ),
    )
    return HookResult(hook_name=hook_name, status=status, duration_ms=duration_ms)


def _reaction_should_be_skipped(db: sqlite3.Connection, *, trigger: SlackTrigger, session_id: str | None) -> bool:
    if trigger.trigger_type == "user_mention":
        return True
    if trigger.trigger_type != "thread_reply" or session_id is None:
        return False
    row = db.execute("SELECT trigger_type FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row is not None and row["trigger_type"] == "user_mention"
