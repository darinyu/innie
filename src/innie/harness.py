from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


HarnessEventType = Literal[
    "started",
    "progress",
    "tool_use",
    "tool_result",
    "output",
    "usage",
    "completed",
    "failed",
    "canceled",
]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_read_tokens / self.input_tokens if self.input_tokens else 0.0


@dataclass(frozen=True)
class HarnessEvent:
    type: HarnessEventType
    message: str | None = None
    usage: TokenUsage | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessArtifact:
    kind: str
    path: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessCapabilities:
    supports_streaming: bool = False
    supports_resume: bool = False
    supports_structured_artifacts: bool = False
    supports_native_approval: bool = False
    supports_autonomous_mode: bool = False
    supports_subagents: bool = False


@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    session_id: str
    goal: str
    workspace: str
    output_target: str
    execution_mode: str
    recovery_context: dict[str, Any]


@dataclass(frozen=True)
class TaskHandle:
    task_id: str
    harness_id: str
    resume_id: str | None = None


class HarnessAdapter(Protocol):
    harness_id: str
    capabilities: HarnessCapabilities

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        ...

    async def send_input(self, task_id: str, input: str) -> None:
        ...

    async def cancel_task(self, task_id: str) -> None:
        ...

    def stream_events(self, task_id: str) -> AsyncIterator[HarnessEvent]:
        ...

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        ...


class ScriptedHarnessAdapter:
    harness_id = "scripted"
    capabilities = HarnessCapabilities(
        supports_streaming=True,
        supports_autonomous_mode=True,
        supports_structured_artifacts=True,
    )

    def __init__(self, *, events: list[HarnessEvent], artifacts: list[HarnessArtifact] | None = None) -> None:
        self._events = events
        self._artifacts = artifacts or []
        self._task_ids: set[str] = set()
        self.canceled: list[str] = []

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        self._task_ids.add(request.task_id)
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError("mid-turn input is not supported by the scripted adapter")

    async def cancel_task(self, task_id: str) -> None:
        self.canceled.append(task_id)

    async def stream_events(self, task_id: str) -> AsyncIterator[HarnessEvent]:
        if task_id not in self._task_ids:
            raise KeyError(task_id)
        for event in self._events:
            yield event

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        if task_id not in self._task_ids:
            raise KeyError(task_id)
        return list(self._artifacts)
