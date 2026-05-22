from __future__ import annotations

import argparse
from pathlib import Path

from .bootstrap import init_workspace


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        result = init_workspace(args.workspace, assume_yes=args.yes)
        for line in result.messages:
            print(line)
        return 0 if result.ok else 1

    parser.error(f"unsupported command: {args.command}")
    return 2
