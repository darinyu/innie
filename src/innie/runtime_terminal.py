from __future__ import annotations

from .harness import HarnessEvent


def format_terminal_event(session_id: str, task_id: str, event: HarnessEvent) -> str:
    label = event.type
    if event.type in {"tool_use", "tool_result"}:
        tool_name = str(event.payload.get("tool_name") or event.payload.get("tool") or "tool")
        label = f"{event.type} {tool_name}"
    message = _preview_terminal(event.message or "")
    suffix = f": {message}" if message else ""
    return f"session {session_id} task {task_id} {label}{suffix}"


def _preview_terminal(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
