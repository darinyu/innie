from __future__ import annotations

import argparse
from pathlib import Path

try:
    from rich.console import Console
except ImportError:  # pragma: no cover - exercised when rich is not installed
    Console = None

from .bootstrap import init_workspace
from .config import innie_dir
from .control import cancel_session, summarize_session
from .db import connect, initialize_schema
from .prompting import prompt_masked_secret
from .run_logging import RunLogger
from .runner import ConsoleSlackClient, format_run_acceptance, run_forever_socket, run_once_event_file, run_once_socket
from .slack_setup import run_slack_setup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="innie")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        dest="workspace",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        dest="state_dir",
        help="Directory where Innie stores durable local state in .innie/",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize Innie and start guided setup")
    init_parser.add_argument(
        "--yes",
        action="store_true",
        help="Accept default setup choices",
    )
    init_parser.add_argument(
        "--skip-slack-setup",
        action="store_true",
        help="Only create local durable state; do not start Slack setup",
    )

    slack_parser = subparsers.add_parser("slack", help="Slack setup and diagnostics")
    slack_subparsers = slack_parser.add_subparsers(dest="slack_command", required=True)
    slack_subparsers.add_parser("setup", help="Run the Slack app setup wizard")

    status_parser = subparsers.add_parser("status", help="Show a durable session summary")
    status_parser.add_argument("session_id")

    logs_parser = subparsers.add_parser("logs", help="Show durable session logs")
    logs_parser.add_argument("session_id")

    cancel_parser = subparsers.add_parser("cancel", help="Cancel a durable session")
    cancel_parser.add_argument("session_id")

    run_parser = subparsers.add_parser("run", help="Run Innie against Slack or one Slack-shaped event")
    run_parser.add_argument("--once", action="store_true", help="Process one event and exit")
    run_parser.add_argument("--event-file", type=Path, default=None, help="Slack event payload JSON file")
    run_parser.add_argument("--harness", choices=("echo", "codex"), default="codex", help="Harness adapter to use")
    run_parser.add_argument("--bot-user-id", default="U_BOT", help="Bot user id for local event-file runs")
    run_parser.add_argument("--watched-user-id", default=None, help="Optional watched user id for mention mode")
    run_parser.add_argument("--verbose", action="store_true", help="Print verbose runtime diagnostics")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state_dir = args.state_dir or args.workspace

    if args.command == "init":
        result = init_workspace(state_dir, assume_yes=args.yes)
        for line in result.messages:
            print(line)
        if not result.ok:
            return 1
        if args.skip_slack_setup:
            print("Skipped Slack setup. Run `innie slack setup` when ready.")
            return 0
        if args.yes or _confirm_default_yes("Set up Slack now? [Y/n] "):
            slack_result = run_slack_setup(state_dir, prompt_secret=prompt_masked_secret)
            for line in slack_result.messages:
                print(line)
            return 0 if slack_result.ok else 1
        print("Skipped Slack setup. Run `innie slack setup` when ready.")
        return 0

    if args.command == "slack" and args.slack_command == "setup":
        result = run_slack_setup(state_dir, prompt_secret=prompt_masked_secret)
        for line in result.messages:
            print(line)
        return 0 if result.ok else 1

    if args.command == "status":
        with _open_workspace_db(state_dir) as db:
            print(summarize_session(db, args.session_id))
        return 0

    if args.command == "logs":
        with _open_workspace_db(state_dir) as db:
            print(_format_logs(db, args.session_id))
        return 0

    if args.command == "cancel":
        with _open_workspace_db(state_dir) as db:
            print(cancel_session(db, args.session_id))
        return 0

    if args.command == "run":
        stdout_output = _run_output(verbose=args.verbose)
        run_logger = RunLogger(state_dir, output=stdout_output)
        run_output = run_logger.emit
        if args.event_file is not None and not args.once:
            parser.error("`innie run --event-file` requires --once")
        run_output(f"run log: {run_logger.path}")
        run_output(f"Innie run starting: harness={args.harness} once={args.once} continuous={not args.once}")
        if args.event_file is None:
            if args.once:
                run_output("Socket Mode enabled; waiting for one accepted Slack event...")
                result = run_once_socket(
                    state_dir,
                    harness_id=args.harness,
                    bot_user_id=None if args.bot_user_id == "U_BOT" else args.bot_user_id,
                    watched_user_id=args.watched_user_id,
                    output=run_output,
                    verbose=args.verbose,
                )
            else:
                run_output("Socket Mode enabled; listening until interrupted with Ctrl-C...")
                run_forever_socket(
                    state_dir,
                    harness_id=args.harness,
                    bot_user_id=None if args.bot_user_id == "U_BOT" else args.bot_user_id,
                    watched_user_id=args.watched_user_id,
                    output=run_output,
                    verbose=args.verbose,
                )
                return 0
        else:
            run_output(f"Reading one Slack event from {args.event_file}")
            result = run_once_event_file(
                state_dir,
                args.event_file,
                harness_id=args.harness,
                bot_user_id=args.bot_user_id,
                watched_user_id=args.watched_user_id,
                slack=ConsoleSlackClient(),
                verbose=args.verbose,
                output=run_output,
            )
        if not result.accepted:
            run_output(f"ignored event: {result.reason}")
            run_output("processed one event-file event; exiting because --once was set")
            return 0
        run_output(format_run_acceptance(result))
        run_output(f"logs: innie --workspace {state_dir} logs {result.session_id}")
        run_output("processed one accepted event; exiting because --once was set")
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


