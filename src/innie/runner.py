from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from .adapters import CodexCliAdapter, EchoAdapter
from .config import innie_dir, load_secrets, read_workspace_config
from .db import connect, initialize_schema
from .harness import HarnessAdapter, HarnessEvent
from .pipeline import accept_slack_event
from .progress import HIDE_PROGRESS_ACTION_ID, SHOW_PROGRESS_ACTION_ID, SLACK_FINAL_TEXT_LIMIT, SlackProgressRenderer
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
    resolved_slack: Any | None = None


class ConsoleSlackClient:
    def __init__(self, *, output: OutputFn = print) -> None:
        self._output = output

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self._output(f"reaction {channel} {timestamp} {name}")

    def post_message(self, *, channel: str, thread_ts: str, text: str, blocks: list[dict[str, Any]] | None = None) -> str:
        self._output(f"message {channel} {thread_ts} {text}")
        return thread_ts

    def update_message(self, *, channel: str, ts: str, text: str, blocks: list[dict[str, Any]] | None = None) -> None:
        self._output(f"update {channel} {ts} {text}")

    def delete_message(self, *, channel: str, ts: str) -> None:
        self._output(f"delete {channel} {ts}")


def adapter_map(*, verbose: bool = False, output: OutputFn | None = None) -> dict[str, HarnessAdapter]:
    return {
        "codex": CodexCliAdapter(verbose=verbose, output=output),
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
    verbose: bool = False,
    output: OutputFn | None = None,
    max_workers: int = 7,
) -> RunOnceResult:
    payload = json.loads(event_file.read_text(encoding="utf-8"))
    return run_once_payload(
        workspace,
        payload,
        harness_id=harness_id,
        bot_user_id=bot_user_id,
        watched_user_id=watched_user_id,
        slack=slack or ConsoleSlackClient(),
        verbose=verbose,
        output=output,
        max_workers=max_workers,
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
    verbose: bool = False,
    output: OutputFn | None = None,
    max_workers: int = 7,
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
            verbose=verbose,
            output=output,
            max_workers=max_workers,
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
    verbose: bool = False,
    max_workers: int = 7,
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
            verbose=verbose,
            max_workers=max_workers,
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
    verbose: bool = False,
    max_workers: int = 7,
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
            verbose=verbose,
            max_workers=max_workers,
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
    verbose: bool,
    max_workers: int,
) -> int:
    accepted_count = 0
    seen_count = 0
    drain_task: asyncio.Task | None = None
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
                verbose=verbose,
                output=output,
                announce_acceptance=False,
                max_workers=max_workers,
                drain=False,
            )
        except KeyboardInterrupt:
            if drain_task is not None:
                try:
                    await drain_task
                except Exception as exc:
                    output(f"worker drain failed during shutdown: {exc}")
            output(f"stopped after {accepted_count} accepted event(s)")
            return accepted_count
        seen_count += 1
        if result.accepted:
            accepted_count += 1
            output(format_run_acceptance(result))
            if drain_task is None or drain_task.done():
                if drain_task is not None:
                    try:
                        drain_task.result()
                    except Exception as exc:
                        output(f"worker drain failed: {exc}")
                drain_task = asyncio.create_task(
                    _drain_workspace(
                        workspace,
                        slack=result.resolved_slack or slack,
                        adapters=adapters,
                        verbose=verbose,
                        output=output,
                        max_workers=max_workers,
                    )
                )
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
    verbose: bool = False,
    output: OutputFn | None = None,
    announce_acceptance: bool = True,
    max_workers: int = 7,
    drain: bool = True,
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
        verbose=verbose,
        output=output,
        announce_acceptance=announce_acceptance,
        max_workers=max_workers,
        drain=drain,
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
    verbose: bool,
    max_workers: int,
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
            verbose=verbose,
            output=output,
            max_workers=max_workers,
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
    verbose: bool = False,
    output: OutputFn | None = None,
    announce_acceptance: bool = True,
    max_workers: int = 7,
    drain: bool = True,
) -> RunOnceResult:
    workspace = workspace.resolve()
    db_path = innie_dir(workspace) / "innie.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    initialize_schema(db)
    try:
        if _is_progress_details_interaction(payload):
            _handle_progress_details_interaction(db, payload, slack)
            db.commit()
            return RunOnceResult(True, "progress_details", payload=payload)
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
    result = RunOnceResult(
        True,
        accepted.decision.reason,
        accepted.session.id,
        payload=payload,
        session_status=session_status,
        harness_id=accepted.session.harness_id,
        resolved_slack=slack,
    )
    if verbose and announce_acceptance and output is not None:
        output(format_run_acceptance(result))

    if drain:
        await _drain_workspace(
            workspace,
            slack=slack,
            adapters=adapters,
            verbose=verbose,
            output=output,
            max_workers=max_workers,
        )
    return result


