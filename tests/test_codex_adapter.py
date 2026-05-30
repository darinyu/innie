from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from innie.adapters.codex import CodexCliAdapter, CodexSessionAdapter
from innie.harness import TaskRequest
from innie.prompts import load_harness_system_prompt


SYSTEM_PROMPT_ARG = f"developer_instructions={json.dumps(load_harness_system_prompt())}"


class FakeProcess:
    def __init__(self, lines: list[dict | str], returncode: int = 0, stderr_lines: list[str] | None = None) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(lines)
        self.stderr = FakeStdout(stderr_lines or [], json_lines=False)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15


class FakeStdin:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False
        self.waited_closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited_closed = True


class FakeStdout:
    def __init__(self, lines: list[dict | str], *, json_lines: bool = True) -> None:
        self._lines = lines
        self._json_lines = json_lines

    def __aiter__(self):
        self._iter = iter(self._lines)
        return self

    async def __anext__(self) -> bytes:
        try:
            line = next(self._iter)
        except StopIteration:
            raise StopAsyncIteration
        await asyncio.sleep(0)
        if self._json_lines:
            return (json.dumps(line) + "\n").encode("utf-8")
        return (str(line) + "\n").encode("utf-8")


class CodexCliAdapterTest(unittest.TestCase):
    def test_start_session_returns_session_scoped_adapter(self) -> None:
        adapter = CodexCliAdapter()

        async def run() -> CodexSessionAdapter:
            return await adapter.start_session(
                session_id="sess_1",
                workspace="/tmp/work",
                recovery_context={"harness_resume_id": "019e-thread"},
            )

        session_adapter = asyncio.run(run())

        self.assertIsInstance(session_adapter, CodexSessionAdapter)
        self.assertEqual("sess_1", session_adapter.session_id)
        self.assertEqual("/tmp/work", session_adapter.workspace)
        self.assertEqual({"harness_resume_id": "019e-thread"}, session_adapter.recovery_context)

    def test_start_task_uses_codex_resume_when_recovery_context_has_resume_id(self) -> None:
        process = FakeProcess([])
        calls: list[tuple[tuple[str, ...], str]] = []

        async def spawn(*args: str, cwd: str):
            calls.append((args, cwd))
            return process

        adapter = CodexCliAdapter(spawn=spawn)

        async def run() -> None:
            await adapter.start_task(
                TaskRequest(
                    task_id="task_1",
                    session_id="sess_1",
                    goal="follow up",
                    workspace="/tmp/work",
                    output_target="slack:D1:100.1",
                    execution_mode="autonomous",
                    recovery_context={"harness_resume_id": "019e-thread"},
                )
            )

        asyncio.run(run())

        self.assertEqual(
            ("codex", "exec", "resume", "--json", "-c", SYSTEM_PROMPT_ARG, "019e-thread", "-"),
            calls[0][0],
        )
        self.assertEqual("/tmp/work", calls[0][1])
        self.assertEqual(b"follow up", process.stdin.data)

    def test_start_task_includes_opt_in_extra_codex_exec_args(self) -> None:
        process = FakeProcess([])
        calls: list[tuple[tuple[str, ...], str]] = []

        async def spawn(*args: str, cwd: str):
            calls.append((args, cwd))
            return process

        adapter = CodexCliAdapter(spawn=spawn)

        async def run() -> None:
            await adapter.start_task(
                TaskRequest(
                    task_id="task_1",
                    session_id="sess_1",
                    goal="write tests",
                    workspace="/tmp/work",
                    output_target="slack:D1:100.1",
                    execution_mode="autonomous",
                    recovery_context={},
                )
            )

        with mock.patch.dict("os.environ", {"INNIE_CODEX_EXEC_ARGS": "--sandbox danger-full-access"}):
            asyncio.run(run())

        self.assertEqual(
            (
                "codex",
                "exec",
                "--sandbox",
                "danger-full-access",
                "--json",
                "-c",
                SYSTEM_PROMPT_ARG,
                "--cd",
                "/tmp/work",
                "-",
            ),
            calls[0][0],
        )
        self.assertEqual(b"write tests", process.stdin.data)

    def test_maps_thread_started_to_resume_event(self) -> None:
        process = FakeProcess(
            [
                {"type": "thread.started", "thread_id": "019e-thread"},
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run():
            handle = await adapter.start_task(request)
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("resume", events[0].type)
        self.assertEqual("019e-thread", events[0].payload["resume_id"])

    def test_maps_json_events_to_normalized_harness_events(self) -> None:
        process = FakeProcess(
            [
                {"type": "session.started"},
                {"type": "agent_message_delta", "delta": "working"},
                {"type": "token_count", "input_tokens": 10, "output_tokens": 5},
                {"type": "agent_message", "message": "final answer"},
                {"type": "session.finished"},
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(request)
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual(
            [
                ("started", "Codex started."),
                ("progress", "working"),
                ("usage", None),
                ("output", "final answer"),
                ("completed", "Codex completed."),
            ],
            events,
        )

    def test_progress_and_output_events_include_dash_phase_metadata(self) -> None:
        process = FakeProcess(
            [
                {"type": "reasoning_summary", "summary": "checking sources"},
                {"type": "agent_message", "message": "final answer"},
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="research CBRS",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(request)
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("phase", events[0].payload["_innie_phase"]["role"])
        self.assertEqual("reasoning", events[0].payload["_innie_phase"]["kind"])
        self.assertEqual("Reasoning summary: checking sources", events[0].payload["_innie_phase"]["title"])
        self.assertEqual("final", events[1].payload["_innie_phase"]["role"])

    def test_default_spawn_pipes_prompt_and_keeps_stderr_separate(self) -> None:
        process = FakeProcess([])

        async def run() -> None:
            with mock.patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
                adapter = CodexCliAdapter()
                await adapter.start_task(
                    TaskRequest(
                        task_id="task_1",
                        session_id="sess_1",
                        goal="write tests",
                        workspace="/tmp/work",
                        output_target="slack:D1:100.1",
                        execution_mode="autonomous",
                        recovery_context={},
                    )
                )

            self.assertEqual("-", spawn.call_args.args[-1])
            self.assertIn("-c", spawn.call_args.args)
            self.assertIn(SYSTEM_PROMPT_ARG, spawn.call_args.args)
            self.assertEqual(asyncio.subprocess.PIPE, spawn.call_args.kwargs["stdin"])
            self.assertEqual(asyncio.subprocess.PIPE, spawn.call_args.kwargs["stdout"])
            self.assertEqual(asyncio.subprocess.PIPE, spawn.call_args.kwargs["stderr"])
            self.assertEqual(b"write tests", process.stdin.data)
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdin.waited_closed)

        asyncio.run(run())

    def test_stderr_is_reported_on_failure_not_streamed_as_progress(self) -> None:
        process = FakeProcess([], returncode=1, stderr_lines=["state warning", "codex failed"])

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(request)
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual([("failed", "Codex exited with status 1: state warning; codex failed")], events)

    def test_maps_responses_item_completed_assistant_message_to_output(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "hello from codex"},
                        ],
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(request)
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertIn(("output", "hello from codex"), events)

    def test_maps_codex_item_completed_agent_message_to_output(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "hello from agent item",
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(request)
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertIn(("output", "hello from agent item"), events)

    def test_buffers_intermediate_agent_messages_as_progress_and_outputs_only_last_message(self) -> None:
        process = FakeProcess(
            [
                {"type": "agent_message", "message": "I am checking the repo first."},
                {
                    "type": "item.started",
                    "item": {
                        "type": "web_search_call",
                        "query": "innie slack progress widget",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "I found the relevant code path.",
                    },
                },
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 11,
                        "output_tokens": 7,
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(request)
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual(
            [
                ("progress", "I am checking the repo first."),
                ("tool_use", "innie slack progress widget"),
                ("usage", None),
                ("output", "I found the relevant code path."),
                ("completed", "Codex completed."),
            ],
            events,
        )

    def test_maps_codex_web_search_item_to_tool_widget_event(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "item.started",
                    "item": {
                        "type": "web_search_call",
                        "query": "pricing model",
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(request)
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("tool_use", events[0].type)
        self.assertEqual("pricing model", events[0].message)
        self.assertEqual("web_search", events[0].payload["tool_name"])

    def test_maps_codex_web_search_started_item_to_generic_tool_widget_event(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "item.started",
                    "item": {
                        "action": {"type": "other"},
                        "id": "ws_1",
                        "query": "",
                        "type": "web_search",
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(request)
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("tool_use", events[0].type)
        self.assertEqual("web search", events[0].message)
        self.assertEqual("web_search", events[0].payload["tool_name"])

    def test_maps_codex_web_search_completed_query_to_tool_widget_event(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "item.completed",
                    "item": {
                        "action": {
                            "queries": [
                                "May 22 2026 stock market today Reuters Wall Street S&P Nasdaq Dow",
                            ],
                            "type": "search",
                        },
                        "id": "ws_1",
                        "query": "May 22 2026 stock market today Reuters Wall Street S&P Nasdaq Dow ...",
                        "type": "web_search",
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(request)
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("tool_use", events[0].type)
        self.assertEqual("May 22 2026 stock market today Reuters Wall Street S&P Nasdaq Dow ...", events[0].message)
        self.assertEqual("web_search", events[0].payload["tool_name"])

    def test_maps_turn_completed_usage(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 11,
                        "output_tokens": 7,
                        "cache_read_input_tokens": 5,
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(request)
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("usage", events[0].type)
        self.assertEqual(11, events[0].usage.input_tokens)
        self.assertEqual(7, events[0].usage.output_tokens)
        self.assertEqual(5, events[0].usage.cache_read_tokens)

    def test_maps_codex_cached_input_tokens_to_cache_read_tokens(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 7,
                        "cached_input_tokens": 42,
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(request)
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("usage", events[0].type)
        self.assertEqual(42, events[0].usage.cache_read_tokens)
        self.assertEqual(0.42, events[0].usage.cache_hit_rate)

    def test_verbose_logs_unknown_codex_event_without_streaming_private_reasoning(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "mystery.reasoning_event",
                    "summary": "visible summary",
                    "chain_of_thought": "never show this",
                },
            ]
        )
        diagnostics: list[str] = []

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn, verbose=True, output=diagnostics.append)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> None:
            handle = await adapter.start_task(request)
            async for _event in adapter.stream_events(handle.task_id):
                pass

        asyncio.run(run())

        self.assertIn("codex task=task_1 event ignored: type=mystery.reasoning_event", "\n".join(diagnostics))
        self.assertIn('"summary":"visible summary"', "\n".join(diagnostics))
        self.assertNotIn("never show this", "\n".join(diagnostics))

    def test_maps_codex_reasoning_summary_to_progress_not_private_reasoning(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "reasoning_summary_delta",
                    "delta": "checking the repo",
                    "chain_of_thought": "never show this",
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(request)
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertIn(("progress", "Reasoning summary: checking the repo"), events)
        self.assertNotIn("never show this", "\n".join(message or "" for _, message in events))


if __name__ == "__main__":
    unittest.main()
