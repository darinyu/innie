from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import json
from typing import Any

from ..harness import HarnessArtifact, HarnessCapabilities, HarnessEvent, TaskHandle, TaskRequest, TokenUsage


SpawnFn = Callable[..., Awaitable[asyncio.subprocess.Process]]


class CodexCliAdapter:
    harness_id = "codex"
    capabilities = HarnessCapabilities(
        supports_streaming=True,
        supports_resume=False,
        supports_structured_artifacts=False,
        supports_native_approval=False,
        supports_autonomous_mode=True,
        supports_subagents=True,
    )

    def __init__(self, *, spawn: SpawnFn | None = None) -> None:
        self._spawn = spawn or self._default_spawn
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        process = await self._spawn(
            "codex",
            "exec",
            "--json",
            "--cd",
            request.workspace,
            request.goal,
            cwd=request.workspace,
        )
        self._processes[request.task_id] = process
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError("Codex exec does not support mid-turn input")

    async def cancel_task(self, task_id: str) -> None:
        process = self._processes.get(task_id)
        if process is not None and process.returncode is None:
            process.terminate()

    async def stream_events(self, task_id: str):
        process = self._processes[task_id]
        stdout = process.stdout
        if stdout is None:
            yield HarnessEvent(type="failed", message="Codex stdout was not captured")
            return
        async for raw_line in stdout:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                yield HarnessEvent(type="progress", message=line)
                continue
            event = _map_codex_event(payload)
            if event is not None:
                yield event
        returncode = await process.wait()
        if returncode == 0:
            yield HarnessEvent(type="completed", message="Codex completed.")
        else:
            yield HarnessEvent(type="failed", message=f"Codex exited with status {returncode}")

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        return []

    async def _default_spawn(self, *args: str, cwd: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )


def _map_codex_event(payload: dict[str, Any]) -> HarnessEvent | None:
    event_type = str(payload.get("type", ""))
    if event_type in {"session.started", "run.started"}:
        return HarnessEvent(type="started", message="Codex started.", payload=payload)
    if event_type in {"agent_message_delta", "exec_command_begin", "exec_command_output_delta"}:
        message = payload.get("delta") or payload.get("message") or payload.get("command")
        return HarnessEvent(type="progress", message=str(message) if message else None, payload=payload)
    if event_type in {"agent_message", "final_message", "run.completed"}:
        message = payload.get("message") or payload.get("text") or payload.get("last_message")
        return HarnessEvent(type="output", message=str(message) if message else None, payload=payload)
    if event_type in {"token_count", "usage"}:
        return HarnessEvent(
            type="usage",
            usage=TokenUsage(
                input_tokens=int(payload.get("input_tokens", 0) or 0),
                output_tokens=int(payload.get("output_tokens", 0) or 0),
                cache_read_tokens=int(payload.get("cache_read_tokens", 0) or 0),
                cache_write_tokens=int(payload.get("cache_write_tokens", 0) or 0),
                cost_usd=payload.get("cost_usd"),
            ),
            payload=payload,
        )
    if event_type == "session.finished":
        return None
    return None
