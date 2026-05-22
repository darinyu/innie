from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
import sqlite3
import sys
from typing import Callable

from .db import connect, initialize_schema


SUPPORTED_HARNESSES = ("codex", "claude", "opencode", "goose")
MIN_PYTHON = (3, 10)
CONFIG_TEMPLATE = """# Innie local workspace config.
# Non-secret metadata belongs here. Tokens should be stored separately.
workspace_version: 1
slack:
  configured: false
harness:
  selected: null
"""


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    ok: bool
    detail: str
    required: bool = False


@dataclass(frozen=True)
class InitResult:
    ok: bool
    messages: list[str]


def check_dependencies(workspace: Path) -> list[DependencyStatus]:
    statuses: list[DependencyStatus] = []
    python_version = (sys.version_info.major, sys.version_info.minor)
    statuses.append(
        DependencyStatus(
            name="python",
            ok=python_version >= MIN_PYTHON,
            detail=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            required=True,
        )
    )

    sqlite_version = sqlite3.sqlite_version
    statuses.append(
        DependencyStatus(
            name="sqlite",
            ok=True,
            detail=sqlite_version,
            required=True,
        )
    )

    slack_config_exists = (workspace / ".innie" / "config.yaml").exists() or any(
        key in sys.modules for key in ("slack_sdk",)
    )
    statuses.append(
        DependencyStatus(
            name="slack_config",
            ok=slack_config_exists,
            detail="found .innie/config.yaml" if slack_config_exists else "not configured yet; run innie slack setup",
            required=False,
        )
    )

    found_harnesses = [name for name in SUPPORTED_HARNESSES if shutil.which(name)]
    statuses.append(
        DependencyStatus(
            name="agent_harness",
            ok=bool(found_harnesses),
            detail=", ".join(found_harnesses) if found_harnesses else "none found: codex, claude, opencode, goose",
            required=False,
        )
    )
    return statuses


def init_workspace(
    workspace: Path,
    *,
    assume_yes: bool = False,
    input_fn: Callable[[str], str] = input,
) -> InitResult:
    workspace = workspace.resolve()
    statuses = check_dependencies(workspace)
    messages = [_format_status(status) for status in statuses]

    missing_required = [status for status in statuses if status.required and not status.ok]
    if missing_required:
        messages.append("Missing required dependencies. Innie did not create workspace files.")
        return InitResult(ok=False, messages=messages)

    missing_optional = [status for status in statuses if not status.required and not status.ok]
    if missing_optional:
        messages.append("Optional dependencies are missing. Innie will not install anything automatically.")
        if not assume_yes:
            answer = input_fn("Continue and create local workspace anyway? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                messages.append("Canceled before creating workspace files.")
                return InitResult(ok=False, messages=messages)

    innie_dir = workspace / ".innie"
    artifacts_dir = innie_dir / "artifacts"
    innie_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    config_path = innie_dir / "config.yaml"
    if not config_path.exists():
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    db_path = innie_dir / "innie.db"
    with connect(db_path) as db:
        initialize_schema(db)
        _record_dependency_status(db, statuses)

    messages.append(f"Created workspace: {innie_dir}")
    messages.append(f"Initialized database: {db_path}")
    return InitResult(ok=True, messages=messages)


def _format_status(status: DependencyStatus) -> str:
    marker = "ok" if status.ok else "missing"
    required = "required" if status.required else "optional"
    return f"{status.name}: {marker} ({required}) - {status.detail}"


def _record_dependency_status(db: sqlite3.Connection, statuses: list[DependencyStatus]) -> None:
    payload = [
        {
            "name": status.name,
            "ok": status.ok,
            "detail": status.detail,
            "required": status.required,
        }
        for status in statuses
    ]
    db.execute(
        """
        INSERT INTO task_events(session_id, event_type, payload_json)
        VALUES(NULL, 'workspace.dependencies_checked', ?)
        """,
        (json.dumps(payload, sort_keys=True),),
    )
