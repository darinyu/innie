from __future__ import annotations

import asyncio
import unittest

from innie.adapters.echo import EchoAdapter
from innie.harness import TaskRequest


class EchoAdapterTest(unittest.TestCase):
    def test_echoes_task_goal_as_output(self) -> None:
        adapter = EchoAdapter()
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="hello from slack",
            workspace=".",
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
                ("started", "Echo started."),
                ("output", "hello from slack"),
                ("completed", "Echo completed."),
            ],
            events,
        )


if __name__ == "__main__":
    unittest.main()