def _open_workspace_db(workspace: Path):
    db_path = innie_dir(workspace) / "innie.db"
    if not db_path.exists():
        raise SystemExit(f"Innie database not found: {db_path}. Run `innie init` first.")
    db = connect(db_path)
    initialize_schema(db)
    return db


def _confirm_default_yes(prompt: str) -> bool:
    return input(prompt).strip().lower() not in {"n", "no"}


def _print_run(message: str) -> None:
    print(message, flush=True)


def _run_output(*, verbose: bool):
    if not verbose or Console is None:
        return _print_run
    console = Console()

    def output(message: str) -> None:
        console.print(message, highlight=False)

    return output


def _format_logs(db, session_id: str) -> str:
    session = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        return f"Session {session_id} not found."
    lines = [
        f"session: {session['id']}",
        f"status: {session['status']}",
        f"output_target: {session['output_target']}",
        "",
        "inbox:",
    ]
    inbox_rows = db.execute(
        """
        SELECT id, status, slack_message_ts, text
        FROM session_inbox
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    lines.extend(
        f"  #{row['id']} {row['status']} {row['slack_message_ts']} {row['text']}"
        for row in inbox_rows
    )
    if not inbox_rows:
        lines.append("  none")

    lines.extend(["", "tasks:"])
    task_rows = db.execute(
        """
        SELECT id, status, harness_id, execution_mode, goal
        FROM tasks
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    ).fetchall()
    lines.extend(
        f"  {row['id']} {row['status']} {row['harness_id']} {row['execution_mode']} {row['goal']}"
        for row in task_rows
    )
    if not task_rows:
        lines.append("  none")

    lines.extend(["", "task_events:"])
    task_rows = db.execute(
        """
        SELECT id, task_id, event_type, created_at, payload_json
        FROM task_events
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    lines.extend(
        f"  #{row['id']} task={row['task_id'] or 'none'} {row['created_at']} {row['event_type']} {row['payload_json']}"
        for row in task_rows
    )
    if not task_rows:
        lines.append("  none")

    lines.extend(["", "hook_events:"])
    hook_rows = db.execute(
        """
        SELECT id, hook_name, status, duration_ms, created_at, payload_json
        FROM hook_events
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    lines.extend(
        f"  #{row['id']} {row['created_at']} {row['hook_name']} {row['status']} "
        f"{row['duration_ms']}ms {row['payload_json']}"
        for row in hook_rows
    )
    if not hook_rows:
        lines.append("  none")

    lines.extend(["", "artifacts:"])
    artifact_rows = db.execute(
        """
        SELECT id, task_id, kind, path, metadata_json
        FROM artifacts
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ).fetchall()
    lines.extend(
        f"  #{row['id']} task={row['task_id'] or 'none'} {row['kind']} {row['path']} {row['metadata_json']}"
        for row in artifact_rows
    )
    if not artifact_rows:
        lines.append("  none")

    lines.extend(["", "harness_capabilities:"])
    capability_rows = db.execute(
        """
        SELECT DISTINCT hc.harness_id, hc.capabilities_json
        FROM harness_capabilities hc
        JOIN tasks t ON t.harness_id = hc.harness_id
        WHERE t.session_id = ?
        ORDER BY hc.harness_id ASC
        """,
        (session_id,),
    ).fetchall()
    lines.extend(f"  {row['harness_id']} {row['capabilities_json']}" for row in capability_rows)
    if not capability_rows:
        lines.append("  none")

    return "\n".join(lines)
