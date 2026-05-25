from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
from typing import Any
import json

from ..harness import HarnessArtifact, HarnessCapabilities, HarnessEvent, TaskHandle, TaskRequest, TokenUsage
from ..prompts import load_harness_system_prompt
from ..slack_mcp_config import slack_mcp_config_path, slack_mcp_process_env
from .codex import _drain_stderr, _extract_text, _safe_json_preview, _stderr_summary, _write_prompt


SpawnFn = Callable[..., Awaitable[asyncio.subprocess.Process]]


class ClaudeCliAdapter:
    harness_id = "claude"
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
        return ClaudeSessionAdapter(self, session_id=session_id, workspace=workspace, recovery_context=recovery_context)

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        resume_id = _resume_id(request.recovery_context)
        args = [
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
        ]
        mcp_config_path = slack_mcp_config_path(request.workspace)
        if mcp_config_path is not None:
            args.extend(["--mcp-config", mcp_config_path])
        if resume_id is not None:
            args.extend(["--resume", resume_id])
        env = slack_mcp_process_env(request.workspace, request.recovery_context)
        if env is None:
            process = await self._spawn(*args, cwd=request.workspace)
        else:
            process = await self._spawn(*args, cwd=request.workspace, env=env)
        await _write_prompt(process, request.goal)
        self._processes[request.task_id] = process
        stderr = getattr(process, "stderr", None)
        if stderr is not None:
            lines: list[str] = []
            self._stderr_lines[request.task_id] = lines
            self._stderr_tasks[request.task_id] = asyncio.create_task(_drain_stderr(stderr, lines))
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id, resume_id=resume_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError("Claude print mode does not support mid-turn input")

    async def cancel_task(self, task_id: str) -> None:
        process = self._processes.get(task_id)
        if process is not None and process.returncode is None:
            process.terminate()

    async def stream_events(self, task_id: str):
        process = self._processes[task_id]
        stdout = process.stdout
        if stdout is None:
            yield HarnessEvent(type="failed", message="Claude stdout was not captured")
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
            events = _map_claude_events(payload)
            if events:
                for event in events:
                    yield event
            elif self._verbose and self._output is not None and _should_log_ignored_event(payload):
                self._output(_describe_ignored_event(task_id, payload))
        returncode = await process.wait()
        stderr_task = self._stderr_tasks.pop(task_id, None)
        if stderr_task is not None:
            await stderr_task
        if returncode == 0:
            yield HarnessEvent(type="completed", message="Claude completed.")
        else:
            message = f"Claude exited with status {returncode}"
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


