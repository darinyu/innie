from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any

from .hooks import SlackReactionClient, run_trigger_accepted_hook
from .inbox import InboxRow, enqueue_trigger
from .sessions import SessionRecord, resolve_session_for_trigger
from .slack_events import SlackEventDecision, normalize_slack_event, persist_trigger


@dataclass(frozen=True)
class AcceptedSlackEvent:
    decision: SlackEventDecision
    session: SessionRecord | None = None
    inbox: InboxRow | None = None


def accept_slack_event(
    db: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    bot_user_id: str,
    slack: SlackReactionClient,
    harness_id: str | None = None,
    watched_user_id: str | None = None,
) -> AcceptedSlackEvent:
    seen = {
        row["slack_event_id"]
        for row in db.execute("SELECT slack_event_id FROM slack_triggers")
    }
    decision = normalize_slack_event(
        payload,
        bot_user_id=bot_user_id,
        watched_user_id=watched_user_id,
        seen_event_ids=seen,
    )
    if not decision.accepted or decision.trigger is None:
        return AcceptedSlackEvent(decision)

    persist_trigger(db, decision.trigger)
    session = resolve_session_for_trigger(db, decision.trigger, harness_id=harness_id)
    inbox = enqueue_trigger(db, session=session, trigger=decision.trigger)
    db.commit()

    run_trigger_accepted_hook(db, trigger=decision.trigger, slack=slack, session_id=session.id)
    db.commit()
    return AcceptedSlackEvent(decision, session=session, inbox=inbox)
