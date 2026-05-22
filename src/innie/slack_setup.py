from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import http.server
import json
import socket
import threading
import time
from typing import Any, Callable
from urllib import parse, request

from .config import load_secrets, write_secrets, write_workspace_config


PromptFn = Callable[[str], str]
SecretPromptFn = Callable[[str], str]


BOT_SCOPES = [
    "app_mentions:read",
    "channels:history",
    "chat:write",
    "groups:history",
    "im:history",
    "im:read",
    "reactions:write",
]
BOT_EVENTS = ["app_mention", "message.im"]


@dataclass(frozen=True)
class SlackSetupResult:
    ok: bool
    messages: list[str]


class SlackApiError(RuntimeError):
    pass


class SlackApi:
    def post_json(self, method: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(f"https://slack.com/api/{method}", data=data)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if not result.get("ok"):
            raise SlackApiError(f"{method} failed: {result.get('error', 'unknown_error')}")
        return result

    def post_form(self, method: str, payload: dict[str, str]) -> dict[str, Any]:
        data = parse.urlencode(payload).encode("utf-8")
        req = request.Request(f"https://slack.com/api/{method}", data=data)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if not result.get("ok"):
            raise SlackApiError(f"{method} failed: {result.get('error', 'unknown_error')}")
        return result


class SlackWebClient:
    def __init__(self, api: SlackApi, token: str) -> None:
        self._api = api
        self._token = token

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self._api.post_json(
            "reactions.add",
            self._token,
            {"channel": channel, "timestamp": timestamp, "name": name},
        )


def build_manifest(
    *,
    app_name: str,
    display_name: str,
    redirect_urls: list[str],
    include_channel_messages: bool,
) -> dict[str, Any]:
    events = list(BOT_EVENTS)
    if include_channel_messages:
        events.extend(["message.channels", "message.groups"])

    return {
        "display_information": {
            "name": app_name,
            "description": "Slack-first Innie sidekick for durable AI work sessions.",
        },
        "features": {
            "bot_user": {
                "display_name": display_name,
                "always_online": True,
            },
            "app_home": {
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": False,
            },
        },
        "oauth_config": {
            "redirect_urls": redirect_urls,
            "scopes": {"bot": BOT_SCOPES},
        },
        "settings": {
            "event_subscriptions": {
                "bot_events": events,
            },
            "interactivity": {"is_enabled": False},
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }


def run_slack_setup(
    workspace: Path,
    *,
    api: SlackApi | None = None,
    prompt: PromptFn = input,
    prompt_secret: SecretPromptFn | None = None,
    open_url: Callable[[str], None] | None = None,
) -> SlackSetupResult:
    api = api or SlackApi()
    prompt_secret = prompt_secret or prompt
    open_url = open_url or (lambda url: None)
    workspace = workspace.resolve()
    messages: list[str] = []
    existing = load_secrets(workspace)
    if existing.get("slack_bot_token"):
        messages.append("Existing Slack bot token found.")
        try:
            auth = api.post_json("auth.test", existing["slack_bot_token"], {})
            messages.append(f"Existing bot token is valid for {auth.get('user_id', 'unknown bot')}.")
            answer = prompt("Refresh or replace existing Slack config? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                return SlackSetupResult(ok=True, messages=messages + ["Kept existing Slack config."])
        except Exception as exc:
            messages.append(f"Existing Slack token validation failed: {exc}")

    app_name = prompt("Slack app name [innie]: ").strip() or "innie"
    display_name = prompt("Bot display name [Innie]: ").strip() or "Innie"
    include_channel_messages = _confirm(prompt, "Listen to all channel/group messages, not just mentions? [y/N] ")
    callback_mode = prompt("OAuth callback mode: local, public, or manual [manual]: ").strip().lower() or "manual"
    redirect_url = _resolve_redirect_url(callback_mode, prompt, messages)
    manifest = build_manifest(
        app_name=app_name,
        display_name=display_name,
        redirect_urls=[redirect_url],
        include_channel_messages=include_channel_messages,
    )

    auto_create = _confirm(prompt, "Create/update the Slack app manifest automatically? [y/N] ")
    client_id = prompt("Slack client id: ").strip()
    client_secret = prompt_secret("Slack client secret: ").strip()
    app_id = prompt("Slack app id, if already known: ").strip()

    if auto_create:
        config_token = prompt_secret("One-time Slack App Configuration token: ").strip()
        result = api.post_json("apps.manifest.create", config_token, {"manifest": manifest})
        app_id = result.get("app_id") or app_id
        credentials = result.get("credentials", {})
        client_id = credentials.get("client_id") or client_id
        client_secret = credentials.get("client_secret") or client_secret
        messages.append("Created Slack app from generated manifest. The configuration token was not stored.")
    else:
        manifest_path = workspace / ".innie" / "slack-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        messages.append(f"Wrote Slack manifest for manual paste: {manifest_path}")

    oauth_url = _oauth_url(client_id, BOT_SCOPES, redirect_url)
    messages.append(f"Open Slack OAuth URL: {oauth_url}")
    open_url(oauth_url)
    code = _collect_oauth_code(callback_mode, redirect_url, prompt, messages)
    oauth_result = api.post_form(
        "oauth.v2.access",
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_url,
        },
    )
    bot_token = oauth_result.get("access_token", "")
    if not bot_token.startswith("xoxb-"):
        return SlackSetupResult(ok=False, messages=messages + ["OAuth did not return an xoxb- bot token."])

    app_id = oauth_result.get("app_id") or app_id
    auth = api.post_json("auth.test", bot_token, {})
    bot_user_id = auth.get("user_id", "")

    xapp_token = prompt_secret("App-level Socket Mode token with connections:write (xapp-...): ").strip()
    if not xapp_token.startswith("xapp-"):
        return SlackSetupResult(ok=False, messages=messages + ["App-level token must start with xapp-."])
    api.post_json("apps.connections.open", xapp_token, {})
    messages.append("Validated bot token and app-level Socket Mode token.")

    write_secrets(
        workspace,
        {
            "slack_bot_token": bot_token,
            "slack_app_token": xapp_token,
            "slack_client_id": client_id,
            "slack_client_secret": client_secret,
        },
    )
    write_workspace_config(workspace, app_id=app_id, bot_user_id=bot_user_id, app_name=app_name)
    messages.append("Saved Slack tokens with restrictive file permissions.")
    messages.append("Slack setup complete: bot can authenticate and Socket Mode can open.")
    return SlackSetupResult(ok=True, messages=messages)


def _confirm(prompt: PromptFn, text: str) -> bool:
    return prompt(text).strip().lower() in {"y", "yes"}


def _resolve_redirect_url(callback_mode: str, prompt: PromptFn, messages: list[str]) -> str:
    if callback_mode == "public":
        return prompt("Public OAuth callback URL: ").strip()
    if callback_mode == "local":
        port_text = prompt("Local OAuth callback port [8765]: ").strip() or "8765"
        port = int(port_text)
        if _port_in_use(port):
            answer = prompt(f"Port {port} is busy. Continue without killing it? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                raise SlackApiError(f"Port {port} is busy.")
        return f"http://localhost:{port}/callback"
    messages.append(
        "Manual OAuth mode: Slack may redirect to localhost and fail to load in remote-dev environments. "
        "Copy the final callback URL or the code query parameter back into this wizard."
    )
    return "http://localhost:8765/callback"


def _collect_oauth_code(callback_mode: str, redirect_url: str, prompt: PromptFn, messages: list[str]) -> str:
    if callback_mode != "local":
        value = prompt("Paste final callback URL or OAuth code: ").strip()
        return _extract_code(value)

    result: dict[str, str] = {}
    parsed = parse.urlparse(redirect_url)
    port = parsed.port or 8765

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            params = parse.parse_qs(parse.urlparse(self.path).query)
            code = params.get("code", [""])[0]
            result["code"] = code
            self.send_response(200 if code else 400)
            self.end_headers()
            self.wfile.write(b"OAuth code received. You can close this tab." if code else b"Missing code.")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = http.server.HTTPServer(("localhost", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    messages.append(f"Waiting up to 120 seconds for OAuth callback on localhost:{port}.")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and "code" not in result:
        time.sleep(0.1)
    if "code" not in result:
        server.shutdown()
        value = prompt("Local callback timed out. Paste final callback URL or OAuth code: ").strip()
        return _extract_code(value)
    return result["code"]


def _extract_code(value: str) -> str:
    parsed = parse.urlparse(value)
    if parsed.query:
        params = parse.parse_qs(parsed.query)
        if params.get("code"):
            return params["code"][0]
    return value


def _oauth_url(client_id: str, scopes: list[str], redirect_url: str) -> str:
    return "https://slack.com/oauth/v2/authorize?" + parse.urlencode(
        {
            "client_id": client_id,
            "scope": ",".join(scopes),
            "redirect_uri": redirect_url,
        }
    )


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("localhost", port)) == 0