class ClaudeSessionAdapter:
    harness_id = "claude"
    capabilities = ClaudeCliAdapter.capabilities

    def __init__(
        self,
        adapter: ClaudeCliAdapter,
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


def _resume_id(recovery_context: dict[str, Any]) -> str | None:
    value = recovery_context.get("resume_id") or recovery_context.get("harness_resume_id")
    return str(value) if value else None


def _map_claude_events(payload: dict[str, Any]) -> list[HarnessEvent]:
    event_type = str(payload.get("type", ""))
    if event_type == "system" and payload.get("subtype") == "init":
        resume_id = payload.get("session_id")
        event_payload = dict(payload)
        if resume_id:
            event_payload["resume_id"] = str(resume_id)
        return [HarnessEvent(type="started", message="Claude started.", payload=event_payload)]
    if event_type == "assistant":
        return _map_message_content(payload.get("message"), payload=payload)
    if event_type == "user":
        return _map_message_content(payload.get("message"), payload=payload)
    if event_type == "result":
        if payload.get("is_error"):
            message = _extract_text(payload.get("result") or payload.get("error") or payload.get("message"))
            return [HarnessEvent(type="failed", message=message or "Claude failed.", payload=payload)]
        events: list[HarnessEvent] = []
        usage = payload.get("usage")
        if isinstance(usage, dict):
            events.append(_usage_event(usage, payload))
        message = _extract_text(payload.get("result"))
        if message:
            events.append(HarnessEvent(type="output", message=message, payload=_phase_payload(payload, role="final", kind="output")))
        return events
    return []


def _map_message_content(message: Any, *, payload: dict[str, Any]) -> list[HarnessEvent]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        text = _extract_text(message)
        return [HarnessEvent(type="progress", message=text, payload=payload)] if text else []
    events: list[HarnessEvent] = []
    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "text":
            text = _extract_text(item)
            if text:
                text_parts.append(text)
        elif item_type == "tool_use":
            events.append(
                HarnessEvent(
                    type="tool_use",
                    message=_tool_message(item),
                    payload=_phase_payload({"tool_name": str(item.get("name") or "tool"), "item_type": item_type}, role="item", kind="tool_use"),
                )
            )
        elif item_type == "tool_result":
            events.append(
                HarnessEvent(
                    type="tool_result",
                    message=_extract_text(item.get("content")) or _tool_message(item),
                    payload=_phase_payload({"tool_name": str(item.get("tool_use_id") or "tool"), "item_type": item_type}, role="item", kind="tool_result"),
                )
            )
    if text_parts:
        title = "\n".join(text_parts)
        events.insert(0, HarnessEvent(type="progress", message=title, payload=_phase_payload(payload, role="phase", kind="assistant", title=title)))
    return events


def _usage_event(usage: dict[str, Any], payload: dict[str, Any]) -> HarnessEvent:
    direct_input_tokens = int(usage.get("input_tokens", 0) or 0)
    cache_read_tokens = int(
        usage.get("cache_read_input_tokens", 0)
        or usage.get("cache_read_tokens", 0)
        or usage.get("cached_input_tokens", 0)
        or 0
    )
    cache_write_tokens = int(usage.get("cache_creation_input_tokens", 0) or usage.get("cache_write_tokens", 0) or 0)
    return HarnessEvent(
        type="usage",
        usage=TokenUsage(
            input_tokens=direct_input_tokens + cache_read_tokens + cache_write_tokens,
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=usage.get("cost_usd") or payload.get("total_cost_usd"),
        ),
        payload=_phase_payload(payload, role="item", kind="usage"),
    )


def _phase_payload(payload: dict[str, Any], *, role: str, kind: str, title: str | None = None) -> dict[str, Any]:
    event_payload = dict(payload)
    phase = {"role": role, "kind": kind}
    if title:
        phase["title"] = title
    event_payload["_innie_phase"] = phase
    return event_payload


def _tool_message(item: dict[str, Any]) -> str | None:
    tool_input = item.get("input")
    if isinstance(tool_input, dict):
        for key in ("command", "query", "path", "url"):
            if tool_input.get(key):
                return str(tool_input[key])
    for key in ("name", "tool_use_id", "message"):
        if item.get(key):
            return str(item[key])
    return _extract_text(item)


def _describe_ignored_event(task_id: str, payload: dict[str, Any]) -> str:
    event_type = str(payload.get("type") or "unknown")
    keys = ", ".join(sorted(str(key) for key in payload.keys() if key not in {"chain_of_thought", "reasoning"}))
    return f"claude task={task_id} event ignored: type={event_type} keys={keys} payload={_safe_json_preview(_redact_ignored_payload(payload))}"


def _should_log_ignored_event(payload: dict[str, Any]) -> bool:
    event_type = str(payload.get("type") or "")
    if event_type == "system" and (payload.get("hook_event") or str(payload.get("subtype") or "").startswith("hook_")):
        return False
    if event_type == "system" and (payload.get("subagent_type") or payload.get("task_id")):
        return False
    if event_type in {"assistant", "user"} and not _has_visible_message_content(payload.get("message")):
        return False
    return True


def _has_visible_message_content(message: Any) -> bool:
    if not isinstance(message, dict):
        return bool(_extract_text(message))
    content = message.get("content")
    if not isinstance(content, list):
        return bool(_extract_text(message))
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") in {"text", "tool_use", "tool_result"}:
            return True
    return False


def _redact_ignored_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {
                "additionalContext",
                "chain_of_thought",
                "output",
                "prompt",
                "reasoning",
                "signature",
                "stderr",
                "stdout",
                "thinking",
            }:
                redacted[key_text] = "<redacted>"
            elif key_text == "content" and isinstance(item, list):
                redacted[key_text] = [_redact_ignored_payload(entry) for entry in item]
            else:
                redacted[key_text] = _redact_ignored_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_ignored_payload(item) for item in value]
    return value
