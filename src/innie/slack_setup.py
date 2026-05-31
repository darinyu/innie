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

from .config import load_secrets, secret_store_for_workspace, write_secrets, write_workspace_config
from .terminal_ui import WizardUI, success_panel


PromptFn = Callable[[str], str]
SecretPromptFn = Callable[[str], str]
OutputFn = Callable[[str], None]


BOT_SCOPES = [
    "app_mentions:read",
    "channels:history",
    "chat:write",
    "files:read",
    "files:write",
    "groups:history",
    "im:write",
    "reactions:read",
    "reactions:write",
]
BOT_EVENTS = ["app_mention", "message.channels", "message.groups"]


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
) -> dict[str, Any]:
    events = list(BOT_EVENTS)

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
            "interactivity": {"is_enabled": True},
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
    ui = WizardUI(output)
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

    ui.step(
        "Slack setup",
        "5-8 minutes",
        "Innie will create a Slack manifest, guide OAuth install, and store tokens locally. "
        "Use docs/slack-setup.md if you want screenshots beside the wizard.",
    )
    messages.append("Slack setup started.")
    ui.step("Step 1/6", "Name the Slack app", "About 1 minute. Names are modifiable in Slack later.")
    app_name = prompt(
        "Slack app name [innie]: "
    ).strip() or "innie"
    display_name = prompt(
        "Bot display name [Innie]: "
    ).strip() or "Innie"
    ui.step(
        "Step 2/6",
        "Configure the watched user",
        "Innie responds when someone tags the installing Slack user, like @<username>, "
        "in channels where the app is present.",
    )
    messages.append("Innie will use the installing Slack user ID returned by OAuth.")
    redirect_url = _resolve_redirect_url(prompt, messages)
    manifest = build_manifest(
        app_name=app_name,
        display_name=display_name,
        redirect_urls=[redirect_url],
    )

    manifest_path = workspace / ".innie" / "slack-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    ui.step(
        "Step 3/6",
        "Create Slack app from manifest",
        "Open https://api.slack.com/apps\n"
        "Click Create New App -> From an app manifest -> select your workspace.\n"
        "Copy the manifest below and paste it into Slack.",
    )
    manifest_json = ui.manifest(manifest)
    manifest_path.write_text(manifest_json + "\n", encoding="utf-8")
    messages.append(f"Step 3/6 - Wrote Slack manifest for manual paste: {manifest_path}")
    prompt(
        "Press Enter after Slack creates the app."
    )
    ui.clear()

    ui.step(
        "Step 4/6",
        "Copy app credentials",
        "In Slack API, open Basic Information -> App Credentials. About 2 clicks.",
    )
    client_id = prompt(
        "Client ID: "
    ).strip()
    client_secret = prompt_secret("Client Secret: ").strip()
    app_id = prompt("App ID (optional but useful): ").strip()

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
    watched_user_id = (oauth_result.get("authed_user") or {}).get("id")
    if not watched_user_id:
        return SlackSetupResult(
            ok=False,
            messages=messages
            + [
                "Innie needs Slack to return the installing user ID from OAuth, but it was missing.",
                "Run setup again, or report this OAuth response shape as a bug.",
            ],
        )
    auth = api.post_json("auth.test", bot_token, {})
    bot_user_id = auth.get("user_id", "")

    ui.step(
        "Step 6/6",
        "Create Socket Mode token",
        "In Basic Information -> App-Level Tokens, click Generate Token and Scopes.\n"
        "Name the token `socket`.\n"
        "Add scope: `connections:write`.\n"
        "About 4 clicks.",
    )
    try:
        xapp_token = _prompt_xapp_token(prompt_secret, output)
    except SlackApiError as exc:
        return SlackSetupResult(ok=False, messages=[str(exc)])
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
    secret_store_description = secret_store_for_workspace(workspace).description
    write_workspace_config(
        workspace,
        app_id=app_id,
        bot_user_id=bot_user_id,
        app_name=app_name,
        watched_user_id=watched_user_id,
    )
    return SlackSetupResult(
        ok=True,
        messages=_success_summary(
            app_name=app_name,
            app_id=app_id,
            bot_user_id=bot_user_id,
            watched_user_id=watched_user_id,
            secret_store_description=secret_store_description,
        ),
    )


def _prompt_xapp_token(prompt_secret: SecretPromptFn, output: OutputFn) -> str:
    for attempt in range(3):
        xapp_token = prompt_secret("App-level token (xapp-...): ").strip()
        if xapp_token.startswith("xapp-"):
            return xapp_token
        if not xapp_token:
            output("No token entered. Paste the xapp- token, or press Ctrl-C to stop setup.")
        else:
            output("App-level token must start with xapp-. Try again, or press Ctrl-C to stop setup.")
    raise SlackApiError("App-level token must start with xapp-.")


def _success_summary(
    *,
    app_name: str,
    app_id: str,
    bot_user_id: str,
    watched_user_id: str | None,
    secret_store_description: str,
) -> list[str]:
    watched_label = watched_user_id or "not used"
    body = "\n".join(
        [
            "  ___             _      ",
            " |_ _|_ __  _ __ (_) ___ ",
            "  | || '_ \\| '_ \\| |/ _ \\",
            "  | || | | | | | | |  __/",
            " |___|_| |_|_| |_|_|\\___|",
            "",
            "Slack setup complete.",
            "",
            "Status       ok",
            f"App          {app_name} ({app_id or 'unknown app id'})",
            f"Bot user     {bot_user_id or 'unknown bot user'}",
            "Trigger      watched user mention",
            f"Watched user {watched_label}",
            f"Tokens       saved to {secret_store_description}",
            "",
            "Next         innie run --once --harness codex",
            "Alt          innie run --once --harness claude",
            "Dashboard    innie dash",
        ]
    )
    return [success_panel("Slack setup complete", body)]


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
            _plain_text(
                [
                    "Step 5/6 - Local callback could not start.",
                    oauth_url,
                    "REMOTE BROWSER: Copy the browser's final callback URL and paste it here.",
                ]
            )
            + "\nPaste final callback URL or OAuth code: "
        ).strip()
        return _extract_code(value)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output(_render_oauth_instructions(oauth_url))
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


def _oauth_lines(oauth_url: str) -> list[str]:
    return [
        "Step 5/6 - Install the app with OAuth.",
        oauth_url,
        "LOCAL BROWSER: Approve in Slack. Innie continues automatically when the localhost callback opens.",
        "REMOTE BROWSER: If Slack cannot open localhost, copy the browser's final callback URL and paste it when prompted.",
    ]


def _plain_text(lines: list[str]) -> str:
    return "\n".join(lines)


def _render_oauth_instructions(oauth_url: str) -> str:
    lines = _oauth_lines(oauth_url)
    try:
        from rich.console import Console
        from rich.text import Text
    except ImportError:
        return _plain_text(lines)

    text = Text()
    text.append(lines[0] + "\n", style="bold cyan")
    text.append(lines[1] + "\n", style="bold")
    for line in lines[2:]:
        label, body = line.split(":", 1)
        text.append(label + ":", style="bold green" if label == "LOCAL BROWSER" else "bold yellow")
        text.append(body + "\n")
    from io import StringIO

    file = StringIO()
    console = Console(file=file, force_terminal=True, color_system="auto", width=max(120, len(oauth_url) + 1))
    console.print(text, end="")
    return file.getvalue().rstrip()


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
