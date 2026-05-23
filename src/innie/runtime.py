from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Callable
import uuid

from .adapters import CodexCliAdapter
from .control import SlackReplyClient
from .db import connect, initialize_schema
from .harness import HarnessAdapter, HarnessEvent, TaskRequest
from .inbox import (
    claim_next_available_inbox_row,
    claim_next_inbox_row,
    mark_inbox_done,
    release_session_lock,
    renew_session_lock,
)
from .progress import SlackProgressRenderer
from .sessions import get_session
from .sessions import set_harness_resume_id
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

    async def run_claimed_row(self, row, *, worker_id: str, run_id: str) -> None:
        lock_expires_at = _session_lock_expires_at(self._db, self.session_id)
        _append_event(
            self._db,
            self.session_id,
            "worker.inbox.claimed",
            {
                "run_id": run_id,
                "worker_id": worker_id,
                "session_id": self.session_id,
                "inbox_id": row.id,
                "text": row.text,
                "lock_expires_at": lock_expires_at,
                "status": "processing",
            },
        )
        session = get_session(self._db, self.session_id)
        harness_id = session.harness_id or "codex"
        adapter = self._adapters[harness_id]
        task: TaskRecord | None = None
        terminal_status = "failed"
        stop_renewal = asyncio.Event()
        renewal_task = asyncio.create_task(
            self._renew_session_lock_until_stopped(worker_id=worker_id, run_id=run_id, stop=stop_renewal)
        )
        try:
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
            if terminal_status == "completed":
                record_artifacts(self._db, task, await adapter.collect_artifacts(task.id))
        except Exception as exc:
            terminal_status = "failed"
            if task is not None:
                failed_event = HarnessEvent(type="failed", message=str(exc) or exc.__class__.__name__)
                append_harness_event(self._db, task, failed_event)
                self._post_terminal_event(task.id, failed_event)
                self._post_progress(task.id, failed_event, row)
        finally:
            stop_renewal.set()
            await renewal_task
            if task is not None:
                set_task_status(self._db, task.id, terminal_status)
            mark_inbox_done(self._db, row.id)
            if _session_status(self._db, self.session_id) != "canceled":
                _set_session_status(self._db, self.session_id, "idle")
            release_session_lock(self._db, self.session_id, worker_id=worker_id)
            _append_event(
                self._db,
                self.session_id,
                "worker.session.released",
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "session_id": self.session_id,
                    "inbox_id": row.id,
                    "task_id": None if task is None else task.id,
                    "status": terminal_status,
                },
            )
            self._db.commit()

    async def _renew_session_lock_until_stopped(
        self,
        *,
        worker_id: str,
        run_id: str,
        stop: asyncio.Event,
        lease_seconds: int = 120,
    ) -> None:
        interval = max(1.0, lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                renewed = renew_session_lock(
                    self._db,
                    self.session_id,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )
                lock_expires_at = _session_lock_expires_at(self._db, self.session_id)
                _append_event(
                    self._db,
                    self.session_id,
                    "worker.session.lock_renewed",
                    {
                        "run_id": run_id,
                        "worker_id": worker_id,
                        "session_id": self.session_id,
                        "renewed": renewed,
                        "lock_expires_at": lock_expires_at,
                    },
                )
                self._db.commit()

    async def _run_harness_turn(self, adapter: HarnessAdapter, task: TaskRecord, row) -> str:
        start_event = HarnessEvent(type="started")
        append_harness_event(self._db, task, start_event)
        self._post_terminal_event(task.id, start_event)
        self._post_progress(task.id, start_event, row)
        self._db.commit()

        terminal_status = "completed"
        try:
            session = get_session(self._db, self.session_id)
            await adapter.start_task(
                TaskRequest(
                    task_id=task.id,
                    session_id=task.session_id,
                    goal=task.goal,
                    workspace=str(self._workspace),
                    output_target=task.output_target,
                    execution_mode=task.execution_mode,
                    recovery_context={
                        "inbox_id": row.id,
                        "harness_resume_id": session.harness_resume_id,
                    },
                )
            )
            async for event in adapter.stream_events(task.id):
                if event.type == "started":
                    self._record_harness_resume_id(event)
                    continue
                if event.type == "resume":
                    self._record_harness_resume_id(event)
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

    def _record_harness_resume_id(self, event: HarnessEvent) -> None:
        resume_id = event.payload.get("resume_id") or event.payload.get("thread_id")
        if not resume_id:
            return
        set_harness_resume_id(self._db, self.session_id, str(resume_id))

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
            _append_runtime_event(
                self._db,
                self.session_id,
                task_id,
                "worker.slack_delivery_failed",
                {"operation": "final_update", "channel": current_channel, "ts": ts, "error": str(exc)},
            )
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
                    _append_runtime_event(
                        self._db,
                        self.session_id,
                        task_id,
                        "worker.slack_delivery_failed",
                        {
                            "operation": "final_fallback_post" if fallback else "final_post",
                            "channel": channel,
                            "thread_ts": thread_ts,
                            "error": str(exc),
                        },
                    )

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
                _append_runtime_event(
                    self._db,
                    self.session_id,
                    task_id,
                    "worker.slack_delivery_failed",
                    {
                        "operation": "progress_post",
                        "channel": channel,
                        "thread_ts": thread_ts,
                        "error": str(exc),
                    },
                )
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
            _append_runtime_event(
                self._db,
                self.session_id,
                task_id,
                "worker.slack_delivery_failed",
                {"operation": "progress_update", "channel": current_channel, "ts": ts, "error": str(exc)},
            )
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
            _append_runtime_event(
                self._db,
                self.session_id,
                task_id,
                "worker.slack_delivery_failed",
                {"operation": f"{label}_delete", "channel": channel, "ts": ts, "error": str(exc)},
            )

    def _post_terminal_event(self, task_id: str, event: HarnessEvent) -> None:
        if event.type == "started":
            self._post_terminal_line(f"session {self.session_id} task {task_id} started")
        elif event.type == "resume":
            resume_id = event.payload.get("resume_id") or event.payload.get("thread_id")
            if resume_id:
                self._post_terminal_line(f"session {self.session_id} task {task_id} harness_resume_id={resume_id}")
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
        max_workers: int = 7,
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
        self.max_workers = max(1, max_workers)
        self.run_id = f"run_{uuid.uuid4().hex[:16]}"
        self._running_workers: set[str] = set()

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
        self.recover_stale_work()
        self._emit_worker_heartbeat()
        workers = [
            asyncio.create_task(self._worker_loop(f"worker-{index + 1}"))
            for index in range(self.max_workers)
        ]
        await asyncio.gather(*workers)

    def recover_stale_work(self) -> None:
        rows = self.db.execute(
            """
            SELECT DISTINCT s.id
            FROM sessions s
            LEFT JOIN session_inbox i ON i.session_id = s.id AND i.status = 'processing'
            LEFT JOIN tasks t ON t.session_id = s.id AND t.status = 'running'
            WHERE s.locked_by IS NOT NULL
               OR s.status = 'running'
               OR i.id IS NOT NULL
               OR t.id IS NOT NULL
            ORDER BY s.created_at ASC
            """
        ).fetchall()
        for row in rows:
            session_id = row["id"]
            running_tasks = [
                task["id"]
                for task in self.db.execute(
                    "SELECT id FROM tasks WHERE session_id = ? AND status = 'running'",
                    (session_id,),
                ).fetchall()
            ]
            processing_inbox = [
                inbox["id"]
                for inbox in self.db.execute(
                    "SELECT id FROM session_inbox WHERE session_id = ? AND status = 'processing'",
                    (session_id,),
                ).fetchall()
            ]
            self.db.execute(
                """
                UPDATE tasks
                SET status = 'interrupted',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE session_id = ? AND status = 'running'
                """,
                (session_id,),
            )
            self.db.execute(
                """
                UPDATE session_inbox
                SET status = 'queued'
                WHERE session_id = ? AND status = 'processing'
                """,
                (session_id,),
            )
            release_session_lock(self.db, session_id)
            if _session_status(self.db, session_id) == "running":
                _set_session_status(self.db, session_id, "idle")
            _append_event(
                self.db,
                session_id,
                "worker.recovery.startup",
                {
                    "run_id": self.run_id,
                    "session_id": session_id,
                    "tasks_interrupted": running_tasks,
                    "inbox_requeued": processing_inbox,
                },
            )
            if self.event_output is not None:
                self.event_output(
                    f"session {session_id} startup recovery: "
                    f"interrupted_tasks={len(running_tasks)} requeued_inbox={len(processing_inbox)}"
                )
        self.db.commit()

    async def _worker_loop(self, worker_id: str) -> None:
        while True:
            row = claim_next_available_inbox_row(self.db, worker_id=worker_id)
            self.db.commit()
            if row is None:
                if self._running_workers:
                    await asyncio.sleep(0.01)
                    continue
                return
            self._running_workers.add(worker_id)
            self._emit_worker_heartbeat()
            actor = SessionActor(
                self.db,
                row.session_id,
                adapters=self.adapters,
                slack=self.slack,
                workspace=self.workspace,
                progress=self.progress,
                event_output=self.event_output,
            )
            try:
                await actor.run_claimed_row(row, worker_id=worker_id, run_id=self.run_id)
            finally:
                self._running_workers.discard(worker_id)
                self._emit_worker_heartbeat()

    def _emit_worker_heartbeat(self) -> None:
        if self.event_output is None:
            return
        running = len(self._running_workers)
        queued = self.db.execute(
            "SELECT COUNT(*) AS count FROM session_inbox WHERE status = 'queued'"
        ).fetchone()["count"]
        locked = self.db.execute(
            """
            SELECT COUNT(*) AS count
            FROM sessions
            WHERE locked_by IS NOT NULL
              AND (
                lock_expires_at IS NULL
                OR lock_expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
              )
            """
        ).fetchone()["count"]
        self.event_output(
            f"workers: total={self.max_workers} idle={self.max_workers - running} "
            f"running={running} queued={queued} locked_sessions={locked}"
        )


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


def _session_lock_expires_at(db: sqlite3.Connection, session_id: str) -> str | None:
    row = db.execute("SELECT lock_expires_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return None if row is None else row["lock_expires_at"]


def _append_event(db: sqlite3.Connection, session_id: str, event_type: str, payload: dict) -> None:
    db.execute(
        """
        INSERT INTO task_events(session_id, event_type, payload_json)
        VALUES(?, ?, ?)
        """,
        (session_id, event_type, json.dumps(payload, sort_keys=True)),
    )


def _append_runtime_event(
    db: sqlite3.Connection,
    session_id: str,
    task_id: str | None,
    event_type: str,
    payload: dict,
) -> None:
    db.execute(
        """
        INSERT INTO task_events(session_id, task_id, event_type, payload_json)
        VALUES(?, ?, ?, ?)
        """,
        (session_id, task_id, event_type, json.dumps(payload, sort_keys=True)),
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
