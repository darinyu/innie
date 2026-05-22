from __future__ import annotations

import re

from .harness import HarnessEvent


class SlackMessageFormatter:
    def format(self, text: str) -> str:
        text = self._format_headings(text)
        text = re.sub(r"\*\*([^*\n]+)\*\*", r"*\1*", text)
        return text.strip()

    def _format_headings(self, text: str) -> str:
        lines = []
        for line in text.splitlines():
            match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if match:
                lines.append(f"*{match.group(2).strip()}*")
            else:
                lines.append(line)
        return "\n".join(lines)


class SlackProgressRenderer:
    def __init__(self, *, formatter: SlackMessageFormatter | None = None) -> None:
        self._formatter = formatter or SlackMessageFormatter()

    def render(self, task_id: str, event: HarnessEvent) -> str | None:
        if event.type == "started":
            return None
        if event.type == "progress" and event.message:
            return self._formatter.format(f"Progress: {event.message}")
        if event.type == "tool_use" and event.message:
            return self._format_tool_use(event)
        if event.type == "tool_result" and event.message:
            return self._formatter.format(f"Tool result:\n{event.message}")
        if event.type == "output" and event.message:
            return self._formatter.format(event.message)
        if event.type == "usage" and event.usage:
            cache_pct = int(event.usage.cache_hit_rate * 100)
            return self._formatter.format(
                f"Usage: {event.usage.input_tokens} input, "
                f"{event.usage.output_tokens} output, {cache_pct}% cache hit."
            )
        if event.type == "completed":
            return None
        if event.type == "failed":
            return f"Task {task_id} failed: {event.message or 'no error message'}"
        if event.type == "canceled":
            return f"Task {task_id} canceled."
        return None

    def _format_tool_use(self, event: HarnessEvent) -> str:
        tool_name = str(event.payload.get("tool_name") or event.payload.get("tool") or "tool")
        verb = _tool_verb(tool_name)
        detail = self._formatter.format(event.message or tool_name)
        return f"*Innie is {verb}*\n> {detail}"


def _tool_verb(tool_name: str) -> str:
    normalized = tool_name.replace("-", "_").lower()
    if "web" in normalized and "search" in normalized:
        return "searching the web"
    if "read" in normalized or "open" in normalized:
        return "reading context"
    if "exec" in normalized or "shell" in normalized or "command" in normalized:
        return "running a command"
    if "apply_patch" in normalized or "edit" in normalized:
        return "editing files"
    return f"using {tool_name}"
