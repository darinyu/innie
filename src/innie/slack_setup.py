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
OutputFn = Callable[[str], None]


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
    output: OutputFn = print,
    oauth_timeout_seconds: int = 120,
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

    messages.extend(
        [
            "Slack setup takes about 5-8 minutes.",
            "Use docs/slack-setup.md if you want screenshots beside the wizard.",
        ]
    )
    app_name = prompt(
        "Step 1/6 - Name the Slack app (1 minute), modifiable in the future.\n"
        "Slack app name [innie]: "
    ).strip() or "innie"
    display_name = prompt(
        "Bot display name [Innie]: "
    ).strip() or "Innie"
    trigger_mode_choice = prompt(
        "Step 2/6 - Choose when Innie should respond.\n"
        "  Mode 1: respond when someone tags the bot, like @Innie.\n"
        "  Mode 2: respond when someone tags you, like @<username>, in channels where the app is present.\n"
        "Choose Mode 1 or Mode 2 [1]: "
    ).strip() or "1"
    trigger_mode = "user_mention" if trigger_mode_choice == "2" else "bot_mention"
    watched_user_id = None
    if trigger_mode == "user_mention":
        messages.append("Mode 2 selected. Innie will use the installing Slack user ID returned by OAuth.")
    include_channel_messages = trigger_mode == "user_mention"
    redirect_url = _resolve_redirect_url(prompt, messages)
    manifest = build_manifest(
        app_name=app_name,
        display_name=display_name,
        redirect_urls=[redirect_url],
        include_channel_messages=include_channel_messages,
    )

    manifest_path = workspace / ".innie" / "slack-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
    manifest_path.write_text(manifest_json + "\n", encoding="utf-8")
    messages.append(f"Step 3/6 - Wrote Slack manifest for manual paste: {manifest_path}")
    messages.append(
        "In Slack API: Create New App -> From an app manifest -> paste this file. "
        "This is usually 6 clicks and 2-3 minutes."
    )
    output(
        "\n".join(
            [
                "",
                "Step 3/6 - Create the Slack app from the manifest.",
                "Open: https://api.slack.com/apps",
                "Click: Create New App -> From an app manifest -> select your workspace.",
                "Paste this manifest:",
                "",
                manifest_json,
                "",
            ]
        )
    )
    prompt(
        "Step 3/6 - Create the Slack app from the generated manifest.\n"
        f"  Manifest file: {manifest_path}\n"
        "  Open https://api.slack.com/apps\n"
        "  Click Create New App -> From an app manifest -> select your workspace.\n"
        "  Paste the manifest printed above.\n"
        "  This is usually 6 clicks and 2-3 minutes.\n"
        "Press Enter after the Slack app is created from the manifest."
    )
    _clear_terminal(output)

    client_id = prompt(
        "Step 4/6 - In Slack API, open Basic Information -> App Credentials "
        "(about 2 clicks). Copy Client ID: "
    ).strip()
    client_secret = prompt_secret("Step 4/6 - Copy Client Secret: ").strip()
    app_id = prompt("Step 4/6 - Copy App ID (optional but useful): ").strip()

    oauth_url = _oauth_url(client_id, BOT_SCOPES, redirect_url)
    messages.append("Step 5/6 - OAuth install. This opens a local callback server on localhost:8765.")
    messages.append("If the callback page cannot reach this machine, copy the final URL from your browser.")
    messages.append(f"Open Slack OAuth URL: {oauth_url}")
    open_url(oauth_url)
    code = _collect_oauth_code(
        redirect_url,
        oauth_url,
        prompt,
        output,
        messages,
        timeout_seconds=oauth_timeout_seconds,
    )
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
    if trigger_mode == "user_mention":
        watched_user_id = (oauth_result.get("authed_user") or {}).get("id")
        if not watched_user_id:
            return SlackSetupResult(
                ok=False,
                messages=messages
                + [
                    "Mode 2 needs Slack to return the installing user ID from OAuth, but it was missing.",
                    "Run setup again with Mode 1, or report this OAuth response shape as a bug.",
                ],
            )
    auth = api.post_json("auth.test", bot_token, {})
    bot_user_id = auth.get("user_id", "")

    xapp_token = prompt_secret(
        "Step 6/6 - In Basic Information -> App-Level Tokens, Generate Token and Scopes "
        "(about 4 clicks). Add `connections:write`, then paste xapp- token: "
    ).strip()
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
    write_workspace_config(
        workspace,
        app_id=app_id,
        bot_user_id=bot_user_id,
        app_name=app_name,
        trigger_mode=trigger_mode,
        watched_user_id=watched_user_id,
    )
    messages.append("Saved Slack tokens with restrictive file permissions.")
    messages.append("Slack setup complete: bot can authenticate and Socket Mode can open.")
    return SlackSetupResult(ok=True, messages=messages)


def _confirm(prompt: PromptFn, text: str) -> bool:
    return prompt(text).strip().lower() in {"y", "yes"}


def _resolve_redirect_url(prompt: PromptFn, messages: list[str]) -> str:
    port = 8765
    if _port_in_use(port):
        answer = prompt(
            "Step 5/6 - Local OAuth callback port 8765 is busy. "
            "Continue and use copy/paste fallback if the callback cannot bind? [Y/n] "
        ).strip().lower()
        if answer in {"n", "no"}:
            raise SlackApiError("Port 8765 is busy.")
        messages.append("Port 8765 is busy; OAuth will use copy/paste fallback if needed.")
    return f"http://localhost:{port}/callback"


def _collect_oauth_code(
    redirect_url: str,
    oauth_url: str,
    prompt: PromptFn,
    output: OutputFn,
    messages: list[str],
    *,
    timeout_seconds: int,
    on_server_started: Callable[[], str | None] | None = None,
) -> str:
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

    try:
        server = http.server.HTTPServer(("localhost", port), Handler)
    except OSError:
        value = prompt(
            "Step 5/6 - Local callback could not start.\n"
            f"  Open this URL: {oauth_url}\n"
            "  Paste final callback URL or OAuth code from your browser: "
        ).strip()
        return _extract_code(value)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output(
        "Step 5/6 - Install the app with OAuth.\n"
        f"  Open this URL: {oauth_url}\n"
        "  If your browser can reach localhost, Innie continues automatically after approval.\n"
        "  If the browser cannot reach localhost, wait for the paste fallback below.\n"
    )
    if on_server_started:
        callback_code = on_server_started()
        if callback_code:
            result["code"] = callback_code
            server.shutdown()
    messages.append(f"Waiting up to {timeout_seconds} seconds for OAuth callback on localhost:{port}.")
    messages.append("Remote/cloud users: if the browser fails to load localhost, copy the full URL and paste it here.")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and "code" not in result:
        time.sleep(0.1)
    if "code" not in result:
        server.shutdown()
        value = prompt("Step 5/6 - Paste final callback URL or OAuth code from your browser: ").strip()
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


def _clear_terminal(output: OutputFn) -> None:
    output("\033[2J\033[H")
