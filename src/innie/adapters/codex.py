from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
from contextlib import suppress
import json
from typing import Any

from ..harness import HarnessArtifact, HarnessCapabilities, HarnessEvent, TaskHandle, TaskRequest, TokenUsage
from ..prompts import load_harness_system_prompt


SpawnFn = Callable[..., Awaitable[asyncio.subprocess.Process]]


class CodexCliAdapter:
    harness_id = "codex"
    capabilities = HarnessCapabilities(
        supports_streaming=True,
        supports_resume=True,
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

    async def start_session(self, *, session_id: str, workspace: str, recovery_context: dict[str, Any]):
        return CodexSessionAdapter(self, session_id=session_id, workspace=workspace, recovery_context=recovery_context)

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        resume_id = request.recovery_context.get("harness_resume_id")
        system_prompt_arg = _system_prompt_config_arg()
        if resume_id:
            args = (
                "codex",
                "exec",
                "resume",
                "--json",
                "-c",
                system_prompt_arg,
                str(resume_id),
                "-",
            )
        else:
            args = (
                "codex",
                "exec",
                "--json",
                "-c",
                system_prompt_arg,
                "--cd",
                request.workspace,
                "-",
            )
        process = await self._spawn(*args, cwd=request.workspace)
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
        pending_output: HarnessEvent | None = None
        async for raw_line in stdout:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                yield HarnessEvent(type="progress", message=line, payload=_phase_payload({"message": line}, role="phase", kind="progress", title=line))
                continue
            event = _map_codex_event(payload)
            if event is not None:
                if _is_deferred_output_event(event):
                    if pending_output is not None:
                        yield _output_as_progress(pending_output)
                    pending_output = event
                    continue
                if pending_output is not None and event.type in {"progress", "tool_use", "tool_result"}:
                    yield _output_as_progress(pending_output)
                    pending_output = None
                yield event
            elif self._verbose and self._output is not None:
                self._output(_describe_ignored_event(task_id, payload))
        returncode = await process.wait()
        stderr_task = self._stderr_tasks.pop(task_id, None)
        if stderr_task is not None:
            await stderr_task
        if returncode == 0:
            if pending_output is not None:
                yield pending_output
            yield HarnessEvent(type="completed", message="Codex completed.")
        else:
            message = f"Codex exited with status {returncode}"
            stderr_summary = _stderr_summary(self._stderr_lines.pop(task_id, []))
            if stderr_summary:
                message = f"{message}: {stderr_summary}"
            yield HarnessEvent(type="failed", message=message)

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        return []

    async def _default_spawn(self, *args: str, cwd: str, env: dict[str, str] | None = None) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )


def _system_prompt_config_arg() -> str:
    return f"developer_instructions={json.dumps(load_harness_system_prompt())}"


