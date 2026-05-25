from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

from .adapters import CodexCliAdapter
from .control import SlackReplyClient
from .db import connect, initialize_schema
from .harness import HarnessAdapter
from .inbox import available_queued_session_ids, release_session_lock
from .progress import SlackProgressRenderer
from .runtime_state import append_event, session_status, set_session_status
from .runtime_worker import EventOutput, SessionWorker


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
        session_worker_idle_ttl_seconds: float = 0.0,
        stop_when_idle: asyncio.Event | None = None,
    ) -> None:
        self.db_path = db_path
        self.db = connect(db_path)
        initialize_schema(self.db)
        self.adapters = adapters or {"codex": CodexCliAdapter()}
        self.slack = slack
        self.workspace = workspace or db_path.parent.parent
        self.progress = SlackProgressRenderer()
        self.event_output = event_output
        self.workers: dict[str, SessionWorker] = {}
        self.max_workers = max(1, max_workers)
        self.session_worker_idle_ttl_seconds = max(0.0, session_worker_idle_ttl_seconds)
        self.stop_when_idle = stop_when_idle
        self.run_id = f"run_{uuid.uuid4().hex[:16]}"
        self._live_worker_ids: set[str] = set()
        self._active_sessions: dict[str, asyncio.Task[None]] = {}
        self._worker_sequence = 0

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
            self.workers.setdefault(
                row["id"],
                SessionWorker(
                    self.db,
                    row["id"],
                    adapters=self.adapters,
                    slack=self.slack,
                    workspace=self.workspace,
                    progress=self.progress,
                    event_output=self.event_output,
                ),
            )
        return list(self.workers)

    async def run_until_idle(self) -> None:
        self.recover_stale_work()
        self._emit_worker_heartbeat()
        await self._run_session_workers_until_idle()

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
            if session_status(self.db, session_id) == "running":
                set_session_status(self.db, session_id, "idle")
            append_event(
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
            if self.event_output is not None and (running_tasks or processing_inbox):
                self.event_output(
                    f"session {session_id} startup recovery: "
                    f"interrupted_tasks={len(running_tasks)} requeued_inbox={len(processing_inbox)}"
                )
        self.db.commit()

    async def _run_session_workers_until_idle(self) -> None:
        while True:
            scheduled = self._schedule_available_session_workers()
            if scheduled:
                await asyncio.sleep(0)
                self._emit_worker_heartbeat()
            if not self._active_sessions:
                if not scheduled:
                    return
                continue
            if scheduled and len(self._active_sessions) < self.max_workers:
                continue

            done, _ = await asyncio.wait(
                self._active_sessions.values(),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=0.01,
            )
            for task in done:
                task.result()

    def _schedule_available_session_workers(self) -> bool:
        scheduled = False
        capacity = self.max_workers - len(self._active_sessions)
        for session_id in available_queued_session_ids(self.db):
            if session_id in self._active_sessions:
                self.workers[session_id].wake()
                scheduled = True
                continue
            if capacity <= 0:
                return scheduled
            worker_id = self._next_worker_id()
            worker = self.workers.setdefault(
                session_id,
                SessionWorker(
                    self.db,
                    session_id,
                    adapters=self.adapters,
                    slack=self.slack,
                    workspace=self.workspace,
                    progress=self.progress,
                    event_output=self.event_output,
                ),
            )
            task = asyncio.create_task(self._run_session_worker(worker, worker_id=worker_id))
            self._active_sessions[session_id] = task
            self._live_worker_ids.add(worker_id)
            self.db.commit()
            scheduled = True
            capacity -= 1
        return scheduled

    async def _run_session_worker(self, worker: SessionWorker, *, worker_id: str) -> None:
        try:
            await worker.run_worker(
                worker_id=worker_id,
                run_id=self.run_id,
                idle_ttl_seconds=self.session_worker_idle_ttl_seconds,
                stop_when_idle=self.stop_when_idle,
            )
        finally:
            self._active_sessions.pop(worker.session_id, None)
            self._live_worker_ids.discard(worker_id)
            self.workers.pop(worker.session_id, None)
            self._emit_worker_heartbeat()

    async def shutdown(self) -> None:
        for worker in list(self.workers.values()):
            worker.shutdown()
        if not self._active_sessions:
            return
        await asyncio.gather(*self._active_sessions.values(), return_exceptions=True)

    def _next_worker_id(self) -> str:
        self._worker_sequence += 1
        return f"worker-{self._worker_sequence}"

    def _emit_worker_heartbeat(self) -> None:
        if self.event_output is None:
            return
        live = len(self._live_worker_ids)
        queued = self.db.execute(
            "SELECT COUNT(DISTINCT session_id) AS count FROM session_inbox WHERE status = 'queued'"
        ).fetchone()["count"]
        active = self.db.execute(
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
        warm = max(0, live - active)
        self.event_output(
            f"workers: total={self.max_workers} capacity={self.max_workers - live} "
            f"live={live} active={active} warm={warm} queued_sessions={queued}"
        )
