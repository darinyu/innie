from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from innie.adapters.codex import CodexCliAdapter
from innie.harness import TaskRequest


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
