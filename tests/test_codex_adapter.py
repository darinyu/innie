from __future__ import annotations

import asyncio
import json
import unittest

from innie.adapters.codex import CodexCliAdapter
from innie.harness import TaskRequest


class FakeProcess:
    def __init__(self, lines: list[dict], returncode: int = 0) -> None:
        self.stdout = FakeStdout(lines)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15


class FakeStdout:
    def __init__(self, lines: list[dict]) -> None:
        self._lines = lines

    def __aiter__(self):
        self._iter = iter(self._lines)
        return self

    async def __anext__(self) -> bytes:
        try:
            line = next(self._iter)
        except StopIteration:
            raise StopAsyncIteration
        await asyncio.sleep(0)
        return (json.dumps(line) + "\n").encode("utf-8")


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


if __name__ == "__main__":
    unittest.main()
