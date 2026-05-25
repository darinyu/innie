from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from innie.adapters.claude import ClaudeCliAdapter, ClaudeSessionAdapter
from innie.config import write_secrets
from innie.harness import HarnessEvent, TaskRequest
from innie.prompts import load_harness_system_prompt


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


def task_request(*, resume_id: str | None = None) -> TaskRequest:
    recovery_context = {}
    if resume_id is not None:
        recovery_context["resume_id"] = resume_id
    return TaskRequest(
        task_id="task_1",
        session_id="sess_1",
        goal="write tests",
        workspace="/tmp/work",
        output_target="slack:D1:100.1",
        execution_mode="autonomous",
        recovery_context=recovery_context,
    )


class ClaudeCliAdapterTest(unittest.TestCase):
    def test_start_session_returns_session_scoped_adapter(self) -> None:
        adapter = ClaudeCliAdapter()

        async def run() -> ClaudeSessionAdapter:
            return await adapter.start_session(
                session_id="sess_1",
                workspace="/tmp/work",
                recovery_context={"harness_resume_id": "claude-session-1"},
            )

        session_adapter = asyncio.run(run())

        self.assertIsInstance(session_adapter, ClaudeSessionAdapter)
        self.assertEqual("sess_1", session_adapter.session_id)
        self.assertEqual("/tmp/work", session_adapter.workspace)
        self.assertEqual({"harness_resume_id": "claude-session-1"}, session_adapter.recovery_context)

    def test_default_spawn_pipes_prompt_and_uses_stream_json_print_mode(self) -> None:
        process = FakeProcess([])

        async def run() -> None:
            with mock.patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
                adapter = ClaudeCliAdapter()
                await adapter.start_task(task_request())

            self.assertEqual(
                (
                    "claude",
                    "-p",
                    "--verbose",
                    "--permission-mode",
                    "auto",
                    "--output-format",
                    "stream-json",
                    "--input-format",
                    "text",
                    "--append-system-prompt",
                    load_harness_system_prompt(),
                ),
                spawn.call_args.args,
            )
            self.assertEqual(asyncio.subprocess.PIPE, spawn.call_args.kwargs["stdin"])
            self.assertEqual(asyncio.subprocess.PIPE, spawn.call_args.kwargs["stdout"])
            self.assertEqual(asyncio.subprocess.PIPE, spawn.call_args.kwargs["stderr"])
            self.assertEqual(b"write tests", process.stdin.data)
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdin.waited_closed)

        asyncio.run(run())

    def test_start_task_adds_resume_flag_when_recovery_context_has_resume_id(self) -> None:
        process = FakeProcess([])

        async def spawn(*args: str, cwd: str):
            self.assertEqual(
                (
                    "claude",
                    "-p",
                    "--verbose",
                    "--permission-mode",
                    "auto",
                    "--output-format",
                    "stream-json",
                    "--input-format",
                    "text",
                    "--append-system-prompt",
                    load_harness_system_prompt(),
                    "--resume",
                    "claude-session-1",
                ),
                args,
            )
            return process

        adapter = ClaudeCliAdapter(spawn=spawn)

        async def run() -> None:
            await adapter.start_task(task_request(resume_id="claude-session-1"))

        asyncio.run(run())

    def test_start_task_adds_slack_mcp_config_when_bot_token_exists(self) -> None:
        process = FakeProcess([])
        calls: list[tuple[tuple[str, ...], str, dict[str, str] | None]] = []

        async def spawn(*args: str, cwd: str, env: dict[str, str] | None = None):
            calls.append((args, cwd, env))
            return process

        adapter = ClaudeCliAdapter(spawn=spawn)

        with tempfile.TemporaryDirectory() as tmp:
            write_secrets(Path(tmp), {"slack_bot_token": "xoxb-test-token"})
            request = task_request()
            request = TaskRequest(
                task_id=request.task_id,
                session_id=request.session_id,
                goal=request.goal,
                workspace=tmp,
                output_target=request.output_target,
                execution_mode=request.execution_mode,
                recovery_context={
                    "slack_channel_id": "C1",
                    "slack_message_ts": "100.2",
                    "slack_thread_ts": "100.1",
                },
            )

            async def run() -> None:
                await adapter.start_task(request)

            asyncio.run(run())

            args, cwd, env = calls[0]
            self.assertIn("--mcp-config", args)
            config_path = Path(args[args.index("--mcp-config") + 1])
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIn("innie_slack", config["mcpServers"])
            self.assertEqual(["-m", "innie.slack_mcp", "--workspace", str(Path(tmp).resolve())], config["mcpServers"]["innie_slack"]["args"])
            self.assertEqual("xoxb-test-token", env["INNIE_SLACK_BOT_TOKEN"])
            self.assertNotIn("INNIE_SLACK_CHANNEL", env)
            self.assertNotIn("INNIE_SLACK_THREAD_TS", env)
            self.assertNotIn("INNIE_SLACK_MESSAGE_TS", env)
            self.assertEqual(b"write tests", process.stdin.data)

    def test_maps_stream_json_events_and_captures_session_id_for_resume(self) -> None:
        process = FakeProcess(
            [
                {"type": "system", "subtype": "init", "session_id": "claude-session-1"},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
                {
                    "type": "result",
                    "result": "final answer",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_input_tokens": 4,
                        "cache_creation_input_tokens": 6,
                    },
                    "total_cost_usd": 0.02,
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = ClaudeCliAdapter(spawn=spawn)

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(task_request())
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("started", events[0].type)
        self.assertEqual("Claude started.", events[0].message)
        self.assertEqual("claude-session-1", events[0].payload["resume_id"])
        self.assertEqual(("progress", "working"), (events[1].type, events[1].message))
        self.assertEqual("usage", events[2].type)
        self.assertEqual(20, events[2].usage.input_tokens)
        self.assertEqual(5, events[2].usage.output_tokens)
        self.assertEqual(4, events[2].usage.cache_read_tokens)
        self.assertEqual(6, events[2].usage.cache_write_tokens)
        self.assertEqual(0.02, events[2].usage.cost_usd)
        self.assertEqual(("output", "final answer"), (events[3].type, events[3].message))
        self.assertEqual(("completed", "Claude completed."), (events[4].type, events[4].message))
        self.assertEqual("phase", events[1].payload["_innie_phase"]["role"])
        self.assertEqual("assistant", events[1].payload["_innie_phase"]["kind"])
        self.assertEqual("working", events[1].payload["_innie_phase"]["title"])
        self.assertEqual("final", events[3].payload["_innie_phase"]["role"])

    def test_maps_tool_use_content_to_tool_event(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "git status --short"},
                            }
                        ]
                    },
                },
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = ClaudeCliAdapter(spawn=spawn)

        async def run() -> list[HarnessEvent]:
            handle = await adapter.start_task(task_request())
            return [event async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual("tool_use", events[0].type)
        self.assertEqual("git status --short", events[0].message)
        self.assertEqual("Bash", events[0].payload["tool_name"])

    def test_stderr_is_reported_on_failure_not_streamed_as_progress(self) -> None:
        process = FakeProcess([], returncode=1, stderr_lines=["wrapper warning", "claude failed"])

        async def spawn(*args: str, cwd: str):
            return process

        adapter = ClaudeCliAdapter(spawn=spawn)

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(task_request())
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())

        self.assertEqual([("failed", "Claude exited with status 1: wrapper warning; claude failed")], events)

    def test_verbose_suppresses_ignored_system_hook_payloads(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "system",
                    "subtype": "hook_response",
                    "hook_event": "SessionStart",
                    "output": "large startup context",
                    "stdout": "secret stdout",
                },
            ]
        )
        diagnostics: list[str] = []

        async def spawn(*args: str, cwd: str):
            return process

        adapter = ClaudeCliAdapter(spawn=spawn, verbose=True, output=diagnostics.append)

        async def run() -> None:
            handle = await adapter.start_task(task_request())
            async for _event in adapter.stream_events(handle.task_id):
                pass

        asyncio.run(run())

        self.assertEqual([], diagnostics)

    def test_verbose_suppresses_ignored_system_subagent_payloads(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "system",
                    "subtype": "task_started",
                    "subagent_type": "Explore",
                    "task_id": "subtask_1",
                    "description": "Deep research",
                    "prompt": "large subagent prompt",
                },
                {
                    "type": "system",
                    "subtype": "task_progress",
                    "subagent_type": "Explore",
                    "task_id": "subtask_1",
                    "description": "Reading file",
                    "usage": {"total_tokens": 123},
                },
            ]
        )
        diagnostics: list[str] = []

        async def spawn(*args: str, cwd: str):
            return process

        adapter = ClaudeCliAdapter(spawn=spawn, verbose=True, output=diagnostics.append)

        async def run() -> None:
            handle = await adapter.start_task(task_request())
            async for _event in adapter.stream_events(handle.task_id):
                pass

        asyncio.run(run())

        self.assertEqual([], diagnostics)

    def test_verbose_suppresses_ignored_assistant_thinking_payloads(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "private chain of thought",
                                "signature": "signed-thinking-payload",
                            }
                        ]
                    },
                },
            ]
        )
        diagnostics: list[str] = []

        async def spawn(*args: str, cwd: str):
            return process

        adapter = ClaudeCliAdapter(spawn=spawn, verbose=True, output=diagnostics.append)

        async def run() -> None:
            handle = await adapter.start_task(task_request())
            async for _event in adapter.stream_events(handle.task_id):
                pass

        asyncio.run(run())

        self.assertEqual([], diagnostics)

    def test_verbose_redacts_unknown_ignored_payloads(self) -> None:
        process = FakeProcess(
            [
                {
                    "type": "mystery",
                    "summary": "visible summary",
                    "output": "large private context",
                    "prompt": "large prompt context",
                    "message": {"content": [{"signature": "signed-thinking-payload"}]},
                },
            ]
        )
        diagnostics: list[str] = []

        async def spawn(*args: str, cwd: str):
            return process

        adapter = ClaudeCliAdapter(spawn=spawn, verbose=True, output=diagnostics.append)

        async def run() -> None:
            handle = await adapter.start_task(task_request())
            async for _event in adapter.stream_events(handle.task_id):
                pass

        asyncio.run(run())

        output = "\n".join(diagnostics)
        self.assertIn("claude task=task_1 event ignored: type=mystery", output)
        self.assertIn('"summary":"visible summary"', output)
        self.assertNotIn("large private context", output)
        self.assertNotIn("large prompt context", output)
        self.assertNotIn("signed-thinking-payload", output)


if __name__ == "__main__":
    unittest.main()
