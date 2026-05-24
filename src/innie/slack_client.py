from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import request

from .slack_files import SlackFileDownloadResult


class SlackApiError(RuntimeError):
    pass


class SlackWebClient:
    def __init__(self, token: str) -> None:
        self._token = token

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self._post_json("reactions.add", {"channel": channel, "timestamp": timestamp, "name": name})

    def post_message(
        self,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {"channel": channel, "thread_ts": thread_ts, "text": text}
        if blocks is not None:
            payload["blocks"] = blocks
        result = self._post_json("chat.postMessage", payload)
        return result.get("ts")

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks is not None:
            payload["blocks"] = blocks
        self._post_json("chat.update", payload)

    def delete_message(self, *, channel: str, ts: str) -> None:
        self._post_json("chat.delete", {"channel": channel, "ts": ts})

    def download_file(self, url: str, destination: Path) -> SlackFileDownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        req = request.Request(url)
        req.add_header("Authorization", f"Bearer {self._token}")
        try:
            with request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:
            return SlackFileDownloadResult(error=str(exc) or exc.__class__.__name__)
        if _looks_like_slack_login_redirect(data):
            return SlackFileDownloadResult(error="slack_login_redirect")
        destination.write_bytes(data)
        return SlackFileDownloadResult(byte_count=len(data))

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


def _looks_like_slack_login_redirect(data: bytes) -> bool:
    sample = data[:131072].lower()
    has_files_redirect = b"/files-pri/" in sample or b"\\/files-pri\\/" in sample or b"%2ffiles-pri" in sample
    return (
        b"<title>slack</title>" in sample
        and b"redirecturl" in sample
        and has_files_redirect
        and b"loggedinteams" in sample
    )
