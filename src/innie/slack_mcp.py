from __future__ import annotations

import json
import os
import sys
from typing import Any

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
            if name == "slack_get_channel_history":
                return _text_result(self._get_channel_history(arguments))
            if name == "slack_find_messages":
                return _text_result(self._find_messages(arguments))
            return _text_result(f"Unknown tool: {name}", is_error=True)
        except Exception as exc:
            return _text_result(str(exc) or exc.__class__.__name__, is_error=True)

    def _get_thread(self, arguments: dict[str, Any]) -> str:
        channel = _required(arguments, "channel")
        thread_ts = _required(arguments, "thread_ts")
        limit = _limit(arguments.get("limit"), default=20)
        result = self._client.api_call("conversations.replies", {"channel": channel, "ts": thread_ts, "limit": limit})
        return _format_messages(f"Slack thread {channel} {thread_ts}", result.get("messages"))

    def _get_channel_history(self, arguments: dict[str, Any]) -> str:
        channel = _required(arguments, "channel")
        limit = _limit(arguments.get("limit"), default=20)
        payload: dict[str, Any] = {"channel": channel, "limit": limit}
        latest = arguments.get("latest")
        if latest:
            payload["latest"] = str(latest)
        result = self._client.api_call("conversations.history", payload)
        return _format_messages(f"Recent Slack messages in {channel}", result.get("messages"))

    def _find_messages(self, arguments: dict[str, Any]) -> str:
        channel = _required(arguments, "channel")
        query = _required(arguments, "query").lower()
        limit = _limit(arguments.get("limit"), default=50)
        result = self._client.api_call("conversations.history", {"channel": channel, "limit": limit})
        messages = [message for message in _messages(result.get("messages")) if query in str(message.get("text") or "").lower()]
        return _format_messages(f"Slack messages in {channel} matching {query!r}", messages)


def main() -> int:
    token = os.environ.get(SLACK_BOT_TOKEN_ENV)
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


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "slack_get_thread",
            "description": "Fetch replies from a Slack thread by channel and root thread timestamp.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "thread_ts": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["channel", "thread_ts"],
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
        {
            "name": "slack_find_messages",
            "description": "Find matching text in recent Slack channel history.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["channel", "query"],
            },
        },
    ]


def _format_messages(title: str, raw_messages: Any) -> str:
    lines = [title]
    for message in _messages(raw_messages):
        user = message.get("user") or message.get("username") or "unknown"
        ts = message.get("ts") or "unknown_ts"
        text = " ".join(str(message.get("text") or "").split())
        lines.append(f"- {user} at {ts}: {text}")
    if len(lines) == 1:
        lines.append("- no messages found")
    return "\n".join(lines)


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _required(arguments: dict[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


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
