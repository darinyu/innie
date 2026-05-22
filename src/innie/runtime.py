from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Callable

from .adapters import CodexCliAdapter
from .control import SlackReplyClient
from .db import connect, initialize_schema
from .harness import HarnessAdapter, HarnessEvent, TaskRequest
from .inbox import claim_next_inbox_row, mark_inbox_done
from .progress import SlackProgressRenderer
from .sessions import get_session
from .tasks import (
    TaskRecord,
    append_harness_event,
    create_task,
    record_adapter_capabilities,
    record_artifacts,
    set_task_status,
)


TERMINAL_STATUSES = {"canceled", "completed"}
EventOutput = Callable[[str], None]


class SessionActor:
    def __init__(
        self,
        db: sqlite3.Connection,
        session_id: str,
        *,
        adapters: dict[str, HarnessAdapter],
        slack: SlackReplyClient | None,
        workspace: Path,
        progress: SlackProgressRenderer,
        event_output: EventOutput | None = None,
    ) -> None:
        self._db = db
        self.session_id = session_id
        self._adapters = adapters
        self._slack = slack
        self._workspace = workspace
        self._progress = progress
        self._event_output = event_output
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    async def run_until_idle(self) -> None:
        while not self._cancel_requested:
            row = claim_next_inbox_row(self._db, self.session_id)
            if row is None:
                if _session_status(self._db, self.session_id) != "canceled":
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
            session = get_session(self._db, self.session_id)
            harness_id = session.harness_id or "codex"
            adapter = self._adapters[harness_id]
            task = create_task(
                self._db,
                session_id=self.session_id,
                goal=row.text,
                output_target=session.output_target,
                harness_id=harness_id,
                execution_mode="autonomous",
            )
            record_adapter_capabilities(self._db, harness_id, adapter.capabilities)
            set_task_status(self._db, task.id, "running")
            self._db.commit()

            terminal_status = await self._run_harness_turn(adapter, task, row)
            set_task_status(self._db, task.id, terminal_status)
            if terminal_status == "completed":
                record_artifacts(self._db, task, await adapter.collect_artifacts(task.id))
            mark_inbox_done(self._db, row.id)
            self._db.commit()

        _set_session_status(self._db, self.session_id, "canceled")
        self._db.commit()

    async def _run_harness_turn(self, adapter: HarnessAdapter, task: TaskRecord, row) -> str:
        start_event = HarnessEvent(type="started")
        append_harness_event(self._db, task, start_event)
        self._post_terminal_event(task.id, start_event)
        self._post_progress(task.id, start_event, row)
        self._db.commit()

        await adapter.start_task(
            TaskRequest(
                task_id=task.id,
                session_id=task.session_id,
                goal=task.goal,
                workspace=str(self._workspace),
                output_target=task.output_target,
                execution_mode=task.execution_mode,
                recovery_context={"inbox_id": row.id},
            )
        )
        terminal_status = "completed"
        async for event in adapter.stream_events(task.id):
            if event.type == "started":
                continue
            append_harness_event(self._db, task, event)
            self._post_terminal_event(task.id, event)
            self._post_progress(task.id, event, row)
            if event.type == "failed":
                terminal_status = "failed"
            elif event.type == "canceled":
                terminal_status = "canceled"
            self._db.commit()
        return terminal_status

    def _post_progress(self, task_id: str, event: HarnessEvent, row) -> None:
        if self._slack is None:
            return
        text = self._progress.render(task_id, event)
        if text is None:
            return
        self._slack.post_message(
            channel=row.slack_channel_id,
            thread_ts=row.slack_thread_ts or row.slack_message_ts,
            text=text,
        )

    def _post_terminal_event(self, task_id: str, event: HarnessEvent) -> None:
        if self._event_output is None:
            return
        if event.type == "started":
            self._event_output(f"session {self.session_id} task {task_id} started")
        elif event.type == "completed":
            self._event_output(f"session {self.session_id} task {task_id} completed")


class SessionManager:
    def __init__(
        self,
        db_path: Path,
        *,
        adapters: dict[str, HarnessAdapter] | None = None,
        slack: SlackReplyClient | None = None,
        workspace: Path | None = None,
        event_output: EventOutput | None = None,
    ) -> None:
        self.db_path = db_path
        self.db = connect(db_path)
        initialize_schema(self.db)
        self.adapters = adapters or {"codex": CodexCliAdapter()}
        self.slack = slack
        self.workspace = workspace or db_path.parent.parent
        self.progress = SlackProgressRenderer()
        self.event_output = event_output
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
            self.actors.setdefault(
                row["id"],
                SessionActor(
                    self.db,
                    row["id"],
                    adapters=self.adapters,
                    slack=self.slack,
                    workspace=self.workspace,
                    progress=self.progress,
                    event_output=self.event_output,
                ),
            )
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


def _session_status(db: sqlite3.Connection, session_id: str) -> str:
    row = db.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return "" if row is None else row["status"]


def _append_event(db: sqlite3.Connection, session_id: str, event_type: str, payload: dict) -> None:
    db.execute(
        """
        INSERT INTO task_events(session_id, event_type, payload_json)
        VALUES(?, ?, ?)
        """,
        (session_id, event_type, json.dumps(payload, sort_keys=True)),
    )
