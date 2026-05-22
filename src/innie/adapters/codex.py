from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
from contextlib import suppress
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

    def __init__(self, *, spawn: SpawnFn | None = None, verbose: bool = False, output: Callable[[str], None] | None = None) -> None:
        self._spawn = spawn or self._default_spawn
        self._verbose = verbose
        self._output = output
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._stderr_lines: dict[str, list[str]] = {}
        self._stderr_tasks: dict[str, asyncio.Task[None]] = {}

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        process = await self._spawn(
            "codex",
            "exec",
            "--json",
            "--cd",
            request.workspace,
            "-",
            cwd=request.workspace,
        )
        await _write_prompt(process, request.goal)
        self._processes[request.task_id] = process
        stderr = getattr(process, "stderr", None)
        if stderr is not None:
            lines: list[str] = []
            self._stderr_lines[request.task_id] = lines
            self._stderr_tasks[request.task_id] = asyncio.create_task(_drain_stderr(stderr, lines))
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
            elif self._verbose and self._output is not None:
                self._output(_describe_ignored_event(task_id, payload))
        returncode = await process.wait()
        stderr_task = self._stderr_tasks.pop(task_id, None)
        if stderr_task is not None:
            await stderr_task
        if returncode == 0:
            yield HarnessEvent(type="completed", message="Codex completed.")
        else:
            message = f"Codex exited with status {returncode}"
            stderr_summary = _stderr_summary(self._stderr_lines.pop(task_id, []))
            if stderr_summary:
                message = f"{message}: {stderr_summary}"
            yield HarnessEvent(type="failed", message=message)

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        return []

    async def _default_spawn(self, *args: str, cwd: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )


async def _write_prompt(process: asyncio.subprocess.Process, prompt: str) -> None:
    stdin = getattr(process, "stdin", None)
    if stdin is None:
        return
    with suppress(BrokenPipeError, ConnectionResetError):
        stdin.write(prompt.encode("utf-8"))
        await stdin.drain()
    stdin.close()
    wait_closed = getattr(stdin, "wait_closed", None)
    if wait_closed is not None:
        with suppress(BrokenPipeError, ConnectionResetError):
            await wait_closed()


async def _drain_stderr(stderr, lines: list[str]) -> None:
    async for raw_line in stderr:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if line:
            lines.append(line)


def _stderr_summary(lines: list[str], *, limit: int = 3) -> str:
    return "; ".join(lines[-limit:])


def _map_codex_event(payload: dict[str, Any]) -> HarnessEvent | None:
    event_type = str(payload.get("type", ""))
    if event_type in {"session.started", "run.started"}:
        return HarnessEvent(type="started", message="Codex started.", payload=payload)
    if event_type in {"agent_message_delta", "exec_command_begin", "exec_command_output_delta"}:
        message = payload.get("delta") or payload.get("message") or payload.get("command")
        return HarnessEvent(type="progress", message=str(message) if message else None, payload=payload)
    if event_type in {"reasoning_summary_delta", "reasoning_summary"}:
        message = _extract_text(payload.get("delta") or payload.get("summary") or payload.get("message"))
        return HarnessEvent(
            type="progress",
            message=f"Reasoning summary: {message}" if message else "Reasoning summary updated.",
            payload=payload,
        )
    if event_type in {"agent_message", "final_message", "run.completed"}:
        message = _extract_text(payload.get("message") or payload.get("text") or payload.get("last_message"))
        return HarnessEvent(type="output", message=str(message) if message else None, payload=payload)
    if event_type in {"item.completed", "response.output_item.done"}:
        item = payload.get("item") or payload.get("output_item") or payload
        if isinstance(item, dict) and (item.get("role") == "assistant" or item.get("type") in {"agent_message", "assistant_message", "message"}):
            message = _extract_text(item)
            if message:
                return HarnessEvent(type="output", message=message, payload=payload)
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


def _extract_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if not isinstance(value, dict):
        return str(value)
    if value.get("type") in {"reasoning", "chain_of_thought"}:
        return None
    for key in ("text", "message", "content", "output_text", "summary"):
        if key in value:
            text = _extract_text(value[key])
            if text:
                return text
    return None


def _describe_ignored_event(task_id: str, payload: dict[str, Any]) -> str:
    event_type = str(payload.get("type") or "unknown")
    keys = ", ".join(sorted(str(key) for key in payload.keys() if key not in {"chain_of_thought", "reasoning"}))
    return f"codex task={task_id} event ignored: type={event_type} keys={keys} payload={_safe_json_preview(payload)}"


def _safe_json_preview(value: Any, *, limit: int = 1200) -> str:
    sanitized = _sanitize(value)
    text = json.dumps(sanitized, sort_keys=True, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {"chain_of_thought", "reasoning"}:
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _sanitize(item)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value
