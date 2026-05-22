from __future__ import annotations

from .harness import HarnessEvent


class SlackProgressRenderer:
    def render(self, task_id: str, event: HarnessEvent) -> str | None:
        if event.type == "started":
            return f"Started task {task_id}."
        if event.type == "progress" and event.message:
            return f"Progress: {event.message}"
        if event.type == "tool_use" and event.message:
            return f"Using tool: {event.message}"
        if event.type == "tool_result" and event.message:
            return f"Tool result: {event.message}"
        if event.type == "output" and event.message:
            return f"Done:\n{event.message}"
        if event.type == "usage" and event.usage:
            cache_pct = int(event.usage.cache_hit_rate * 100)
            return (
                f"Usage: {event.usage.input_tokens} input, "
                f"{event.usage.output_tokens} output, {cache_pct}% cache hit."
            )
        if event.type == "completed":
            return f"Task {task_id} completed."
        if event.type == "failed":
            return f"Task {task_id} failed: {event.message or 'no error message'}"
        if event.type == "canceled":
            return f"Task {task_id} canceled."
        return None
