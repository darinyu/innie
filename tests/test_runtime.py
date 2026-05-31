from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.harness import HarnessCapabilities, HarnessEvent, ScriptedHarnessAdapter, TaskHandle, TokenUsage
from innie.inbox import enqueue_trigger
from innie.runtime import SessionManager
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger
from innie.progress import SLACK_FINAL_TEXT_LIMIT, SLACK_SECTION_TEXT_LIMIT


class FakeSlackReplies:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.ephemerals: list[tuple[str, str, str, str | None]] = []
        self.updates: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self.message_blocks: list[list[dict] | None] = []
        self.update_blocks: list[list[dict] | None] = []
        self.ephemeral_blocks: list[list[dict] | None] = []
        self.message_options: list[dict[str, bool | None]] = []
        self.opened_dms: list[str] = []
        self._next_ts = 1

    def post_message(
        self,
        *,
        channel: str,
        thread_ts: str | None,
        text: str,
        blocks: list[dict] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> str:
        self.messages.append((channel, thread_ts, text))
        self.message_blocks.append(blocks)
        self.message_options.append({"unfurl_links": unfurl_links, "unfurl_media": unfurl_media})
        ts = f"900.{self._next_ts}"
        self._next_ts += 1
        return ts

    def post_ephemeral(
        self,
        *,
        channel: str,
        user: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[dict] | None = None,
    ) -> str:
        self.ephemerals.append((channel, user, text, thread_ts))
        self.ephemeral_blocks.append(blocks)
        ts = f"ephemeral.{self._next_ts}"
        self._next_ts += 1
        return ts

    def open_dm(self, *, user: str) -> str:
        self.opened_dms.append(user)
        return f"D_{user}"

    def get_permalink(self, *, channel: str, message_ts: str) -> str | None:
        return f"https://slack.example/archives/{channel}/p{message_ts.replace('.', '')}"

    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
        self.updates.append((channel, ts, text))
        self.update_blocks.append(blocks)

    def delete_message(self, *, channel: str, ts: str) -> None:
        self.deletes.append((channel, ts))


class FailingFinalUpdateSlack(FakeSlackReplies):
    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
        if text.startswith("final"):
            raise RuntimeError("chat.update failed: msg_too_long")
        super().update_message(channel=channel, ts=ts, text=text, blocks=blocks)


class FailingProgressPostSlack(FakeSlackReplies):
    def post_message(
        self,
        *,
        channel: str,
        thread_ts: str | None,
        text: str,
        blocks: list[dict] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> str:
        if text in {"Innie is working", "working", "first", "second"}:
            raise RuntimeError("chat.postMessage failed: rate_limited")
        return super().post_message(
            channel=channel,
            thread_ts=thread_ts,
            text=text,
            blocks=blocks,
            unfurl_links=unfurl_links,
            unfurl_media=unfurl_media,
        )


class FailingProgressUpdateSlack(FakeSlackReplies):
    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
        if text in {"Innie is working", "working", "first", "second"}:
            raise RuntimeError("chat.update failed: rate_limited")
        super().update_message(channel=channel, ts=ts, text=text, blocks=blocks)


class LengthLimitedSlack(FakeSlackReplies):
    def post_message(
        self,
        *,
        channel: str,
        thread_ts: str | None,
        text: str,
        blocks: list[dict] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> str:
        if len(text) > SLACK_SECTION_TEXT_LIMIT:
            raise RuntimeError("chat.postMessage failed: msg_too_long")
        return super().post_message(
            channel=channel,
            thread_ts=thread_ts,
            text=text,
            blocks=blocks,
            unfurl_links=unfurl_links,
            unfurl_media=unfurl_media,
        )

    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
        if len(text) > SLACK_SECTION_TEXT_LIMIT:
            raise RuntimeError("chat.update failed: msg_too_long")
        super().update_message(channel=channel, ts=ts, text=text, blocks=blocks)


class FailingPermalinkSlack(FakeSlackReplies):
    def get_permalink(self, *, channel: str, message_ts: str) -> str | None:
        raise RuntimeError("chat.getPermalink failed: invalid_arguments")


class FailingPermalinkWorkspaceSlack(FailingPermalinkSlack):
    def workspace_url(self) -> str:
        return "https://paofuanddddd.slack.com/"


class DirectMessageSlack(FakeSlackReplies):
    def post_direct_message(
        self,
        *,
        user: str,
        text: str,
        blocks: list[dict] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ):
        self.messages.append((f"D_REAL_{user}", None, text))
        self.message_blocks.append(blocks)
        self.message_options.append({"unfurl_links": unfurl_links, "unfurl_media": unfurl_media})
        return type("SlackPostResult", (), {"channel": f"D_REAL_{user}", "ts": "900.8"})()


class FailingStreamAdapter:
    harness_id = "failing"
    capabilities = HarnessCapabilities(supports_streaming=True)

    async def start_task(self, request):
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError

    async def cancel_task(self, task_id: str) -> None:
        pass

    async def stream_events(self, task_id: str):
        yield HarnessEvent(type="progress", message="working")
        raise RuntimeError("adapter crashed")

    async def collect_artifacts(self, task_id: str):
        return []


class BlockingAdapter:
    harness_id = "blocking"
    capabilities = HarnessCapabilities(supports_streaming=True)

    def __init__(self) -> None:
        self.started: list[str] = []
        self.active = 0
        self.max_active = 0
        self.release = asyncio.Event()

    async def start_task(self, request):
        self.started.append(request.session_id)
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError

    async def cancel_task(self, task_id: str) -> None:
        pass

    async def stream_events(self, task_id: str):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        yield HarnessEvent(type="progress", message=f"running {task_id}")
        await self.release.wait()
        self.active -= 1
        yield HarnessEvent(type="output", message=f"done {task_id}")
        yield HarnessEvent(type="completed")

    async def collect_artifacts(self, task_id: str):
        return []


class SequentialSessionAdapter:
    harness_id = "sequential"
    capabilities = HarnessCapabilities(supports_streaming=True)

    def __init__(self) -> None:
        self.active_by_session: dict[str, int] = {}
        self.max_active_by_session: dict[str, int] = {}
        self.session_by_task: dict[str, str] = {}

    async def start_task(self, request):
        self.session_by_task[request.task_id] = request.session_id
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError

    async def cancel_task(self, task_id: str) -> None:
        pass

    async def stream_events(self, task_id: str):
        session_id = self.session_by_task[task_id]
        self.active_by_session[session_id] = self.active_by_session.get(session_id, 0) + 1
        self.max_active_by_session[session_id] = max(
            self.max_active_by_session.get(session_id, 0),
            self.active_by_session[session_id],
        )
        await asyncio.sleep(0)
        self.active_by_session[session_id] -= 1
        yield HarnessEvent(type="output", message=f"done {task_id}")
        yield HarnessEvent(type="completed")

    async def collect_artifacts(self, task_id: str):
        return []


class ResumeRecordingAdapter:
    harness_id = "resume_recording"
    capabilities = HarnessCapabilities(supports_streaming=True, supports_resume=True)

    def __init__(self) -> None:
        self.recovery_contexts: list[dict] = []
        self.task_order: list[str] = []

    async def start_task(self, request):
        self.recovery_contexts.append(dict(request.recovery_context))
        self.task_order.append(request.task_id)
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError

    async def cancel_task(self, task_id: str) -> None:
        pass

    async def stream_events(self, task_id: str):
        if task_id == self.task_order[0]:
            yield HarnessEvent(type="resume", payload={"resume_id": "019e-thread"})
        yield HarnessEvent(type="output", message=f"done {task_id}")
        yield HarnessEvent(type="completed")

    async def collect_artifacts(self, task_id: str):
        return []


class PersistentSessionAdapter:
    harness_id = "persistent"
    capabilities = HarnessCapabilities(supports_streaming=True, supports_resume=True)

    def __init__(self) -> None:
        self.sessions_started: list[dict] = []
        self.sessions_closed = 0
        self.started: list[str] = []
        self.completed: list[str] = []

    async def start_session(self, *, session_id: str, workspace: str, recovery_context: dict):
        self.sessions_started.append(
            {
                "session_id": session_id,
                "workspace": workspace,
                "recovery_context": dict(recovery_context),
            }
        )
        return PersistentSessionHandle(self)


class PersistentSessionHandle:
    harness_id = "persistent"
    capabilities = PersistentSessionAdapter.capabilities

    def __init__(self, owner: PersistentSessionAdapter) -> None:
        self._owner = owner
        self._session_by_task: dict[str, str] = {}

    async def start_task(self, request):
        self._owner.started.append(request.goal)
        self._session_by_task[request.task_id] = request.session_id
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError

    async def cancel_task(self, task_id: str) -> None:
        pass

    async def stream_events(self, task_id: str):
        yield HarnessEvent(type="output", message=f"done {task_id}")
        yield HarnessEvent(type="completed")
        self._owner.completed.append(task_id)

    async def collect_artifacts(self, task_id: str):
        return []

    async def close(self) -> None:
        self._owner.sessions_closed += 1


class GoalRecordingAdapter:
    capabilities = HarnessCapabilities(supports_streaming=True)

    def __init__(self, harness_id: str) -> None:
        self.harness_id = harness_id
        self.goals: list[str] = []

    async def start_task(self, request):
        self.goals.append(request.goal)
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError

    async def cancel_task(self, task_id: str) -> None:
        pass

    async def stream_events(self, task_id: str):
        yield HarnessEvent(type="output", message="done")
        yield HarnessEvent(type="completed")

    async def collect_artifacts(self, task_id: str):
        return []


def make_trigger(
    event_id: str,
    channel: str = "D1",
    ts: str = "100.1",
    thread_ts: str | None = None,
    trigger_type: str = "dm",
    text: str | None = None,
) -> SlackTrigger:
    return SlackTrigger(
        event_id=event_id,
        trigger_type=trigger_type,
        channel_id=channel,
        message_ts=ts,
        thread_ts=thread_ts,
        sender_user_id="U1",
        text=text or f"text {event_id}",
        payload={"event_id": event_id},
    )


class RuntimeTest(unittest.TestCase):
    def test_manager_appends_slack_file_paths_to_codex_goal(self) -> None:
        self._assert_manager_appends_slack_file_paths_to_goal("codex")

    def test_manager_appends_slack_file_paths_to_claude_goal(self) -> None:
        self._assert_manager_appends_slack_file_paths_to_goal("claude")

    def test_manager_appends_slack_trigger_coordinates_to_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            path = workspace / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("EvSlack", "C1", "100.3", thread_ts="100.1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="codex")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()

            adapter = GoalRecordingAdapter("codex")
            manager = SessionManager(path, adapters={"codex": adapter}, slack=FakeSlackReplies(), workspace=workspace)

            asyncio.run(manager.run_until_idle())

            self.assertEqual(1, len(adapter.goals))
            goal = adapter.goals[0]
            self.assertIn("text EvSlack", goal)
            self.assertIn("Variable turn context:", goal)
            self.assertIn("Slack trigger:", goal)
            self.assertIn("- channel: C1", goal)
            self.assertIn("- thread_ts: 100.1", goal)
            self.assertIn("- message_ts: 100.3", goal)
            self.assertIn("- routing_note: Innie will route the final answer back to the Slack destination above.", goal)
            self.assertIn(
                "- context_lookup: Use the active harness environment to inspect Slack only when the task needs more thread context.",
                goal,
            )

    def _assert_manager_appends_slack_file_paths_to_goal(self, harness_id: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            path = workspace / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("EvFile", "D1", "100.1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id=harness_id)
            enqueue_trigger(db, session=session, trigger=item)
            staged = workspace / ".innie" / "files" / session.id / "EvFile" / "report.txt"
            staged.parent.mkdir(parents=True)
            staged.write_text("report", encoding="utf-8")
            db.execute(
                """
                INSERT INTO slack_files(
                    session_id,
                    slack_event_id,
                    slack_file_id,
                    name,
                    mimetype,
                    filetype,
                    url_private_download,
                    local_path,
                    byte_count,
                    status,
                    error
                )
                VALUES(?, 'EvFile', 'F1', 'report.txt', 'text/plain', 'text', 'https://files.example/F1', ?, 6, 'staged', NULL)
                """,
                (session.id, str(staged)),
            )
            db.execute(
                """
                INSERT INTO slack_files(
                    session_id,
                    slack_event_id,
                    slack_file_id,
                    name,
                    mimetype,
                    filetype,
                    url_private_download,
                    local_path,
                    byte_count,
                    status,
                    error
                )
                VALUES(?, 'EvFile', 'F2', 'missing.csv', 'text/csv', 'csv', 'https://files.example/F2', NULL, 0, 'failed', 'not_allowed')
                """,
                (session.id,),
            )
            db.commit()
            adapter = GoalRecordingAdapter(harness_id)
            manager = SessionManager(path, adapters={harness_id: adapter}, slack=FakeSlackReplies(), workspace=workspace)

            asyncio.run(manager.run_until_idle())

            self.assertEqual(1, len(adapter.goals))
            goal = adapter.goals[0]
            self.assertIn("text EvFile", goal)
            self.assertIn("Attached files:\n- " + str(staged), goal)
            self.assertIn("Attachment warnings:\n- missing.csv: download failed: not_allowed", goal)

    def test_worker_pool_processes_different_sessions_concurrently(self) -> None:
        async def run_manager(manager: SessionManager, adapter: BlockingAdapter) -> None:
            run_task = asyncio.create_task(manager.run_until_idle())
            while len(adapter.started) < 2:
                await asyncio.sleep(0)
            self.assertEqual(2, adapter.active)
            adapter.release.set()
            await run_task

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            for item in (make_trigger("Ev1", "D1", "100.1"), make_trigger("Ev2", "D2", "200.1")):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item, harness_id="blocking")
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            adapter = BlockingAdapter()
            manager = SessionManager(path, adapters={"blocking": adapter}, workspace=Path(tmp), max_workers=2)
            try:
                asyncio.run(run_manager(manager, adapter))
            finally:
                manager.close()

            self.assertEqual(2, adapter.max_active)

    def test_worker_pool_preserves_same_session_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            root = make_trigger("Ev1", "D1", "100.1")
            followup = make_trigger("Ev2", "D1", "100.2", thread_ts="100.1")
            for item in (root, followup):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item, harness_id="sequential")
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            adapter = SequentialSessionAdapter()
            manager = SessionManager(path, adapters={"sequential": adapter}, workspace=Path(tmp), max_workers=2)
            try:
                asyncio.run(manager.run_until_idle())
                inbox_statuses = [
                    row["status"]
                    for row in manager.db.execute("SELECT status FROM session_inbox ORDER BY id")
                ]
            finally:
                manager.close()

            self.assertEqual(["done", "done"], inbox_statuses)
            self.assertEqual([1], list(adapter.max_active_by_session.values()))

    def test_session_worker_drains_same_session_with_one_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            root = make_trigger("Ev1", "D1", "100.1")
            followup = make_trigger("Ev2", "D1", "100.2", thread_ts="100.1")
            for item in (root, followup):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item, harness_id="sequential")
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            adapter = SequentialSessionAdapter()
            manager = SessionManager(path, adapters={"sequential": adapter}, workspace=Path(tmp), max_workers=2)
            try:
                asyncio.run(manager.run_until_idle())
                worker_events = [
                    (row["event_type"], json.loads(row["payload_json"]))
                    for row in manager.db.execute(
                        """
                        SELECT event_type, payload_json
                        FROM task_events
                        WHERE session_id = ? AND event_type LIKE 'worker.%'
                        ORDER BY id
                        """,
                        (session.id,),
                    )
                ]
            finally:
                manager.close()

            event_types = [event_type for event_type, _ in worker_events]
            self.assertEqual(1, event_types.count("worker.session.started"))
            self.assertEqual(1, event_types.count("worker.session.lock_acquired"))
            self.assertEqual(2, event_types.count("worker.inbox.claimed"))
            self.assertEqual(1, event_types.count("worker.session.idle"))
            self.assertEqual(1, event_types.count("worker.session.released"))
            worker_ids = {
                payload["worker_id"]
                for event_type, payload in worker_events
                if event_type in {"worker.session.started", "worker.inbox.claimed", "worker.session.released"}
            }
            self.assertEqual(1, len(worker_ids))

    def test_session_worker_keeps_persistent_harness_until_idle_ttl_expires(self) -> None:
        async def enqueue_followup_after_first_turn(manager: SessionManager, adapter: PersistentSessionAdapter, session_id: str) -> None:
            run_task = asyncio.create_task(manager.run_until_idle())
            while len(adapter.completed) < 1:
                await asyncio.sleep(0.001)
            followup = make_trigger("Ev2", "D1", "100.2", thread_ts="100.1")
            persist_trigger(manager.db, followup)
            session = resolve_session_for_trigger(manager.db, followup, harness_id="persistent")
            self.assertEqual(session_id, session.id)
            enqueue_trigger(manager.db, session=session, trigger=followup)
            manager.db.commit()
            await run_task

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            root = make_trigger("Ev1", "D1", "100.1")
            persist_trigger(db, root)
            session = resolve_session_for_trigger(db, root, harness_id="persistent")
            enqueue_trigger(db, session=session, trigger=root)
            db.commit()
            db.close()

            adapter = PersistentSessionAdapter()
            terminal: list[str] = []
            manager = SessionManager(
                path,
                adapters={"persistent": adapter},
                workspace=Path(tmp),
                max_workers=2,
                session_worker_idle_ttl_seconds=0.2,
                event_output=terminal.append,
            )
            try:
                asyncio.run(enqueue_followup_after_first_turn(manager, adapter, session.id))
            finally:
                manager.close()

            self.assertEqual(1, len(adapter.sessions_started))
            self.assertEqual(["text Ev1", "text Ev2"], adapter.started)
            self.assertEqual(2, len(adapter.completed))
            self.assertEqual(1, adapter.sessions_closed)
            self.assertEqual(1, terminal.count(f"session {session.id} harness persistent started"))
            self.assertEqual(1, terminal.count(f"session {session.id} harness persistent closed"))

    def test_manager_recovers_stale_processing_work_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1", "D1", "100.1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            row = enqueue_trigger(db, session=session, trigger=item)
            db.execute(
                """
                INSERT INTO tasks(id, session_id, status, goal, output_target, harness_id, execution_mode)
                VALUES('task_stale', ?, 'running', 'stale work', ?, 'scripted', 'autonomous')
                """,
                (session.id, session.output_target),
            )
            db.execute("UPDATE session_inbox SET status = 'processing' WHERE id = ?", (row.id,))
            db.execute(
                """
                UPDATE sessions
                SET status = 'running',
                    locked_by = 'dead-worker',
                    locked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-120 seconds'),
                    lock_expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-60 seconds')
                WHERE id = ?
                """,
                (session.id,),
            )
            db.commit()
            db.close()

            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="output", message="recovered"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                workspace=Path(tmp),
                event_output=terminal.append,
            )
            try:
                asyncio.run(manager.run_until_idle())
                statuses = {
                    row["id"]: row["status"]
                    for row in manager.db.execute("SELECT id, status FROM tasks ORDER BY id")
                }
                session_row = manager.db.execute("SELECT locked_by, status FROM sessions WHERE id = ?", (session.id,)).fetchone()
                recovery_events = [
                    row["event_type"]
                    for row in manager.db.execute("SELECT event_type FROM task_events WHERE session_id = ?", (session.id,))
                ]
            finally:
                manager.close()

            self.assertEqual("interrupted", statuses["task_stale"])
            self.assertIn("completed", statuses.values())
            self.assertIsNone(session_row["locked_by"])
            self.assertEqual("idle", session_row["status"])
            self.assertIn("worker.recovery.startup", recovery_events)
            self.assertIn("startup recovery", "\n".join(terminal))

    def test_manager_logs_worker_claim_release_and_compact_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1", "D1", "100.1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="output", message="done"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                workspace=Path(tmp),
                event_output=terminal.append,
                max_workers=2,
            )
            try:
                asyncio.run(manager.run_until_idle())
                worker_events = [
                    (row["event_type"], json.loads(row["payload_json"]))
                    for row in manager.db.execute(
                        """
                        SELECT event_type, payload_json
                        FROM task_events
                        WHERE session_id = ? AND event_type LIKE 'worker.%'
                        ORDER BY id
                        """,
                        (session.id,),
                    )
                ]
            finally:
                manager.close()

            self.assertTrue(any(line.startswith("workers: total=2 ") for line in terminal))
            event_types = [event_type for event_type, _ in worker_events]
            self.assertIn("worker.inbox.claimed", event_types)
            self.assertIn("worker.session.released", event_types)
            claim_payload = next(payload for event_type, payload in worker_events if event_type == "worker.inbox.claimed")
            self.assertIn("run_id", claim_payload)
            self.assertEqual("worker-1", claim_payload["worker_id"])
            self.assertEqual(session.id, claim_payload["session_id"])

    def test_manager_stores_harness_resume_id_from_adapter_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1", "D1", "100.1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="resume", payload={"resume_id": "019e-thread"}),
                    HarnessEvent(type="output", message="done"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
                session_row = manager.db.execute(
                    "SELECT harness_resume_id FROM sessions WHERE id = ?",
                    (session.id,),
                ).fetchone()
            finally:
                manager.close()

            self.assertEqual("019e-thread", session_row["harness_resume_id"])

    def test_manager_passes_stored_harness_resume_id_to_followup_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            root = make_trigger("Ev1", "D1", "100.1")
            followup = make_trigger("Ev2", "D1", "100.2", thread_ts="100.1")
            for item in (root, followup):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item, harness_id="resume_recording")
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            adapter = ResumeRecordingAdapter()
            manager = SessionManager(path, adapters={"resume_recording": adapter}, workspace=Path(tmp), max_workers=2)
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertIsNone(adapter.recovery_contexts[0]["harness_resume_id"])
            self.assertEqual("D1", adapter.recovery_contexts[0]["slack_channel_id"])
            self.assertEqual("100.1", adapter.recovery_contexts[0]["slack_message_ts"])
            self.assertIsNone(adapter.recovery_contexts[0]["slack_thread_ts"])
            self.assertEqual("019e-thread", adapter.recovery_contexts[1]["harness_resume_id"])
            self.assertEqual("D1", adapter.recovery_contexts[1]["slack_channel_id"])
            self.assertEqual("100.2", adapter.recovery_contexts[1]["slack_message_ts"])
            self.assertEqual("100.1", adapter.recovery_contexts[1]["slack_thread_ts"])

    def test_manager_processes_sessions_concurrently_until_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            for item in (make_trigger("Ev1", "D1", "100.1"), make_trigger("Ev2", "D2", "200.1")):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item, harness_id="scripted")
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="progress", message="working"),
                    HarnessEvent(type="output", message="done"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
                statuses = [row["status"] for row in manager.db.execute("SELECT status FROM sessions ORDER BY id")]
                events = [
                    row["event_type"]
                    for row in manager.db.execute("SELECT event_type FROM task_events WHERE session_id IS NOT NULL")
                ]
            finally:
                manager.close()

            self.assertEqual(["idle", "idle"], statuses)
            self.assertEqual(2, events.count("harness.output"))
            self.assertIn(("D1", "100.1", "Innie is working"), slack.messages)
            self.assertIn(("D2", "900.2", "done"), slack.updates)

    def test_manager_updates_one_slack_progress_message_and_replaces_it_with_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}),
                    HarnessEvent(
                        type="tool_use",
                        message="Cerebras OpenAI partnership AWS 2026 official Cerebras ...",
                        payload={"tool_name": "web_search"},
                    ),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual([("D1", "100.1", "*Innie is searching the web*\n> web search")], slack.messages)
            self.assertEqual(
                [
                    (
                        "D1",
                        "900.1",
                        "*Innie is searching the web*\n> Cerebras OpenAI partnership AWS 2026 official Cerebras ...",
                    ),
                    ("D1", "900.1", "final answer"),
                ],
                slack.updates,
            )
            self.assertEqual("plan", slack.message_blocks[0][0]["type"])
            self.assertEqual("section", slack.update_blocks[1][0]["type"])
            self.assertTrue(slack.update_blocks[1][0]["expand"])
            self.assertEqual("final answer", slack.update_blocks[1][0]["text"]["text"])
            self.assertEqual("plan", slack.update_blocks[0][0]["type"])
            self.assertEqual([], slack.deletes)

    def test_manager_hides_thinking_progress_summary_above_tool_widget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="I will check recent primary sources first."),
                    HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual("plan", slack.update_blocks[0][0]["type"])
            self.assertEqual("Innie is searching the web", slack.update_blocks[0][0]["title"])
            self.assertNotIn("I will check recent primary sources first.", str(slack.update_blocks[0]))

    def test_manager_deletes_progress_message_and_posts_fallback_when_final_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FailingFinalUpdateSlack()
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="working"),
                    HarnessEvent(type="output", message="final " + ("answer " * 10000)),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                event_output=terminal.append,
            )
            try:
                asyncio.run(manager.run_until_idle())
                task_status = manager.db.execute("SELECT status FROM tasks").fetchone()["status"]
            finally:
                manager.close()

            self.assertEqual("completed", task_status)
            self.assertEqual([("D1", "900.1")], slack.deletes)
            fallback_messages = slack.messages[1:]
            self.assertGreater(len(fallback_messages), 1)
            self.assertEqual(("D1", "100.1"), fallback_messages[0][:2])
            self.assertTrue(fallback_messages[0][2].startswith("final answer"))
            self.assertTrue(all(len(message[2]) <= SLACK_FINAL_TEXT_LIMIT for message in fallback_messages))
            self.assertEqual("section", slack.message_blocks[1][0]["type"])
            self.assertNotIn("Progress details", str(slack.message_blocks[2]))
            self.assertIn(
                f"session {session.id} task ",
                "\n".join(terminal),
            )
            self.assertIn("slack final update failed", "\n".join(terminal))

    def test_manager_continues_when_progress_post_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FailingProgressPostSlack()
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="working"),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp), event_output=terminal.append)
            try:
                asyncio.run(manager.run_until_idle())
                task_status = manager.db.execute("SELECT status FROM tasks").fetchone()["status"]
                slack_failures = [
                    json.loads(row["payload_json"])
                    for row in manager.db.execute(
                        "SELECT payload_json FROM task_events WHERE event_type = 'worker.slack_delivery_failed'"
                    )
                ]
            finally:
                manager.close()

            self.assertEqual("completed", task_status)
            self.assertIn(("D1", "100.1", "final answer"), slack.messages)
            self.assertIn("slack progress post failed", "\n".join(terminal))
            self.assertEqual("progress_post", slack_failures[0]["operation"])
            self.assertIn("rate_limited", slack_failures[0]["error"])

    def test_manager_continues_when_progress_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FailingProgressUpdateSlack()
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="first"),
                    HarnessEvent(type="progress", message="second"),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp), event_output=terminal.append)
            try:
                asyncio.run(manager.run_until_idle())
                task_status = manager.db.execute("SELECT status FROM tasks").fetchone()["status"]
            finally:
                manager.close()

            self.assertEqual("completed", task_status)
            self.assertIn(("D1", "900.1", "final answer"), slack.updates)
            self.assertEqual([], slack.deletes)
            self.assertIn("slack progress update failed", "\n".join(terminal))

    def test_manager_keeps_large_tool_result_progress_update_under_slack_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = LengthLimitedSlack()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="working"),
                    HarnessEvent(type="tool_result", message="search result\n" + ("x" * 6000)),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
                failures = [
                    row["event_type"]
                    for row in manager.db.execute(
                        "SELECT event_type FROM task_events WHERE event_type = 'worker.slack_delivery_failed'"
                    )
                ]
            finally:
                manager.close()

            self.assertEqual([], failures)
            self.assertLessEqual(len(slack.updates[0][2]), SLACK_SECTION_TEXT_LIMIT)
            self.assertEqual("plan", slack.update_blocks[0][0]["type"])

    def test_manager_marks_task_failed_and_replaces_progress_when_adapter_crashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="failing")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            terminal: list[str] = []
            manager = SessionManager(
                path,
                adapters={"failing": FailingStreamAdapter()},
                slack=slack,
                workspace=Path(tmp),
                event_output=terminal.append,
            )
            try:
                asyncio.run(manager.run_until_idle())
                task_status = manager.db.execute("SELECT status FROM tasks").fetchone()["status"]
                failed_count = manager.db.execute("SELECT COUNT(*) AS count FROM task_events WHERE event_type = 'harness.failed'").fetchone()["count"]
            finally:
                manager.close()

            self.assertEqual("failed", task_status)
            self.assertEqual(1, failed_count)
            self.assertIn(("D1", "900.1", "Task " + slack.updates[-1][2].split("Task ", 1)[1]), slack.updates)
            self.assertIn("adapter crashed", slack.updates[-1][2])
            self.assertIn("failed: adapter crashed", "\n".join(terminal))

    def test_manager_splits_long_final_output_without_progress_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            first_line = "a" * (SLACK_FINAL_TEXT_LIMIT - 20)
            second_line = "second slack message"
            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="checking context"),
                    HarnessEvent(type="output", message=f"{first_line}\n{second_line}"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual(("D1", "900.1", first_line), slack.updates[-1])
            self.assertEqual(("D1", "100.1", second_line), slack.messages[-1])
            self.assertEqual("section", slack.update_blocks[-1][0]["type"])
            self.assertEqual("section", slack.message_blocks[-1][0]["type"])
            self.assertNotIn("Progress details", str(slack.update_blocks[-1]))
            self.assertNotIn("Progress details", str(slack.message_blocks[-1]))

    def test_manager_posts_final_output_without_progress_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="checking context"),
                    HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual(("D1", "900.1", "final answer"), slack.updates[-1])
            final_blocks = slack.update_blocks[-1]
            self.assertIsNotNone(final_blocks)
            self.assertEqual("section", final_blocks[0]["type"])
            self.assertEqual("final answer", final_blocks[0]["text"]["text"])
            self.assertNotIn("Progress details", str(final_blocks))
            self.assertNotIn("show more", str(final_blocks))

    def test_manager_posts_progress_widget_to_root_thread_for_channel_and_threaded_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            root = make_trigger("EvRoot", channel="C1", ts="100.1")
            reply = make_trigger("EvReply", channel="C1", ts="100.2", thread_ts="100.1")
            for item in (root, reply):
                persist_trigger(db, item)
                session = resolve_session_for_trigger(db, item, harness_id="scripted")
                enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            progress_messages = [message for message in slack.messages if message[2].startswith("*Innie is")]
            self.assertEqual([("C1", "100.1"), ("C1", "100.1")], [(channel, thread_ts) for channel, thread_ts, _ in progress_messages])

    def test_manager_posts_only_final_for_threaded_user_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger(
                "EvUserThread",
                channel="C1",
                ts="100.2",
                thread_ts="100.1",
                trigger_type="user_mention",
                text="<@U_DARIN> draft my reply",
            )
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="drafting"),
                    HarnessEvent(type="output", message="draft reply"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                watched_user_id="U_DARIN",
            )
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual([], slack.messages)
            self.assertEqual([("C1", "U_DARIN", "draft reply", "100.1")], slack.ephemerals)

    def test_manager_suppresses_ephemeral_progress_for_user_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger(
                "EvUserThread",
                channel="C1",
                ts="100.2",
                thread_ts="100.1",
                trigger_type="user_mention",
                text="<@U_DARIN> draft my reply",
            )
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="drafting"),
                    HarnessEvent(type="tool_use", message="lookup", payload={"tool_name": "web_search"}),
                    HarnessEvent(type="tool_result", message="result"),
                    HarnessEvent(type="output", message="draft reply"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                watched_user_id="U_DARIN",
            )
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual([("C1", "U_DARIN", "draft reply", "100.1")], slack.ephemerals)

    def test_manager_does_not_post_ephemeral_progress_after_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger(
                "EvUserThread",
                channel="C1",
                ts="100.2",
                thread_ts="100.1",
                trigger_type="user_mention",
                text="<@U_DARIN> draft my reply",
            )
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="output", message="draft reply"),
                    HarnessEvent(type="usage", usage=TokenUsage(input_tokens=10, output_tokens=2)),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                watched_user_id="U_DARIN",
            )
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual([("C1", "U_DARIN", "draft reply", "100.1")], slack.ephemerals)

    def test_manager_dm_handoff_for_root_user_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger(
                "EvRootUser",
                channel="C1",
                ts="100.1",
                trigger_type="user_mention",
                text="<@U_DARIN> draft my reply",
            )
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="drafting"),
                    HarnessEvent(type="output", message="draft reply"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                watched_user_id="U_DARIN",
            )
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual(["U_DARIN"], slack.opened_dms)
            handoff = slack.messages[0]
            self.assertEqual("D_U_DARIN", handoff[0])
            self.assertIsNone(handoff[1])
            self.assertEqual(
                "Hi, here is the <https://slack.example/archives/C1/p1001|open thread> you are tagged on. "
                "Let me help draft a reply.",
                handoff[2],
            )
            self.assertEqual({"unfurl_links": True, "unfurl_media": False}, slack.message_options[0])
            self.assertEqual(("D_U_DARIN", "900.2", "draft reply"), slack.updates[-1])

    def test_manager_dm_handoff_posts_directly_to_user_without_opening_dm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger(
                "EvRootUserDirect",
                channel="C1",
                ts="100.1",
                trigger_type="user_mention",
                text="<@U_DARIN> draft my reply",
            )
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = DirectMessageSlack()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="progress", message="drafting"),
                    HarnessEvent(type="output", message="draft reply"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                watched_user_id="U_DARIN",
            )
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual([], slack.opened_dms)
            self.assertEqual(
                (
                    "D_REAL_U_DARIN",
                    None,
                    "Hi, here is the <https://slack.example/archives/C1/p1001|open thread> you are tagged on. "
                    "Let me help draft a reply.",
                ),
                slack.messages[0],
            )
            self.assertEqual(("D_REAL_U_DARIN", "900.1", "draft reply"), slack.updates[-1])

    def test_manager_dm_handoff_falls_back_when_permalink_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger(
                "EvRootUser",
                channel="C1",
                ts="100.1",
                trigger_type="user_mention",
                text="<@U_DARIN> draft my reply",
            )
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FailingPermalinkSlack()
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="output", message="draft reply"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                watched_user_id="U_DARIN",
                event_output=terminal.append,
            )
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual(["U_DARIN"], slack.opened_dms)
            self.assertEqual(
                "Hi, here is the <https://slack.com/app_redirect?channel=C1&message_ts=100.1|open thread> "
                "you are tagged on. Let me help draft a reply.",
                slack.messages[0][2],
            )
            self.assertEqual({"unfurl_links": False, "unfurl_media": False}, slack.message_options[0])
            self.assertEqual(("D_U_DARIN", "900.1", "draft reply"), slack.messages[-1])
            self.assertTrue(any("permalink lookup failed" in line for line in terminal))

    def test_manager_dm_handoff_uses_workspace_url_when_permalink_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger(
                "EvRootUser",
                channel="C1",
                ts="100.1",
                trigger_type="user_mention",
                text="<@U_DARIN> draft my reply",
            )
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FailingPermalinkWorkspaceSlack()
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="output", message="draft reply"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                watched_user_id="U_DARIN",
            )
            try:
                asyncio.run(manager.run_until_idle())
            finally:
                manager.close()

            self.assertEqual(
                "Hi, here is the <https://paofuanddddd.slack.com/archives/C1/p1001|open thread> you are tagged on. "
                "Let me help draft a reply.",
                slack.messages[0][2],
            )
            self.assertEqual({"unfurl_links": True, "unfurl_media": False}, slack.message_options[0])

    def test_manager_logs_task_started_to_terminal_not_slack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp), event_output=terminal.append)
            try:
                asyncio.run(manager.run_until_idle())
                task_id = manager.db.execute("SELECT id FROM tasks WHERE session_id = ?", (session.id,)).fetchone()["id"]
                started_count = manager.db.execute(
                    "SELECT COUNT(*) AS count FROM task_events WHERE task_id = ? AND event_type = 'harness.started'",
                    (task_id,),
                ).fetchone()["count"]
            finally:
                manager.close()

            self.assertEqual(1, started_count)
            self.assertNotIn(("D1", "100.1", f"Started task {task_id}."), slack.messages)
            self.assertIn(f"session {session.id} task {task_id} started", terminal)
            self.assertIn(f"session {session.id} task {task_id} completed", terminal)

    def test_manager_logs_mapped_harness_and_slack_progress_events_to_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}),
                    HarnessEvent(type="tool_use", message="finance: NBIS", payload={"tool_name": "web_search"}),
                    HarnessEvent(type="output", message="final answer"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(
                path,
                adapters={"scripted": adapter},
                slack=slack,
                workspace=Path(tmp),
                event_output=terminal.append,
            )
            try:
                asyncio.run(manager.run_until_idle())
                task_id = manager.db.execute("SELECT id FROM tasks WHERE session_id = ?", (session.id,)).fetchone()["id"]
            finally:
                manager.close()

            self.assertIn(f"session {session.id} task {task_id} tool_use web_search: web search", terminal)
            self.assertIn(f"session {session.id} task {task_id} slack progress posted ts=900.1", terminal)
            self.assertIn(f"session {session.id} task {task_id} tool_use web_search: finance: NBIS", terminal)
            self.assertIn(f"session {session.id} task {task_id} slack progress updated ts=900.1", terminal)
            self.assertIn(f"session {session.id} task {task_id} output: final answer", terminal)
            self.assertIn(f"session {session.id} task {task_id} slack final updated ts=900.1", terminal)

    def test_manager_does_not_duplicate_adapter_started_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.commit()
            db.close()

            slack = FakeSlackReplies()
            terminal: list[str] = []
            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp), event_output=terminal.append)
            try:
                asyncio.run(manager.run_until_idle())
                started_messages = [message for message in terminal if message.endswith(" started")]
            finally:
                manager.close()

            self.assertEqual(1, len(started_messages))

    def test_manager_rehydrates_running_session_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innie.db"
            db = connect(path)
            initialize_schema(db)
            item = make_trigger("Ev1")
            persist_trigger(db, item)
            session = resolve_session_for_trigger(db, item, harness_id="scripted")
            enqueue_trigger(db, session=session, trigger=item)
            db.execute("UPDATE sessions SET status = 'running' WHERE id = ?", (session.id,))
            db.commit()
            db.close()

            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="output", message="done"),
                    HarnessEvent(type="completed"),
                ]
            )
            manager = SessionManager(path, adapters={"scripted": adapter}, workspace=Path(tmp))
            try:
                self.assertEqual([session.id], manager.hydrate())
                asyncio.run(manager.run_until_idle())
                row = manager.db.execute("SELECT status FROM sessions WHERE id = ?", (session.id,)).fetchone()
            finally:
                manager.close()

            self.assertEqual("idle", row["status"])


if __name__ == "__main__":
    unittest.main()
