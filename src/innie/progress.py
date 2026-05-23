from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .harness import HarnessEvent


SHOW_PROGRESS_ACTION_ID = "innie_show_progress_details"
HIDE_PROGRESS_ACTION_ID = "innie_hide_progress_details"
SLACK_TEXT_LIMIT = 12000
SLACK_SECTION_TEXT_LIMIT = 2900
SLACK_FINAL_TEXT_LIMIT = SLACK_SECTION_TEXT_LIMIT


@dataclass(frozen=True)
class SlackRenderedMessage:
    text: str
    blocks: list[dict[str, Any]] | None = None


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
        widget = self.render_widget(task_id, event)
        return None if widget is None else widget.text

    def render_widget(self, task_id: str, event: HarnessEvent) -> SlackRenderedMessage | None:
        if event.type == "started":
            return None
        if event.type == "progress" and event.message:
            text = self._formatter.format(event.message)
            return SlackRenderedMessage(text=text, blocks=_progress_blocks("Innie is working", text, include_summary=True))
        if event.type == "tool_use" and event.message:
            text = self._format_tool_use(event)
            title = f"Innie is {_tool_verb(str(event.payload.get('tool_name') or event.payload.get('tool') or 'tool'))}"
            detail = self._formatter.format(event.message)
            return SlackRenderedMessage(text=text, blocks=_progress_blocks(title, detail))
        if event.type == "tool_result" and event.message:
            text = self._formatter.format(f"Tool result:\n{event.message}")
            detail = self._formatter.format(event.message)
            return SlackRenderedMessage(text=text, blocks=_progress_blocks("Innie received tool output", detail))
        if event.type == "output" and event.message:
            return SlackRenderedMessage(text=self._formatter.format(event.message))
        if event.type == "usage" and event.usage:
            cache_pct = int(event.usage.cache_hit_rate * 100)
            text = self._formatter.format(
                f"Usage: {event.usage.input_tokens} input, "
                f"{event.usage.output_tokens} output, {cache_pct}% cache hit."
            )
            return SlackRenderedMessage(text=text, blocks=_progress_blocks("Innie is wrapping up", text))
        if event.type == "completed":
            return None
        if event.type == "failed":
            return SlackRenderedMessage(text=f"Task {task_id} failed: {event.message or 'no error message'}")
        if event.type == "canceled":
            return SlackRenderedMessage(text=f"Task {task_id} canceled.")
        return None

    def with_progress_summary(self, rendered: SlackRenderedMessage, progress_summary: str | None) -> SlackRenderedMessage:
        if not progress_summary or not rendered.blocks:
            return rendered
        if any(block.get("block_id") == "innie-progress-summary" for block in rendered.blocks):
            return rendered
        return SlackRenderedMessage(
            text=rendered.text,
            blocks=[
                _progress_summary_block(progress_summary),
                *rendered.blocks,
            ],
        )

    def render_final_widget(
        self,
        task_id: str,
        event: HarnessEvent,
        progress_details: list[str],
    ) -> SlackRenderedMessage | None:
        messages = self.render_final_messages(task_id, event, progress_details)
        return None if not messages else messages[0]

    def render_final_messages(
        self,
        task_id: str,
        event: HarnessEvent,
        progress_details: list[str],
    ) -> list[SlackRenderedMessage]:
        rendered = self.render_widget(task_id, event)
        if rendered is None or event.type not in {"output", "failed", "canceled"}:
            return [] if rendered is None else [rendered]
        parts = _split_text_at_newlines(rendered.text, SLACK_FINAL_TEXT_LIMIT)
        messages = []
        for index, part in enumerate(parts):
            if index == 0 and progress_details:
                blocks = _final_blocks(task_id=task_id, final_text=part, progress_details=progress_details, expanded=False)
            else:
                blocks = _final_output_blocks(part)
            messages.append(SlackRenderedMessage(text=part, blocks=blocks))
        return messages

    def render_expanded_final_widget(
        self,
        task_id: str,
        final_text: str,
        progress_details: list[str],
    ) -> SlackRenderedMessage:
        return self._render_final_text_widget(task_id, final_text, progress_details, expanded=True)

    def render_collapsed_final_widget(
        self,
        task_id: str,
        final_text: str,
        progress_details: list[str],
    ) -> SlackRenderedMessage:
        return self._render_final_text_widget(task_id, final_text, progress_details, expanded=False)

    def _render_final_text_widget(
        self,
        task_id: str,
        final_text: str,
        progress_details: list[str],
        *,
        expanded: bool,
    ) -> SlackRenderedMessage:
        text = self._formatter.format(final_text)
        return SlackRenderedMessage(
            text=text,
            blocks=_final_blocks(
                task_id=task_id,
                final_text=text,
                progress_details=progress_details,
                expanded=expanded,
            ),
        )

    def detail_line(self, event: HarnessEvent) -> str | None:
        if event.type == "progress" and event.message:
            return self._formatter.format(event.message)
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