async def _drain_workspace(
    workspace: Path,
    *,
    slack,
    adapters: dict[str, HarnessAdapter] | None,
    verbose: bool,
    output: OutputFn | None,
    max_workers: int,
) -> None:
    db_path = innie_dir(workspace) / "innie.db"
    manager = SessionManager(
        db_path,
        adapters=adapters or adapter_map(verbose=verbose, output=output),
        slack=slack,
        workspace=workspace,
        event_output=output if verbose else None,
        max_workers=max_workers,
    )
    try:
        await manager.run_until_idle()
    finally:
        manager.close()


def format_run_acceptance(result: RunOnceResult) -> str:
    if result.accepted and result.session_id is None:
        return f"handled {result.reason}"
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


def _is_progress_details_interaction(payload: dict) -> bool:
    if payload.get("type") != "block_actions":
        return False
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return False
    return any(
        isinstance(action, dict) and action.get("action_id") in {SHOW_PROGRESS_ACTION_ID, HIDE_PROGRESS_ACTION_ID}
        for action in actions
    )


def _handle_progress_details_interaction(db, payload: dict, slack) -> None:
    action = _progress_details_action(payload)
    if action is None:
        return
    task_id = str(action.get("value") or "")
    if not task_id:
        return
    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    channel_id = str(channel.get("id") or "")
    message_ts = str(message.get("ts") or "")
    if not channel_id or not message_ts:
        return
    renderer = SlackProgressRenderer()
    details = _progress_details_for_task(db, renderer, task_id)
    final_text = _first_final_text_for_task(db, renderer, task_id, details)
    if final_text is None:
        final_text = _fallback_interaction_message_text(message)
    if action.get("action_id") == HIDE_PROGRESS_ACTION_ID:
        rendered = renderer.render_collapsed_final_widget(task_id, final_text, details)
    else:
        rendered = renderer.render_expanded_final_widget(task_id, final_text, details)
    try:
        slack.update_message(channel=channel_id, ts=message_ts, text=rendered.text, blocks=rendered.blocks)
    except Exception:
        return


def _progress_details_action(payload: dict) -> dict | None:
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return None
    for action in actions:
        if isinstance(action, dict) and action.get("action_id") in {SHOW_PROGRESS_ACTION_ID, HIDE_PROGRESS_ACTION_ID}:
            return action
    return None


def _progress_details_for_task(db, renderer: SlackProgressRenderer, task_id: str) -> list[str]:
    rows = db.execute(
        """
        SELECT payload_json
        FROM task_events
        WHERE task_id = ? AND event_type = 'harness.progress'
        ORDER BY id ASC
        """,
        (task_id,),
    ).fetchall()
    details: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        message = payload.get("message")
        if not message:
            continue
        detail = renderer.detail_line(HarnessEvent(type="progress", message=str(message)))
        if detail is not None:
            details.append(detail)
    return details


def _first_final_text_for_task(
    db,
    renderer: SlackProgressRenderer,
    task_id: str,
    progress_details: list[str],
) -> str | None:
    row = db.execute(
        """
        SELECT event_type, payload_json
        FROM task_events
        WHERE task_id = ?
          AND event_type IN ('harness.output', 'harness.failed', 'harness.canceled')
        ORDER BY id DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    event_type = str(payload.get("type") or str(row["event_type"]).removeprefix("harness."))
    message = payload.get("message")
    event = HarnessEvent(
        type=event_type,
        message=None if message is None else str(message),
        payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    )
    messages = renderer.render_final_messages(task_id, event, progress_details)
    if not messages:
        return None
    return messages[0].text


def _fallback_interaction_message_text(message: dict, *, limit: int = SLACK_FINAL_TEXT_LIMIT) -> str:
    text = str(message.get("text") or "")
    if len(text) <= limit:
        return text
    split_at = text.rfind("\n", 0, limit + 1)
    if split_at <= 0:
        split_at = limit
    return text[:split_at]


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
