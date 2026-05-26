from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
import sqlite3
import sys
from typing import Callable

from .db import connect, initialize_schema


SUPPORTED_HARNESSES = ("codex", "claude")
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
            detail=", ".join(found_harnesses) if found_harnesses else "none found: codex, claude",
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
        messages.append("Missing required dependencies. Innie did not create local state.")
        return InitResult(ok=False, messages=messages)

    missing_optional = [
        status
        for status in statuses
        if not status.required and not status.ok and status.name != "slack_config"
    ]
    if missing_optional:
        messages.append("Optional setup is incomplete. Innie will not install or change tools without approval.")
        if not assume_yes:
            answer = input_fn("Continue and create Innie local state anyway? [Y/n] ").strip().lower()
            if answer in {"n", "no"}:
                messages.append("Canceled before creating local state.")
                return InitResult(ok=False, messages=messages)

    innie_dir = workspace / ".innie"
    artifacts_dir = innie_dir / "artifacts"
    innie_dir_exists = innie_dir.exists()
    config_path = innie_dir / "config.yaml"
    config_exists = config_path.exists()
    db_path = innie_dir / "innie.db"
    innie_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    with connect(db_path) as db:
        initialize_schema(db)
        _record_dependency_status(db, statuses)

    if innie_dir_exists:
        messages.append(f"Using existing Innie local state: {innie_dir}")
    else:
        messages.append(f"Created Innie local state: {innie_dir}")
    if config_exists:
        messages.append(f"Using existing workspace config: {config_path}")
    else:
        messages.append(f"Created workspace config: {config_path}")
    messages.append(f"Initialized or verified database: {db_path}")
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
