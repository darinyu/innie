from __future__ import annotations

import json
from typing import Any
from urllib import request


class SlackApiError(RuntimeError):
    pass


class SlackWebClient:
    def __init__(self, token: str) -> None:
        self._token = token

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self._post_json("reactions.add", {"channel": channel, "timestamp": timestamp, "name": name})

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        self._post_json("chat.postMessage", {"channel": channel, "thread_ts": thread_ts, "text": text})

    def _post_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(f"https://slack.com/api/{method}", data=data)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if not result.get("ok"):
            raise SlackApiError(f"{method} failed: {result.get('error', 'unknown_error')}")
        return result
