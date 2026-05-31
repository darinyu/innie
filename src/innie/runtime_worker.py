from __future__ import annotations

from collections.abc import Callable
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .control import SlackReplyClient
from .harness import HarnessAdapter, HarnessEvent, TaskRequest
from .inbox import (
    acquire_session_lock,
    claim_next_inbox_row,
    mark_inbox_done,
    release_session_lock,
    renew_session_lock,
)
from .progress import SlackProgressRenderer
from .runtime_state import (
    append_event,
    append_runtime_event,
    session_lock_expires_at,
    session_status,
    set_session_status,
)
from .runtime_terminal import format_terminal_event
from .sessions import get_session, set_harness_resume_id
from .slack_files import build_goal_with_files, list_files_for_inbox
from .tasks import (
    TaskRecord,
    append_harness_event,
    create_task,
    record_adapter_capabilities,
    record_artifacts,
    set_task_status,
)


EventOutput = Callable[[str], None]


@dataclass(frozen=True)
class SlackDeliveryTarget:
    channel: str
    thread_ts: str
    mode: str = "message"
    user: str | None = None


class SessionWorker:
    def __init__(
        self,
        db: sqlite3.Connection,
        session_id: str,
        *,
        adapters: dict[str, HarnessAdapter],
        slack: SlackReplyClient | None,
        workspace: Path,
        progress: SlackProgressRenderer,
        watched_user_id: str | None = None,
        event_output: EventOutput | None = None,
    ) -> None:
        self._db = db
        self.session_id = session_id
        self._adapters = adapters
        self._slack = slack
        self._workspace = workspace
        self._progress = progress
        self._watched_user_id = watched_user_id
        self._event_output = event_output
        self._cancel_requested = False
        self._progress_messages: dict[str, tuple[str, str]] = {}
        self._delivery_targets: dict[str, SlackDeliveryTarget] = {}
        self._finalized_tasks: set[str] = set()
        self._progress_details: dict[str, list[str]] = {}
        self._progress_summaries: dict[str, str] = {}
        self._wake_requested = asyncio.Event()
        self._shutdown_requested = False
        self._harness_sessions: dict[str, HarnessAdapter] = {}

    def cancel(self) -> None:
        self._cancel_requested = True
        self.wake()

    def shutdown(self) -> None:
        self._shutdown_requested = True
        self.wake()

    def wake(self) -> None:
        self._wake_requested.set()

    async def run_worker(
        self,
        *,
        worker_id: str,
        run_id: str,
        idle_ttl_seconds: float = 0.0,
        stop_when_idle: asyncio.Event | None = None,
    ) -> None:
        append_event(
            self._db,
            self.session_id,
            "worker.session.started",
            {"run_id": run_id, "worker_id": worker_id, "session_id": self.session_id},
        )
        self._db.commit()
        terminal_status = "idle"
        try:
            while not self._cancel_requested and not self._shutdown_requested:
                self._wake_requested.clear()
                await self._drain_locked_session(worker_id=worker_id, run_id=run_id)
                if self._cancel_requested or self._shutdown_requested or _event_is_set(stop_when_idle):
                    break
                if idle_ttl_seconds <= 0:
                    break
                try:
                    await _wait_for_wake_or_stop(self._wake_requested, stop_when_idle, timeout=idle_ttl_seconds)
                except asyncio.TimeoutError:
                    break
            if self._cancel_requested:
                terminal_status = "canceled"
                set_session_status(self._db, self.session_id, "canceled")
            elif session_status(self._db, self.session_id) != "canceled":
                set_session_status(self._db, self.session_id, "idle")
                append_event(
                    self._db,
                    self.session_id,
                    "worker.session.idle",
                    {"run_id": run_id, "worker_id": worker_id, "session_id": self.session_id},
                )
            self._db.commit()
        except Exception as exc:
            terminal_status = "failed"
            append_event(
                self._db,
                self.session_id,
                "worker.session.failed",
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "session_id": self.session_id,
                    "error": str(exc) or exc.__class__.__name__,
                },
            )
            self._db.commit()
            raise
        finally:
            await self._close_harness_sessions()
            append_event(
                self._db,
                self.session_id,
                "worker.session.released",
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "session_id": self.session_id,
                    "status": terminal_status,
                },
            )
            self._db.commit()

    async def _drain_locked_session(self, *, worker_id: str, run_id: str) -> None:
        if not acquire_session_lock(self._db, self.session_id, worker_id=worker_id):
            self._db.commit()
            return
        append_event(
            self._db,
            self.session_id,
            "worker.session.lock_acquired",
            {
                "run_id": run_id,
                "worker_id": worker_id,
                "session_id": self.session_id,
                "lock_expires_at": session_lock_expires_at(self._db, self.session_id),
            },
        )
        self._db.commit()
        stop_renewal = asyncio.Event()
        renewal_task = asyncio.create_task(
            self._renew_session_lock_until_stopped(worker_id=worker_id, run_id=run_id, stop=stop_renewal)
        )
        try:
            while not self._cancel_requested and not self._shutdown_requested:
                row = claim_next_inbox_row(self._db, self.session_id)
                if row is None:
                    break
                set_session_status(self._db, self.session_id, "running")
                self._db.commit()
                await self._process_inbox_row(row, worker_id=worker_id, run_id=run_id)
        finally:
            stop_renewal.set()
            await renewal_task
            release_session_lock(self._db, self.session_id, worker_id=worker_id)
            self._db.commit()

    async def _process_inbox_row(self, row, *, worker_id: str, run_id: str) -> None:
        lock_expires_at = session_lock_expires_at(self._db, self.session_id)
        append_event(
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
        adapter = await self._adapter_for_turn(harness_id, session.harness_resume_id)
        task: TaskRecord | None = None
        terminal_status = "failed"
        try:
            goal = self._goal_for_row(row, harness_id=harness_id)
            task = create_task(
                self._db,
                session_id=self.session_id,
                goal=goal,
                output_target=session.output_target,
                harness_id=harness_id,
                execution_mode="autonomous",
            )
            record_adapter_capabilities(self._db, harness_id, adapter.capabilities)
            set_task_status(self._db, task.id, "running")
            append_event(
                self._db,
                self.session_id,
                "worker.turn.started",
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "session_id": self.session_id,
                    "inbox_id": row.id,
                    "task_id": task.id,
                },
            )
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
            if task is not None:
                set_task_status(self._db, task.id, terminal_status)
            mark_inbox_done(self._db, row.id)
            append_event(
                self._db,
                self.session_id,
                "worker.turn.completed",
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

    async def _adapter_for_turn(self, harness_id: str, resume_id: str | None) -> HarnessAdapter:
        if harness_id in self._harness_sessions:
            return self._harness_sessions[harness_id]
        adapter = self._adapters[harness_id]
        start_session = getattr(adapter, "start_session", None)
        if start_session is None:
            return adapter
        session_adapter = await start_session(
            session_id=self.session_id,
            workspace=str(self._workspace),
            recovery_context={"harness_resume_id": resume_id},
        )
        self._harness_sessions[harness_id] = session_adapter
        append_event(
            self._db,
            self.session_id,
            "worker.harness.started",
            {"harness_id": harness_id, "session_id": self.session_id},
        )
        self._post_terminal_line(f"session {self.session_id} harness {harness_id} started")
        self._db.commit()
        return session_adapter

    async def _close_harness_sessions(self) -> None:
        for harness_id, session_adapter in list(self._harness_sessions.items()):
            close = getattr(session_adapter, "close", None)
            if close is None:
                continue
            await close()
            append_event(
                self._db,
                self.session_id,
                "worker.harness.closed",
                {"harness_id": harness_id, "session_id": self.session_id},
            )
            self._post_terminal_line(f"session {self.session_id} harness {harness_id} closed")
        self._harness_sessions.clear()
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
                lock_expires_at = session_lock_expires_at(self._db, self.session_id)
                append_event(
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

    def _goal_for_row(self, row, *, harness_id: str) -> str:
        records = list_files_for_inbox(self._db, session_id=row.session_id, slack_event_id=row.slack_event_id)
        goal = _goal_with_slack_context(self._db, row) if harness_id in {"codex", "claude"} else row.text
        return build_goal_with_files(goal, records)

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
                        "slack_channel_id": row.slack_channel_id,
                        "slack_message_ts": row.slack_message_ts,
                        "slack_thread_ts": row.slack_thread_ts,
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
        if task_id in self._finalized_tasks:
            if event.type == "completed":
                self._delete_progress_message(task_id)
                self._finalized_tasks.discard(task_id)
            return
        detail_line = self._progress.detail_line(event)
        if detail_line is not None:
            self._progress_details.setdefault(task_id, []).append(detail_line)
            self._progress_summaries[task_id] = detail_line
        progress_details = self._progress_details.get(task_id, [])
        if event.type in {"output", "failed", "canceled"}:
            final_messages = self._progress.render_final_messages(task_id, event, progress_details)
            target = self._delivery_target_for_task(task_id, row)
            self._replace_progress_message_or_post_final(
                task_id,
                target=target,
                messages=final_messages,
            )
            self._progress_details.pop(task_id, None)
            self._progress_summaries.pop(task_id, None)
            self._delivery_targets.pop(task_id, None)
            self._finalized_tasks.add(task_id)
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
            target = self._delivery_target_for_task(task_id, row)
            self._upsert_progress_message(
                task_id,
                target=target,
                text=rendered.text,
                blocks=rendered.blocks,
            )
            return
        target = self._delivery_target_for_task(task_id, row)
        self._post_slack_message(target=target, text=rendered.text, blocks=rendered.blocks)

    def _replace_progress_message_or_post_final(
        self,
        task_id: str,
        *,
        target: SlackDeliveryTarget,
        messages: list,
    ) -> None:
        if self._slack is None:
            return
        if not messages:
            return
        if target.mode == "ephemeral":
            self._post_final_messages(target=target, messages=messages, task_id=task_id)
            return
        current = self._progress_messages.pop(task_id, None)
        if current is None:
            self._post_final_messages(target=target, messages=messages, task_id=task_id)
            return
        current_channel, ts = current
        first = messages[0]
        try:
            self._slack.update_message(channel=current_channel, ts=ts, text=first.text, blocks=first.blocks)
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack final updated ts={ts}")
            self._post_final_messages(target=target, messages=messages[1:], task_id=task_id)
        except Exception as exc:
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack final update failed: {exc}")
            append_runtime_event(
                self._db,
                self.session_id,
                task_id,
                "worker.slack_delivery_failed",
                {"operation": "final_update", "channel": current_channel, "ts": ts, "error": str(exc)},
            )
            self._delete_slack_message(current_channel, ts, task_id, "progress")
            self._post_final_messages(target=target, messages=messages, task_id=task_id, fallback=True)

    def _post_final_messages(
        self,
        *,
        target: SlackDeliveryTarget,
        messages: list,
        task_id: str | None = None,
        fallback: bool = False,
    ) -> None:
        for message in messages:
            try:
                self._post_slack_message(target=target, text=message.text, blocks=message.blocks)
                if task_id is not None:
                    label = "slack final fallback posted" if fallback else "slack final posted"
                    self._post_terminal_line(f"session {self.session_id} task {task_id} {label}")
            except Exception as exc:
                if task_id is not None:
                    label = "fallback" if fallback else "post"
                    self._post_terminal_line(f"session {self.session_id} task {task_id} slack final {label} failed: {exc}")
                    append_runtime_event(
                        self._db,
                        self.session_id,
                        task_id,
                        "worker.slack_delivery_failed",
                        {
                            "operation": "final_fallback_post" if fallback else "final_post",
                            "channel": target.channel,
                            "thread_ts": target.thread_ts,
                            "mode": target.mode,
                            "error": str(exc),
                        },
                    )

    def _upsert_progress_message(
        self,
        task_id: str,
        *,
        target: SlackDeliveryTarget,
        text: str,
        blocks: list[dict] | None,
    ) -> None:
        if self._slack is None:
            return
        if target.mode == "ephemeral":
            return
        current = self._progress_messages.get(task_id)
        if current is None:
            try:
                ts = self._slack.post_message(channel=target.channel, thread_ts=target.thread_ts, text=text, blocks=blocks)
            except Exception as exc:
                self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress post failed: {exc}")
                append_runtime_event(
                    self._db,
                    self.session_id,
                    task_id,
                    "worker.slack_delivery_failed",
                    {
                        "operation": "progress_post",
                        "channel": target.channel,
                        "thread_ts": target.thread_ts,
                        "error": str(exc),
                    },
                )
                return
            if ts:
                self._progress_messages[task_id] = (target.channel, ts)
                self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress posted ts={ts}")
            return
        current_channel, ts = current
        try:
            self._slack.update_message(channel=current_channel, ts=ts, text=text, blocks=blocks)
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress updated ts={ts}")
        except Exception as exc:
            self._post_terminal_line(f"session {self.session_id} task {task_id} slack progress update failed ts={ts}: {exc}")
            append_runtime_event(
                self._db,
                self.session_id,
                task_id,
                "worker.slack_delivery_failed",
                {"operation": "progress_update", "channel": current_channel, "ts": ts, "error": str(exc)},
            )

    def _delivery_target_for_task(self, task_id: str, row) -> SlackDeliveryTarget:
        target = self._delivery_targets.get(task_id)
        if target is None:
            target = self._delivery_target_for_row(row)
            self._delivery_targets[task_id] = target
        return target

    def _delivery_target_for_row(self, row) -> SlackDeliveryTarget:
        thread_ts = row.slack_thread_ts or row.slack_message_ts
        if _delivery_type_for_row(self._db, row) != "user_mention":
            return SlackDeliveryTarget(row.slack_channel_id, thread_ts)
        user_id = self._watched_user_id
        if not user_id:
            return SlackDeliveryTarget(row.slack_channel_id, thread_ts)
        if row.slack_thread_ts:
            return SlackDeliveryTarget(row.slack_channel_id, row.slack_thread_ts, mode="ephemeral", user=user_id)
        return self._dm_thread_target(row, user_id=user_id)

    def _dm_thread_target(self, row, *, user_id: str) -> SlackDeliveryTarget:
        if self._slack is None:
            return SlackDeliveryTarget(row.slack_channel_id, row.slack_message_ts)
        permalink = _fallback_thread_link(row.slack_channel_id, row.slack_message_ts)
        unfurl_original_thread = False
        try:
            direct_permalink = self._slack.get_permalink(channel=row.slack_channel_id, message_ts=row.slack_message_ts)
            if direct_permalink:
                permalink = direct_permalink
                unfurl_original_thread = True
        except Exception as exc:
            self._post_terminal_line(
                f"session {self.session_id} slack permalink lookup failed for {row.slack_channel_id}:{row.slack_message_ts}: {exc}"
            )
            workspace_link = self._workspace_thread_link(row.slack_channel_id, row.slack_message_ts)
            if workspace_link:
                permalink = workspace_link
                unfurl_original_thread = True
        handoff_text = _dm_handoff_text(row, permalink)
        post_direct_message = getattr(self._slack, "post_direct_message", None)
        if callable(post_direct_message):
            dm_result = post_direct_message(
                user=user_id,
                text=handoff_text,
                unfurl_links=unfurl_original_thread,
                unfurl_media=False,
            )
            dm_channel = str(dm_result.channel)
            dm_ts = dm_result.ts
        else:
            dm_channel = self._slack.open_dm(user=user_id)
            dm_ts = self._slack.post_message(
                channel=dm_channel,
                thread_ts=None,
                text=handoff_text,
                unfurl_links=unfurl_original_thread,
                unfurl_media=False,
            )
        return SlackDeliveryTarget(dm_channel, dm_ts or row.slack_message_ts)

    def _workspace_thread_link(self, channel: str, message_ts: str) -> str | None:
        workspace_url_fn = getattr(self._slack, "workspace_url", None)
        if not callable(workspace_url_fn):
            return None
        try:
            workspace_url = workspace_url_fn()
        except Exception as exc:
            self._post_terminal_line(f"session {self.session_id} slack workspace URL lookup failed: {exc}")
            return None
        if not workspace_url:
            return None
        return _workspace_thread_link(str(workspace_url), channel, message_ts)

    def _post_slack_message(
        self,
        *,
        target: SlackDeliveryTarget,
        text: str,
        blocks: list[dict] | None,
    ) -> str | None:
        if self._slack is None:
            return None
        if target.mode == "ephemeral":
            if target.user is None:
                raise RuntimeError("ephemeral Slack delivery missing user")
            return self._slack.post_ephemeral(
                channel=target.channel,
                user=target.user,
                thread_ts=target.thread_ts,
                text=text,
                blocks=blocks,
            )
        return self._slack.post_message(channel=target.channel, thread_ts=target.thread_ts, text=text, blocks=blocks)

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
            append_runtime_event(
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
            self._post_terminal_line(format_terminal_event(self.session_id, task_id, event))
        elif event.type == "usage" and event.usage is not None:
            self._post_terminal_line(
                f"session {self.session_id} task {task_id} usage: "
                f"{event.usage.input_tokens} input, {event.usage.output_tokens} output"
            )

    def _post_terminal_line(self, line: str) -> None:
        if self._event_output is not None:
            self._event_output(line)


def _goal_with_slack_context(db: sqlite3.Connection, row) -> str:
    thread_ts = row.slack_thread_ts or row.slack_message_ts
    delivery_type = _delivery_type_for_row(db, row)
    output_instruction = (
        "Draft a reply for the watched user to send; write in the user's voice and keep it ready to paste or send."
        if delivery_type == "user_mention"
        else "Reply as Innie in the Slack thread."
    )
    return "\n\n".join(
        [
            row.text,
            "\n".join(
                [
                    "Variable turn context:",
                    "Slack trigger:",
                    f"- channel: {row.slack_channel_id}",
                    f"- thread_ts: {thread_ts}",
                    f"- message_ts: {row.slack_message_ts}",
                    f"- response_mode: {delivery_type}",
                    f"- output_instruction: {output_instruction}",
                    "- routing_note: Innie will route the final answer back to the Slack destination above.",
                    "- context_lookup: Use the active harness environment to inspect Slack only when the task needs more thread context.",
                ]
            ),
        ]
    )


def _delivery_type_for_row(db: sqlite3.Connection, row) -> str:
    if row.slack_event_id is not None:
        trigger = db.execute(
            "SELECT trigger_type FROM slack_triggers WHERE slack_event_id = ?",
            (row.slack_event_id,),
        ).fetchone()
        if trigger is not None and trigger["trigger_type"] in {"bot_mention", "user_mention"}:
            return str(trigger["trigger_type"])
    session = db.execute("SELECT trigger_type FROM sessions WHERE id = ?", (row.session_id,)).fetchone()
    if session is not None and session["trigger_type"] in {"bot_mention", "user_mention"}:
        return str(session["trigger_type"])
    return "bot_mention"


def _fallback_thread_link(channel: str, message_ts: str) -> str:
    return f"https://slack.com/app_redirect?channel={channel}&message_ts={message_ts}"


def _workspace_thread_link(workspace_url: str, channel: str, message_ts: str) -> str:
    return f"{workspace_url.rstrip('/')}/archives/{channel}/p{message_ts.replace('.', '')}"


def _dm_handoff_text(row, permalink: str) -> str:
    thread_link = f"<{permalink}|open thread>"
    return f"Hi, here is the {thread_link} you are tagged on. Let me help draft a reply."


def _compact_original_text(text: str, *, limit: int = 500) -> str:
    compact = "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _slack_quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.splitlines() if line)


def _event_is_set(event: asyncio.Event | None) -> bool:
    return event is not None and event.is_set()


async def _wait_for_wake_or_stop(
    wake: asyncio.Event,
    stop: asyncio.Event | None,
    *,
    timeout: float,
) -> None:
    if stop is None:
        await asyncio.wait_for(wake.wait(), timeout=timeout)
        return
    wake_task = asyncio.create_task(wake.wait())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, pending = await asyncio.wait(
            {wake_task, stop_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise asyncio.TimeoutError
        for task in pending:
            task.cancel()
    finally:
        for task in (wake_task, stop_task):
            if not task.done():
                task.cancel()
