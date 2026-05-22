from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
from pathlib import Path
from typing import Callable

from .adapters import CodexCliAdapter, EchoAdapter
from .config import innie_dir, load_secrets, read_workspace_config
from .db import connect, initialize_schema
from .harness import HarnessAdapter
from .pipeline import accept_slack_event
from .runtime import SessionManager
from .slack_client import SlackWebClient
from .slack_socket import SlackSocketModeEventSource


OutputFn = Callable[[str], None]


@dataclass(frozen=True)
class RunOnceResult:
    accepted: bool
    reason: str
    session_id: str | None = None
    payload: dict | None = None
    session_status: str | None = None
    harness_id: str | None = None


class ConsoleSlackClient:
    def __init__(self, *, output: OutputFn = print) -> None:
        self._output = output

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self._output(f"reaction {channel} {timestamp} {name}")

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        self._output(f"message {channel} {thread_ts} {text}")


def adapter_map() -> dict[str, HarnessAdapter]:
    return {
        "codex": CodexCliAdapter(),
        "echo": EchoAdapter(),
    }


def run_once_event_file(
    workspace: Path,
    event_file: Path,
    *,
    harness_id: str,
    bot_user_id: str,
    watched_user_id: str | None = None,
    slack: ConsoleSlackClient | None = None,
) -> RunOnceResult:
    payload = json.loads(event_file.read_text(encoding="utf-8"))
    return run_once_payload(
        workspace,
        payload,
        harness_id=harness_id,
        bot_user_id=bot_user_id,
        watched_user_id=watched_user_id,
        slack=slack or ConsoleSlackClient(),
    )


def run_once_payload(
    workspace: Path,
    payload: dict,
    *,
    harness_id: str,
    bot_user_id: str,
    watched_user_id: str | None = None,
    slack,
    adapters: dict[str, HarnessAdapter] | None = None,
) -> RunOnceResult:
    return asyncio.run(
        process_payload(
            workspace,
            payload,
            harness_id=harness_id,
            bot_user_id=bot_user_id,
            watched_user_id=watched_user_id,
            slack=slack,
            adapters=adapters,
        )
    )


def run_once_socket(
    workspace: Path,
    *,
    harness_id: str | None = None,
    bot_user_id: str | None = None,
    watched_user_id: str | None = None,
    slack=None,
    event_source=None,
    adapters: dict[str, HarnessAdapter] | None = None,
    output: OutputFn | None = None,
) -> RunOnceResult:
    return asyncio.run(
        _run_until_accepted_socket_async(
            workspace,
            harness_id=harness_id,
            bot_user_id=bot_user_id,
            watched_user_id=watched_user_id,
            slack=slack,
            event_source=event_source,
            adapters=adapters,
            output=output,
        )
    )


def run_forever_socket(
    workspace: Path,
    *,
    harness_id: str | None = None,
    bot_user_id: str | None = None,
    watched_user_id: str | None = None,
    slack=None,
    event_source=None,
    adapters: dict[str, HarnessAdapter] | None = None,
    output: OutputFn = print,
) -> int:
    return asyncio.run(
        _run_forever_socket_async(
            workspace,
            harness_id=harness_id,
            bot_user_id=bot_user_id,
            watched_user_id=watched_user_id,
            slack=slack,
            event_source=event_source,
            adapters=adapters,
            output=output,
        )
    )


async def _run_forever_socket_async(
    workspace: Path,
    *,
    harness_id: str | None,
    bot_user_id: str | None,
    watched_user_id: str | None,
    slack,
    event_source,
    adapters: dict[str, HarnessAdapter] | None,
    output: OutputFn,
) -> int:
    accepted_count = 0
    seen_count = 0
    while True:
        try:
            output(f"waiting for Slack event #{seen_count + 1}")
            result = await _run_once_socket_async(
                workspace,
                harness_id=harness_id,
                bot_user_id=bot_user_id,
                watched_user_id=watched_user_id,
                slack=slack,
                event_source=event_source,
                adapters=adapters,
            )
        except KeyboardInterrupt:
            output(f"stopped after {accepted_count} accepted event(s)")
            return accepted_count
        seen_count += 1
        if result.accepted:
            accepted_count += 1
            output(format_run_acceptance(result))
        else:
            output(f"ignored event: {result.reason} {describe_slack_payload(result.payload)}".rstrip())


