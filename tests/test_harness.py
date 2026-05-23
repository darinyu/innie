from __future__ import annotations

import asyncio
import unittest

from innie.harness import (
    HarnessArtifact,
    HarnessEvent,
    ScriptedHarnessAdapter,
    TaskRequest,
    TokenUsage,
)


class HarnessContractTest(unittest.TestCase):
    def test_usage_cache_hit_rate_is_zero_without_input_tokens(self) -> None:
        self.assertEqual(0.0, TokenUsage().cache_hit_rate)

    def test_usage_cache_hit_rate_uses_input_tokens(self) -> None:
        usage = TokenUsage(input_tokens=100, cache_read_tokens=25)

        self.assertEqual(0.25, usage.cache_hit_rate)

    def test_scripted_adapter_streams_events_and_collects_artifacts(self) -> None:
        adapter = ScriptedHarnessAdapter(
            events=[
                HarnessEvent(type="started", message="started"),
                HarnessEvent(type="progress", message="running tests"),
                HarnessEvent(type="output", message="done"),
                HarnessEvent(type="completed", message="completed"),
            ],
            artifacts=[HarnessArtifact(kind="summary", path="summary.md")],
        )
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="ship it",
            workspace=".",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> tuple[list[str], list[str]]:
            handle = await adapter.start_task(request)
            event_types = [event.type async for event in adapter.stream_events(handle.task_id)]
            artifacts = await adapter.collect_artifacts(handle.task_id)
            return event_types, [artifact.kind for artifact in artifacts]

        event_types, artifact_kinds = asyncio.run(run())

        self.assertEqual(["started", "progress", "output", "completed"], event_types)
        self.assertEqual(["summary"], artifact_kinds)
        self.assertTrue(adapter.capabilities.supports_streaming)
        self.assertTrue(adapter.capabilities.supports_autonomous_mode)


if __name__ == "__main__":
    unittest.main()
