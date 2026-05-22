from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

from .db import connect, initialize_schema
from .inbox import claim_next_inbox_row, mark_inbox_done


TERMINAL_STATUSES = {"canceled", "completed"}


class SessionActor:
    def __init__(self, db: sqlite3.Connection, session_id: str) -> None:
        self._db = db
        self.session_id = session_id
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    async def run_until_idle(self) -> None:
        while not self._cancel_requested:
            row = claim_next_inbox_row(self._db, self.session_id)
            if row is None:
                _set_session_status(self._db, self.session_id, "idle")
                self._db.commit()
                return

            _set_session_status(self._db, self.session_id, "running")
            _append_event(
                self._db,
                self.session_id,
                "actor.input.claimed",
                {"inbox_id": row.id, "text": row.text},
            )
            await asyncio.sleep(0)
            _append_event(
                self._db,
                self.session_id,
                "harness.placeholder.output",
                {
                    "inbox_id": row.id,
                    "summary": "placeholder agent work completed",
                },
            )
            mark_inbox_done(self._db, row.id)
            self._db.commit()

        _set_session_status(self._db, self.session_id, "canceled")
        self._db.commit()


class SessionManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db = connect(db_path)
        initialize_schema(self.db)
        self.actors: dict[str, SessionActor] = {}

    def close(self) -> None:
        self.db.close()

    def hydrate(self) -> list[str]:
        rows = self.db.execute(
            """
            SELECT DISTINCT s.id
            FROM sessions s
            LEFT JOIN session_inbox i ON i.session_id = s.id
            WHERE s.status NOT IN ('canceled', 'completed')
              AND (i.status = 'queued' OR s.status IN ('new', 'running'))
            ORDER BY s.created_at ASC
            """
        ).fetchall()
        for row in rows:
            self.actors.setdefault(row["id"], SessionActor(self.db, row["id"]))
        return list(self.actors)

    async def run_until_idle(self) -> None:
        self.hydrate()
        while True:
            active = [actor.run_until_idle() for actor in self.actors.values()]
            if not active:
                return
            await asyncio.gather(*active)
            self.actors.clear()
            self.hydrate()
            if not self.actors:
                return


def _set_session_status(db: sqlite3.Connection, session_id: str, status: str) -> None:
    db.execute(
        """
        UPDATE sessions
        SET status = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (status, session_id),
    )


def _append_event(db: sqlite3.Connection, session_id: str, event_type: str, payload: dict) -> None:
    db.execute(
        """
        INSERT INTO task_events(session_id, event_type, payload_json)
        VALUES(?, ?, ?)
        """,
        (session_id, event_type, json.dumps(payload, sort_keys=True)),
    )
