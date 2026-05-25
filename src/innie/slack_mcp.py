from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib import parse

from .config import load_secrets
from .slack_client import SlackWebClient
from .slack_mcp_config import SLACK_BOT_TOKEN_ENV


class SlackMcpServer:
    def __init__(self, client) -> None:
        self._client = client

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if request_id is None:
            return None
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "innie-slack", "version": "0.1.0"},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": _tools()})
        if method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            return _result(request_id, self._call_tool(name, arguments))
        return _error(request_id, -32601, f"Unknown method: {method}")

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "slack_get_thread":
                return _text_result(self._get_thread(arguments))
            if name == "slack_get_message":
                return _text_result(self._get_message(arguments))
            if name == "slack_get_permalink":
                return _text_result(self._get_permalink(arguments))
            if name == "slack_get_channel_history":
                return _text_result(self._get_channel_history(arguments))
            return _text_result(f"Unknown tool: {name}", is_error=True)
        except Exception as exc:
            return _text_result(str(exc) or exc.__class__.__name__, is_error=True)

    def _get_thread(self, arguments: dict[str, Any]) -> str:
        channel = _required(arguments, "channel")
        thread_ts = _required(arguments, "thread_ts")
        current_ts = _optional(arguments, "current_ts")
        limit = _limit(arguments.get("limit"), default=100)
        messages = _fetch_thread_messages(
            self._client,
            channel=channel,
            thread_ts=thread_ts,
            limit=limit,
            current_ts=current_ts,
        )
        return _format_messages(f"Slack thread {channel} {thread_ts}", messages, current_message_ts=current_ts)

    def _get_message(self, arguments: dict[str, Any]) -> str:
        channel = _required(arguments, "channel")
        ts = _required(arguments, "ts")
        thread_ts = _optional(arguments, "thread_ts")
        if thread_ts:
            messages = _fetch_thread_messages(self._client, channel=channel, thread_ts=thread_ts, limit=100, current_ts=ts)
            messages = [message for message in messages if str(message.get("ts") or "") == ts]
        else:
            result = _api_call(
                self._client,
                "conversations.history",
                {"channel": channel, "latest": ts, "inclusive": True, "limit": 1},
            )
            messages = [message for message in _messages(result.get("messages")) if str(message.get("ts") or "") == ts]
        return _format_messages(f"Slack message {channel} {ts}", messages, current_message_ts=ts)

    def _get_permalink(self, arguments: dict[str, Any]) -> str:
        link = _parse_permalink(_required(arguments, "url"))
        messages = _fetch_thread_messages(
            self._client,
            channel=link["channel"],
            thread_ts=link["thread_ts"],
            limit=_limit(arguments.get("limit"), default=100),
            current_ts=_permalink_current_ts(link),
        )
        lines = [
            "Slack permalink",
            f"- channel: {link['channel']}",
            f"- message_ts: {link['message_ts']}",
            f"- thread_ts: {link['thread_ts']}",
            f"- suggested_next_call: {_suggested_thread_call(link)}",
            _format_messages("Referenced Slack thread", messages, current_message_ts=link["message_ts"]),
        ]
        return "\n".join(lines)

    def _get_channel_history(self, arguments: dict[str, Any]) -> str:
        channel = _required(arguments, "channel")
        limit = _limit(arguments.get("limit"), default=20)
        payload: dict[str, Any] = {"channel": channel, "limit": limit}
        latest = arguments.get("latest")
        if latest:
            payload["latest"] = str(latest)
        result = _api_call(self._client, "conversations.history", payload)
        return _format_messages(f"Recent Slack messages in {channel}", result.get("messages"))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    token = os.environ.get(SLACK_BOT_TOKEN_ENV) or _workspace_token(args.workspace)
    if not token:
        print(f"{SLACK_BOT_TOKEN_ENV} is required", file=sys.stderr)
        return 1
    server = SlackMcpServer(SlackWebClient(token))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = server.handle(request)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, str(exc))
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


def _parse_args(argv: list[str] | None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", help="Innie workspace containing .innie/secrets.json")
    return parser.parse_args(argv)


def _workspace_token(workspace: str | None) -> str | None:
    if not workspace:
        return None
    token = load_secrets(Path(workspace)).get("slack_bot_token")
    return str(token).strip() if token else None


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "slack_get_thread",
            "description": "Fetch replies from a Slack thread by channel and root thread timestamp. Pass current_ts to mark the triggering or referenced message.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "thread_ts": {"type": "string"},
                    "current_ts": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["channel", "thread_ts"],
            },
        },
        {
            "name": "slack_get_message",
            "description": "Fetch one Slack message by channel and message timestamp. Pass thread_ts when the message is a thread reply.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "ts": {"type": "string"},
                    "thread_ts": {"type": "string"},
                },
                "required": ["channel", "ts"],
            },
        },
        {
            "name": "slack_get_permalink",
            "description": "Parse a Slack permalink, fetch the referenced message/thread, and return reusable channel/message/thread coordinates for further traversal.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["url"],
            },
        },
        {
            "name": "slack_get_channel_history",
            "description": "Fetch recent messages from a Slack channel.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "latest": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["channel"],
            },
        },
    ]