class CodexSessionAdapter:
    harness_id = "codex"
    capabilities = CodexCliAdapter.capabilities

    def __init__(
        self,
        adapter: CodexCliAdapter,
        *,
        session_id: str,
        workspace: str,
        recovery_context: dict[str, Any],
    ) -> None:
        self._adapter = adapter
        self.session_id = session_id
        self.workspace = workspace
        self.recovery_context = dict(recovery_context)

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        return await self._adapter.start_task(request)

    async def send_input(self, task_id: str, input: str) -> None:
        await self._adapter.send_input(task_id, input)

    async def cancel_task(self, task_id: str) -> None:
        await self._adapter.cancel_task(task_id)

    def stream_events(self, task_id: str):
        return self._adapter.stream_events(task_id)

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        return await self._adapter.collect_artifacts(task_id)

    async def close(self) -> None:
        return None


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
    if event_type == "thread.started":
        thread_id = payload.get("thread_id")
        if thread_id:
            event_payload = dict(payload)
            event_payload["resume_id"] = str(thread_id)
            return HarnessEvent(type="resume", payload=event_payload)
        return None
    if event_type in {"agent_message_delta", "exec_command_begin", "exec_command_output_delta"}:
        message = payload.get("delta") or payload.get("message") or payload.get("command")
        title = str(message) if message else "Codex progress"
        return HarnessEvent(type="progress", message=title, payload=_phase_payload(payload, role="phase", kind="assistant", title=title))
    if event_type in {"reasoning_summary_delta", "reasoning_summary"}:
        message = _extract_text(payload.get("delta") or payload.get("summary") or payload.get("message"))
        return HarnessEvent(
            type="progress",
            message=f"Reasoning summary: {message}" if message else "Reasoning summary updated.",
            payload=_phase_payload(
                payload,
                role="phase",
                kind="reasoning",
                title=f"Reasoning summary: {message}" if message else "Reasoning summary updated.",
            ),
        )
    if event_type in {"agent_message", "final_message", "run.completed"}:
        message = _extract_text(payload.get("message") or payload.get("text") or payload.get("last_message"))
        return HarnessEvent(type="output", message=str(message) if message else None, payload=_phase_payload(payload, role="final", kind="output"))
    if event_type in {"item.completed", "response.output_item.done"}:
        item = payload.get("item") or payload.get("output_item") or payload
        tool_event = _map_tool_item(item)
        if tool_event is not None:
            return tool_event
        if isinstance(item, dict) and (item.get("role") == "assistant" or item.get("type") in {"agent_message", "assistant_message", "message"}):
            message = _extract_text(item)
            if message:
                return HarnessEvent(type="output", message=message, payload=_phase_payload(payload, role="final", kind="output"))
    if event_type in {"item.started", "response.output_item.added"}:
        item = payload.get("item") or payload.get("output_item") or payload
        tool_event = _map_tool_item(item)
        if tool_event is not None:
            return tool_event
    if event_type in {"token_count", "usage", "turn.completed"}:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else payload
        return HarnessEvent(
            type="usage",
            usage=TokenUsage(
                input_tokens=int(usage.get("input_tokens", 0) or usage.get("input", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or usage.get("output", 0) or 0),
                cache_read_tokens=int(
                    usage.get("cache_read_tokens", 0)
                    or usage.get("cache_read_input_tokens", 0)
                    or usage.get("cached_input_tokens", 0)
                    or 0
                ),
                cache_write_tokens=int(usage.get("cache_write_tokens", 0) or usage.get("cache_write_input_tokens", 0) or 0),
                cost_usd=usage.get("cost_usd"),
            ),
            payload=_phase_payload(payload, role="item", kind="usage"),
        )
    if event_type == "session.finished":
        return None
    return None


def _is_deferred_output_event(event: HarnessEvent) -> bool:
    return event.type == "output" and _is_assistant_output_payload(event.payload)


def _is_assistant_output_payload(payload: dict[str, Any]) -> bool:
    event_type = str(payload.get("type", ""))
    if event_type == "agent_message":
        return True
    if event_type in {"item.completed", "response.output_item.done"}:
        item = payload.get("item") or payload.get("output_item") or payload
        return isinstance(item, dict) and (
            item.get("role") == "assistant" or item.get("type") in {"agent_message", "assistant_message", "message"}
        )
    return False


def _output_as_progress(event: HarnessEvent) -> HarnessEvent:
    return HarnessEvent(
        type="progress",
        message=event.message,
        payload=_phase_payload(event.payload, role="phase", kind="assistant", title=event.message or "Assistant progress"),
    )


def _map_tool_item(item: Any) -> HarnessEvent | None:
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "")
    tool_name = _normalize_tool_name(item_type or str(item.get("name") or "tool"))
    if not _looks_like_tool(item_type, item):
        return None
    message = _tool_message(item)
    event_type = "tool_result" if item.get("status") in {"completed", "success"} and _extract_text(item.get("output") or item.get("result")) else "tool_use"
    return HarnessEvent(
        type=event_type,
        message=message or tool_name,
        payload=_phase_payload({"tool_name": tool_name, "item_type": item_type}, role="item", kind=event_type),
    )


def _phase_payload(payload: dict[str, Any], *, role: str, kind: str, title: str | None = None) -> dict[str, Any]:
    event_payload = dict(payload)
    phase = {"role": role, "kind": kind}
    if title:
        phase["title"] = title
    event_payload["_innie_phase"] = phase
    return event_payload


def _looks_like_tool(item_type: str, item: dict[str, Any]) -> bool:
    if item_type.endswith("_call") or item_type.endswith("_execution"):
        return True
    if item_type in {"function_call", "tool_call", "web_search", "web_search_call"}:
        return True
    return "tool" in item or "name" in item and item.get("arguments") is not None


def _normalize_tool_name(item_type: str) -> str:
    if item_type in {"web_search", "web_search_call"}:
        return "web_search"
    if item_type.endswith("_call"):
        return item_type[: -len("_call")]
    return item_type or "tool"


def _tool_message(item: dict[str, Any]) -> str | None:
    for key in ("query", "command", "name", "tool", "message"):
        value = item.get(key)
        if value:
            return str(value)
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        for key in ("query", "command", "path"):
            if arguments.get(key):
                return str(arguments[key])
    action = item.get("action")
    if isinstance(action, dict):
        queries = action.get("queries")
        if isinstance(queries, list) and queries:
            return str(queries[0])
        for key in ("query", "command", "url", "path"):
            if action.get(key):
                return str(action[key])
    if item.get("type") == "web_search":
        return "web search"
    return _extract_text(item.get("output") or item.get("result"))


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
