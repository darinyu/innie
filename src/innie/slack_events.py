from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Any


@dataclass(frozen=True)
class SlackTrigger:
    event_id: str
    trigger_type: str
    channel_id: str
    message_ts: str
    thread_ts: str | None
    sender_user_id: str | None
    text: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SlackEventDecision:
    accepted: bool
    reason: str
    trigger: SlackTrigger | None = None


def normalize_slack_event(
    payload: dict[str, Any],
    *,
    bot_user_id: str,
    watched_user_id: str | None = None,
    seen_event_ids: set[str] | None = None,
    known_thread_roots: set[tuple[str, str]] | None = None,
) -> SlackEventDecision:
    event_id = str(payload.get("event_id") or "")
    if not event_id:
        return SlackEventDecision(False, "missing_event_id")
    if seen_event_ids and event_id in seen_event_ids:
        return SlackEventDecision(False, "duplicate_retry")

    event = payload.get("event") or {}
    if not isinstance(event, dict):
        return SlackEventDecision(False, "missing_event")

    event_type = str(event.get("type") or "")
    subtype = event.get("subtype")
    user_id = event.get("user")
    if user_id == bot_user_id or event.get("bot_id") or subtype == "bot_message":
        return SlackEventDecision(False, "self_echo")

    channel_id = str(event.get("channel") or "")
    message_ts = str(event.get("ts") or "")
    text = str(event.get("text") or "")
    thread_ts = event.get("thread_ts")
    if not channel_id or not message_ts:
        return SlackEventDecision(False, "missing_channel_or_ts")

    thread_root = str(thread_ts) if thread_ts else None
    if (
        event_type == "message"
        and thread_root
        and known_thread_roots is not None
        and (channel_id, thread_root) in known_thread_roots
    ):
        trigger_type = "thread_reply"
    elif event_type == "message" and watched_user_id and f"<@{watched_user_id}>" in text:
        trigger_type = "user_mention"
    else:
        return SlackEventDecision(False, "not_for_innie")

    trigger = SlackTrigger(
        event_id=event_id,
        trigger_type=trigger_type,
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=str(thread_ts) if thread_ts else None,
        sender_user_id=str(user_id) if user_id else None,
        text=text,
        payload=payload,
    )
    return SlackEventDecision(True, "accepted", trigger)


def persist_trigger(db: sqlite3.Connection, trigger: SlackTrigger) -> None:
    db.execute(
        """
        INSERT OR IGNORE INTO slack_triggers(
            slack_event_id,
            trigger_type,
            slack_channel_id,
            slack_message_ts,
            slack_thread_ts,
            sender_user_id,
            text,
            payload_json
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trigger.event_id,
            trigger.trigger_type,
            trigger.channel_id,
            trigger.message_ts,
            trigger.thread_ts,
            trigger.sender_user_id,
            trigger.text,
            json.dumps(trigger.payload, sort_keys=True),
        ),
    )