def _api_call(client, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if method == "conversations.replies":
        api_form_call = getattr(client, "api_form_call", None)
        if callable(api_form_call):
            result = api_form_call(method, payload)
        else:
            result = client.api_call(method, payload)
    else:
        result = client.api_call(method, payload)
    if not result.get("ok", True):
        raise RuntimeError(_api_error_message(method, result))
    return result


def _api_error_message(method: str, result: dict[str, Any]) -> str:
    parts = [f"{method} failed: {result.get('error') or 'unknown_error'}"]
    needed = result.get("needed")
    if needed:
        parts.append(f"needed={needed}")
    provided = result.get("provided")
    if provided:
        parts.append(f"provided={provided}")
    metadata = result.get("response_metadata")
    if isinstance(metadata, dict):
        messages = metadata.get("messages")
        if isinstance(messages, list) and messages:
            parts.append("; ".join(str(message) for message in messages))
    return " ".join(parts)


def _fetch_thread_messages(client, *, channel: str, thread_ts: str, limit: int, current_ts: str | None = None) -> list[dict[str, Any]]:
    if not current_ts:
        result = _api_call(client, "conversations.replies", {"channel": channel, "ts": thread_ts, "limit": limit})
        return _messages(result.get("messages"))

    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        payload: dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": 100}
        if cursor:
            payload["cursor"] = cursor
        result = _api_call(client, "conversations.replies", payload)
        page = _messages(result.get("messages"))
        messages.extend(page)
        for index, message in enumerate(messages):
            if str(message.get("ts") or "") == str(current_ts):
                return messages[: index + 1]
        cursor = _next_cursor(result)
        if not cursor:
            return messages


def _suggested_thread_call(link: dict[str, str]) -> str:
    call = f"slack_get_thread(channel=\"{link['channel']}\", thread_ts=\"{link['thread_ts']}\""
    if link["message_ts"] != link["thread_ts"]:
        call += f", current_ts=\"{link['message_ts']}\""
    return call + ")"


def _permalink_current_ts(link: dict[str, str]) -> str | None:
    if link["message_ts"] == link["thread_ts"]:
        return None
    return link["message_ts"]


def _format_messages(title: str, raw_messages: Any, *, current_message_ts: str | None = None) -> str:
    lines = [title]
    for message in _messages(raw_messages):
        user = message.get("user") or message.get("username") or "unknown"
        ts = message.get("ts") or "unknown_ts"
        text = " ".join(str(message.get("text") or "").split())
        marker = " [current]" if current_message_ts and str(ts) == str(current_message_ts) else ""
        lines.append(f"- {user} at {ts}{marker}: {text}")
    if len(lines) == 1:
        lines.append("- no messages found")
    return "\n".join(lines)


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _parse_permalink(url: str) -> dict[str, str]:
    parsed = parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        archive_index = parts.index("archives")
        channel = parts[archive_index + 1]
        raw_message = parts[archive_index + 2]
    except (ValueError, IndexError) as exc:
        raise ValueError("Slack permalink must include /archives/<channel>/p<message_ts>") from exc
    if not raw_message.startswith("p"):
        raise ValueError("Slack permalink message segment must start with p")
    query = parse.parse_qs(parsed.query)
    message_ts = _ts_from_permalink(raw_message[1:])
    thread_ts = str((query.get("thread_ts") or [message_ts])[0])
    channel = str((query.get("cid") or [channel])[0])
    return {"channel": channel, "message_ts": message_ts, "thread_ts": thread_ts}


def _ts_from_permalink(value: str) -> str:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) <= 6:
        raise ValueError("Slack permalink timestamp is invalid")
    seconds = str(int(digits[:-6]))
    fraction = digits[-6:].rstrip("0") or "0"
    return f"{seconds}.{fraction}"


def _next_cursor(result: dict[str, Any]) -> str | None:
    metadata = result.get("response_metadata")
    if not isinstance(metadata, dict):
        return None
    cursor = str(metadata.get("next_cursor") or "").strip()
    return cursor or None


def _required(arguments: dict[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional(arguments: dict[str, Any], key: str) -> str | None:
    value = str(arguments.get(key) or "").strip()
    return value or None


def _limit(value: Any, *, default: int) -> int:
    try:
        limit = int(value or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, 100))


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
