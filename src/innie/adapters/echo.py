from __future__ import annotations

from collections.abc import AsyncIterator

from ..harness import HarnessArtifact, HarnessCapabilities, HarnessEvent, TaskHandle, TaskRequest


class EchoAdapter:
    harness_id = "echo"
    capabilities = HarnessCapabilities(
        supports_streaming=True,
        supports_autonomous_mode=True,
    )

    def __init__(self) -> None:
        self._requests: dict[str, TaskRequest] = {}

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        self._requests[request.task_id] = request
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError("Echo adapter does not support mid-turn input")

    async def cancel_task(self, task_id: str) -> None:
        return None

    async def stream_events(self, task_id: str) -> AsyncIterator[HarnessEvent]:
        request = self._requests[task_id]
        yield HarnessEvent(type="started", message="Echo started.")
        yield HarnessEvent(type="output", message=request.goal)
        yield HarnessEvent(type="completed", message="Echo completed.")

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        return []