def _progress_blocks(title: str, detail: str, *, include_summary: bool = False) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if include_summary:
        blocks.append(_progress_summary_block(detail))
    blocks.append(
        {
            "type": "plan",
            "block_id": "innie-progress-plan",
            "title": title,
            "tasks": [
                {
                    "task_id": "latest",
                    "title": _plain_text_title(detail),
                    "status": "in_progress",
                }
            ],
        },
    )
    return blocks


def _progress_summary_block(text: str) -> dict[str, Any]:
    return {
        "type": "section",
        "block_id": "innie-progress-summary",
        "text": {"type": "mrkdwn", "text": text},
    }


def _final_blocks(task_id: str, final_text: str, progress_details: list[str], *, expanded: bool) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "context",
            "block_id": "innie-progress-details-context",
            "elements": [{"type": "mrkdwn", "text": "Progress details"}],
        },
    ]
    if expanded:
        blocks.append(
            {
                "type": "section",
                "block_id": "innie-progress-details",
                "expand": False,
                "text": {
                    "type": "mrkdwn",
                    "text": _progress_details_text(progress_details),
                },
            }
        )
        blocks.append(
            {
                "type": "actions",
                "block_id": "innie-progress-details-actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": HIDE_PROGRESS_ACTION_ID,
                        "text": {"type": "plain_text", "text": "show less"},
                        "value": task_id,
                    }
                ],
            }
        )
    else:
        blocks.append(
            {
                "type": "actions",
                "block_id": "innie-progress-details-actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": SHOW_PROGRESS_ACTION_ID,
                        "text": {"type": "plain_text", "text": "show more"},
                        "value": task_id,
                    }
                ],
            }
        )
    blocks.append({"type": "divider", "block_id": "innie-final-divider"})
    blocks.extend(_final_output_blocks(final_text))
    return blocks


def _final_output_blocks(final_text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, chunk in enumerate(_chunk_text(final_text)):
        block_id = "innie-final-output" if index == 0 else f"innie-final-output-{index + 1}"
        blocks.append(
            {
                "type": "section",
                "block_id": block_id,
                "expand": True,
                "text": {"type": "mrkdwn", "text": chunk},
            }
        )
    return blocks


def _progress_details_text(progress_details: list[str], *, limit: int = SLACK_SECTION_TEXT_LIMIT) -> str:
    text = "\n".join(progress_details)
    if not text:
        return "No progress details recorded."
    if len(text) <= limit:
        return text
    omitted = "\n... earlier progress omitted ...\n"
    tail_limit = limit - len(omitted)
    return omitted + text[-tail_limit:]


def _chunk_text(text: str, *, limit: int = SLACK_SECTION_TEXT_LIMIT) -> list[str]:
    if not text:
        return [""]
    chunks = []
    remaining = text
    while remaining:
        chunks.append(remaining[:limit])
        remaining = remaining[limit:]
    return chunks


def _split_text_at_newlines(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    parts = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
            part = remaining[:split_at]
            remaining = remaining[split_at:]
        else:
            part = remaining[:split_at]
            remaining = remaining[split_at + 1 :]
        parts.append(part)
    parts.append(remaining)
    return parts


def _plain_text_title(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    compact = re.sub(r"[*_`~<>]", "", compact)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
