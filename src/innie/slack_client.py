from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib import error, parse, request

from .slack_files import SlackFileDownloadResult


class SlackApiError(RuntimeError):
    pass


DOWNLOAD_TIMEOUT_SECONDS = 60
API_TIMEOUT_SECONDS = 30
SLACK_LOGIN_SAMPLE_BYTES = 128 * 1024


@dataclass(frozen=True)
class SlackPostResult:
    channel: str
    ts: str | None


class SlackWebClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._workspace_url: str | None = None

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self._post_json("reactions.add", {"channel": channel, "timestamp": timestamp, "name": name})

    def post_message(
        self,
        *,
        channel: str,
        thread_ts: str | None,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> str | None:
        return self.post_message_result(
            channel=channel,
            thread_ts=thread_ts,
            text=text,
            blocks=blocks,
            unfurl_links=unfurl_links,
            unfurl_media=unfurl_media,
        ).ts

    def post_message_result(
        self,
        *,
        channel: str,
        thread_ts: str | None,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> SlackPostResult:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts is not None:
            payload["thread_ts"] = thread_ts
        if blocks is not None:
            payload["blocks"] = blocks
        if unfurl_links is not None:
            payload["unfurl_links"] = unfurl_links
        if unfurl_media is not None:
            payload["unfurl_media"] = unfurl_media
        result = self._post_json("chat.postMessage", payload)
        return SlackPostResult(channel=str(result.get("channel") or channel), ts=result.get("ts"))

    def post_direct_message(
        self,
        *,
        user: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> SlackPostResult:
        return self.post_message_result(
            channel=user,
            thread_ts=None,
            text=text,
            blocks=blocks,
            unfurl_links=unfurl_links,
            unfurl_media=unfurl_media,
        )

    def post_ephemeral(
        self,
        *,
        channel: str,
        user: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {"channel": channel, "user": user, "text": text}
        if thread_ts is not None:
            payload["thread_ts"] = thread_ts
        if blocks is not None:
            payload["blocks"] = blocks
        result = self._post_json("chat.postEphemeral", payload)
        return result.get("message_ts")

    def open_dm(self, *, user: str) -> str:
        result = self._post_json("conversations.open", {"users": user})
        channel = result.get("channel")
        if not isinstance(channel, dict) or not channel.get("id"):
            raise SlackApiError("conversations.open failed: missing_channel")
        return str(channel["id"])

    def get_permalink(self, *, channel: str, message_ts: str) -> str | None:
        result = self._post_json("chat.getPermalink", {"channel": channel, "message_ts": message_ts})
        permalink = result.get("permalink")
        return str(permalink) if permalink else None

    def workspace_url(self) -> str | None:
        if self._workspace_url is not None:
            return self._workspace_url
        result = self._post_json("auth.test", {})
        url = result.get("url")
        self._workspace_url = str(url) if url else ""
        return self._workspace_url or None

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
            with request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
                data = resp.read()
        except (TimeoutError, OSError, error.URLError) as exc:
            return SlackFileDownloadResult(error=str(exc) or exc.__class__.__name__)
        if _looks_like_slack_login_redirect(data):
            return SlackFileDownloadResult(error=self._diagnose_file_download_redirect(url))
        destination.write_bytes(data)
        return SlackFileDownloadResult(byte_count=len(data))

    def _post_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._post_json_result(method, payload)
        if not result.get("ok"):
            raise SlackApiError(_slack_error_message(method, result))
        return result

    def _post_json_result(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.api_call(method, payload)

    def api_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(f"https://slack.com/api/{method}", data=data)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with request.urlopen(req, timeout=API_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def api_form_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = parse.urlencode({key: _form_value(value) for key, value in payload.items()}).encode("utf-8")
        req = request.Request(f"https://slack.com/api/{method}", data=data)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
        with request.urlopen(req, timeout=API_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _diagnose_file_download_redirect(self, url: str) -> str:
        file_id = _file_id_from_slack_file_url(url)
        if file_id is None:
            return "slack_login_redirect"
        try:
            result = self._post_json_result("files.info", {"file": file_id})
        except (TimeoutError, OSError, error.URLError, json.JSONDecodeError, UnicodeDecodeError):
            return "slack_login_redirect"
        if result.get("ok"):
            return "slack_login_redirect"
        error = str(result.get("error") or "unknown_error")
        if error == "missing_scope":
            needed = str(result.get("needed") or "files:read")
            return f"missing_scope: {needed}"
        return f"files.info failed: {error}"


def _looks_like_slack_login_redirect(data: bytes) -> bool:
    sample = data[:SLACK_LOGIN_SAMPLE_BYTES].lower()
    has_files_redirect = b"/files-pri/" in sample or b"\\/files-pri\\/" in sample or b"%2ffiles-pri" in sample
    return (
        b"<title>slack</title>" in sample
        and b"redirecturl" in sample
        and has_files_redirect
        and b"loggedinteams" in sample
    )


def _file_id_from_slack_file_url(url: str) -> str | None:
    match = re.search(r"/files-pri/[^/]*-([A-Z0-9]+)(?:/|$)", url)
    return match.group(1) if match else None


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _slack_error_message(method: str, result: dict[str, Any]) -> str:
    message = f"{method} failed: {result.get('error', 'unknown_error')}"
    details = []
    for key in ("needed", "provided"):
        value = result.get(key)
        if value:
            details.append(f"{key}={value}")
    if details:
        message = f"{message} ({', '.join(details)})"
    return message
