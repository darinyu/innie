from __future__ import annotations

import argparse
from pathlib import Path

from .bootstrap import init_workspace
from .slack_setup import run_slack_setup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="innie")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace directory that should contain .innie/",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize a local Innie workspace")
    init_parser.add_argument(
        "--yes",
        action="store_true",
        help="Accept creating local Innie files and intentionally skip missing optional dependencies",
    )

    slack_parser = subparsers.add_parser("slack", help="Slack setup and diagnostics")
    slack_subparsers = slack_parser.add_subparsers(dest="slack_command", required=True)
    slack_subparsers.add_parser("setup", help="Run the Slack app setup wizard")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        result = init_workspace(args.workspace, assume_yes=args.yes)
        for line in result.messages:
            print(line)
        return 0 if result.ok else 1

    if args.command == "slack" and args.slack_command == "setup":
        result = run_slack_setup(args.workspace)
        for line in result.messages:
            print(line)
        return 0 if result.ok else 1

    parser.error(f"unsupported command: {args.command}")
    return 2
