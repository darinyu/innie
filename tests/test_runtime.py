from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.harness import HarnessCapabilities, HarnessEvent, ScriptedHarnessAdapter, TaskHandle
from innie.inbox import enqueue_trigger
from innie.runtime import SessionManager
from innie.sessions import resolve_session_for_trigger
from innie.slack_events import SlackTrigger, persist_trigger
from innie.progress import SLACK_FINAL_TEXT_LIMIT, SLACK_SECTION_TEXT_LIMIT


class FakeSlackReplies:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.updates: list[tuple[str, str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self.message_blocks: list[list[dict] | None] = []
        self.update_blocks: list[list[dict] | None] = []
        self._next_ts = 1

    def post_message(self, *, channel: str, thread_ts: str, text: str, blocks: list[dict] | None = None) -> str:
        self.messages.append((channel, thread_ts, text))
        self.message_blocks.append(blocks)
        ts = f"900.{self._next_ts}"
        self._next_ts += 1
        return ts

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
    def post_message(self, *, channel: str, thread_ts: str, text: str, blocks: list[dict] | None = None) -> str:
        if text in {"Innie is working", "working", "first", "second"}:
            raise RuntimeError("chat.postMessage failed: rate_limited")
        return super().post_message(channel=channel, thread_ts=thread_ts, text=text, blocks=blocks)


class FailingProgressUpdateSlack(FakeSlackReplies):
    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
        if text in {"Innie is working", "working", "first", "second"}:
            raise RuntimeError("chat.update failed: rate_limited")
        super().update_message(channel=channel, ts=ts, text=text, blocks=blocks)


class LengthLimitedSlack(FakeSlackReplies):
    def post_message(self, *, channel: str, thread_ts: str, text: str, blocks: list[dict] | None = None) -> str:
        if len(text) > SLACK_SECTION_TEXT_LIMIT:
            raise RuntimeError("chat.postMessage failed: msg_too_long")
        return super().post_message(channel=channel, thread_ts=thread_ts, text=text, blocks=blocks)

    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
        if len(text) > SLACK_SECTION_TEXT_LIMIT:
            raise RuntimeError("chat.update failed: msg_too_long")
        super().update_message(channel=channel, ts=ts, text=text, blocks=blocks)


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


def make_trigger(event_id: str, channel: str = "D1", ts: str = "100.1", thread_ts: str | None = None) -> SlackTrigger:
    return SlackTrigger(
        event_id=event_id,
        trigger_type="dm",
        channel_id=channel,
        message_ts=ts,
        thread_ts=thread_ts,
        sender_user_id="U1",
        text=f"text {event_id}",
        payload={"event_id": event_id},
    )


class RuntimeTest(unittest.TestCase):
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
            self.assertEqual("019e-thread", adapter.recovery_contexts[1]["harness_resume_id"])

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