async def _run_once_socket_async(
    workspace: Path,
    *,
    harness_id: str | None,
    bot_user_id: str | None,
    watched_user_id: str | None,
    slack,
    event_source,
    adapters: dict[str, HarnessAdapter] | None,
) -> RunOnceResult:
    workspace = workspace.resolve()
    config = read_workspace_config(workspace)
    secrets = load_secrets(workspace)
    selected_harness = harness_id or config.harness_selected or "codex"
    selected_bot_user_id = bot_user_id or config.bot_user_id
    if not selected_bot_user_id:
        raise RuntimeError("Slack bot user id is missing. Run `innie slack setup` or pass --bot-user-id.")
    selected_watched_user_id = watched_user_id if watched_user_id is not None else config.watched_user_id
    selected_slack = slack or SlackWebClient(secrets["slack_bot_token"])
    selected_source = event_source or SlackSocketModeEventSource(secrets["slack_app_token"])
    payload = await selected_source.receive_once()
    return await process_payload(
        workspace,
        payload,
        harness_id=selected_harness,
        bot_user_id=selected_bot_user_id,
        watched_user_id=selected_watched_user_id,
        slack=selected_slack,
        adapters=adapters,
    )


async def _run_until_accepted_socket_async(
    workspace: Path,
    *,
    harness_id: str | None,
    bot_user_id: str | None,
    watched_user_id: str | None,
    slack,
    event_source,
    adapters: dict[str, HarnessAdapter] | None,
    output: OutputFn | None,
) -> RunOnceResult:
    while True:
        result = await _run_once_socket_async(
            workspace,
            harness_id=harness_id,
            bot_user_id=bot_user_id,
            watched_user_id=watched_user_id,
            slack=slack,
            event_source=event_source,
            adapters=adapters,
        )
        if result.accepted:
            return result
        if output is not None:
            output(f"ignored event: {result.reason} {describe_slack_payload(result.payload)}".rstrip())


async def process_payload(
    workspace: Path,
    payload: dict,
    *,
    harness_id: str,
    bot_user_id: str,
    watched_user_id: str | None = None,
    slack,
    adapters: dict[str, HarnessAdapter] | None = None,
) -> RunOnceResult:
    workspace = workspace.resolve()
    db_path = innie_dir(workspace) / "innie.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    initialize_schema(db)
    try:
        existing_session_id = _existing_session_id_for_payload(db, payload)
        accepted = accept_slack_event(
            db,
            payload,
            bot_user_id=bot_user_id,
            watched_user_id=watched_user_id,
            slack=slack,
            harness_id=harness_id,
        )
    finally:
        db.close()

    if not accepted.decision.accepted or accepted.session is None:
        return RunOnceResult(False, accepted.decision.reason, payload=payload)
    session_status = "existing" if existing_session_id == accepted.session.id else "new"

    manager = SessionManager(
        db_path,
        adapters=adapters or adapter_map(),
        slack=slack,
        workspace=workspace,
    )
    try:
        await manager.run_until_idle()
    finally:
        manager.close()
    return RunOnceResult(
        True,
        accepted.decision.reason,
        accepted.session.id,
        payload=payload,
        session_status=session_status,
        harness_id=accepted.session.harness_id,
    )


def format_run_acceptance(result: RunOnceResult) -> str:
    line = f"accepted {result.session_status or 'unknown'} session {result.session_id}"
    if result.harness_id:
        line = f"{line} via {result.harness_id}"
    return line


def _existing_session_id_for_payload(db, payload: dict) -> str | None:
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return None
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts")
    if not channel_id or not thread_ts:
        return None
    row = db.execute(
        """
        SELECT id
        FROM sessions
        WHERE slack_channel_id = ? AND slack_root_ts = ?
        """,
        (str(channel_id), str(thread_ts)),
    ).fetchone()
    return None if row is None else row["id"]


def describe_slack_payload(payload: dict) -> str:
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return ""
    parts = [
        ("event_id", payload.get("event_id")),
        ("type", event.get("type")),
        ("subtype", event.get("subtype")),
        ("channel_type", event.get("channel_type")),
        ("channel", event.get("channel")),
        ("ts", event.get("ts")),
        ("user", event.get("user")),
        ("bot_id", event.get("bot_id")),
        ("text", _preview(str(event.get("text") or ""))),
    ]
    return " ".join(f"{key}={value}" for key, value in parts if value)


def _preview(text: str, *, limit: int = 80) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
