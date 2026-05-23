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
        self._progress_messages: dict[str, tuple[str, str]] = {}
        self._progress_details: dict[str, list[str]] = {}
        self._progress_summaries: dict[str, str] = {}

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

        terminal_status = "completed"
        try:
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
        except Exception as exc:
            terminal_status = "failed"
            failed_event = HarnessEvent(type="failed", message=str(exc) or exc.__class__.__name__)
            append_harness_event(self._db, task, failed_event)
            self._post_terminal_event(task.id, failed_event)
            self._post_progress(task.id, failed_event, row)
            self._db.commit()
        return terminal_status

    def _post_progress(self, task_id: str, event: HarnessEvent, row) -> None:
        if self._slack is None:
            return
        detail_line = self._progress.detail_line(event)
        if detail_line is not None:
            self._progress_details.setdefault(task_id, []).append(detail_line)
            self._progress_summaries[task_id] = detail_line
        progress_details = self._progress_details.get(task_id, [])
        if event.type in {"output", "failed", "canceled"}:
            final_messages = self._progress.render_final_messages(task_id, event, progress_details)
            self._replace_progress_message_or_post_final(
                task_id,
                channel=row.slack_channel_id,
                thread_ts=row.slack_thread_ts or row.slack_message_ts,
                messages=final_messages,
            )
            self._progress_details.pop(task_id, None)
            self._progress_summaries.pop(task_id, None)
            return
        else:
            rendered = self._progress.render_widget(task_id, event)
            if event.type in {"tool_use", "tool_result", "usage"} and rendered is not None:
                rendered = self._progress.with_progress_summary(rendered, self._progress_summaries.get(task_id))
        if event.type == "completed":
            self._delete_progress_message(task_id)
        if rendered is None:
            if event.type in {"failed", "canceled", "completed"}:
                self._progress_details.pop(task_id, None)
                self._progress_summaries.pop(task_id, None)
            return
        if event.type in {"progress", "tool_use", "tool_result", "usage"}:
            self._upsert_progress_message(
                task_id,
                channel=row.slack_channel_id,
                thread_ts=row.slack_thread_ts or row.slack_message_ts,
                text=rendered.text,
                blocks=rendered.blocks,
            )
            return
        self._slack.post_message(
            channel=row.slack_channel_id,
            thread_ts=row.slack_thread_ts or row.slack_message_ts,
            text=rendered.text,
            blocks=rendered.blocks,
        )

    def _replace_progress_message_or_post_final(
        self,
        task_id: str,
        *,
        channel: str,
        thread_ts: str,
        messages: list,
    ) -> None:
        if self._slack is None:
            return
        if not messages:
            return
        current = self._progress_messages.pop(task_id, None)
        if current is None:
            self._post_final_messages(channel=channel, thread_ts=thread_ts, messages=messages, task_id=task_id)
            return
        current_channel, ts = current
        first = messages[0]
        try:
            self._slack.update_message(channel=current_channel, ts=ts, text=first.text, blocks=first.blocks)
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack final updated ts={ts}")
            self._post_final_messages(channel=channel, thread_ts=thread_ts, messages=messages[1:], task_id=task_id)
        except Exception as exc:
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack final update failed: {exc}")
            self._delete_slack_message(current_channel, ts, task_id, "progress")
            self._post_final_messages(channel=channel, thread_ts=thread_ts, messages=messages, task_id=task_id, fallback=True)

    def _post_final_messages(self, *, channel: str, thread_ts: str, messages: list, task_id: str | None = None, fallback: bool = False) -> None:
        for message in messages:
            try:
                self._slack.post_message(channel=channel, thread_ts=thread_ts, text=message.text, blocks=message.blocks)
                if task_id is not None:
                    label = "slack final fallback posted" if fallback else "slack final posted"
                    self._post_terminal_line(f"session {self.session_id} task {task_id} {label}")
            except Exception as exc:
                if task_id is not None:
                    label = "fallback" if fallback else "post"
                    self._post_terminal_line(f"session {self.session_id} task {task_id} slack final {label} failed: {exc}")

    def _upsert_progress_message(
        self,
        task_id: str,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: list[dict] | None,
    ) -> None:
        if self._slack is None:
            return
        current = self._progress_messages.get(task_id)
        if current is None:
            try:
                ts = self._slack.post_message(channel=channel, thread_ts=thread_ts, text=text, blocks=blocks)
            except Exception as exc:
                self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress post failed: {exc}")
                return
            if ts:
                self._progress_messages[task_id] = (channel, ts)
                self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress posted ts={ts}")
            return
        current_channel, ts = current
        try:
            self._slack.update_message(channel=current_channel, ts=ts, text=text, blocks=blocks)
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress updated ts={ts}")
        except Exception as exc:
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress update failed ts={ts}: {exc}")
            self._progress_messages.pop(task_id, None)
            self._delete_slack_message(current_channel, ts, task_id, "progress")

    def _delete_progress_message(self, task_id: str) -> None:
        if self._slack is None:
            return
        current = self._progress_messages.pop(task_id, None)
        if current is None:
            return
        channel, ts = current
        self._delete_slack_message(channel, ts, task_id, "progress")

    def _delete_slack_message(self, channel: str, ts: str, task_id: str, label: str) -> None:
        if self._slack is None:
            return
        try:
            self._slack.delete_message(channel=channel, ts=ts)
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack {label} deleted ts={ts}")
        except Exception as exc:
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack {label} delete failed ts={ts}: {exc}")

    def _post_terminal_event(self, task_id: str, event: HarnessEvent) -> None:
        if event.type == "started":
            self._post_terminal_line(f"session {self.session_id} task {task_id} started")
        elif event.type == "completed":
            self._post_terminal_line(f"session {self.session_id} task {task_id} completed")
        elif event.type in {"progress", "tool_use", "tool_result", "output", "failed", "canceled"}:
            self._post_terminal_line(_format_terminal_event(self.session_id, task_id, event))
        elif event.type == "usage" and event.usage is not None:
            self._post_terminal_line(
                f"session {self.session_id} task {task_id} usage: "
                f"{event.usage.input_tokens} input, {event.usage.output_tokens} output"
            )

    def _post_terminal_line(self, line: str) -> None:
        if self._event_output is not None:
            self._event_output(line)


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


def _format_terminal_event(session_id: str, task_id: str, event: HarnessEvent) -> str:
    label = event.type
    if event.type in {"tool_use", "tool_result"}:
        tool_name = str(event.payload.get("tool_name") or event.payload.get("tool") or "tool")
        label = f"{event.type} {tool_name}"
    message = _preview_terminal(event.message or "")
    suffix = f": {message}" if message else ""
    return f"session {session_id} task {task_id} {label}{suffix}"


def _preview_terminal(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
