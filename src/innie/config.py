from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
import json
import os


def innie_dir(workspace: Path) -> Path:
    return workspace.resolve() / ".innie"


def secrets_path(workspace: Path) -> Path:
    return innie_dir(workspace) / "secrets.json"


def config_path(workspace: Path) -> Path:
    return innie_dir(workspace) / "config.yaml"


@dataclass(frozen=True)
class WorkspaceConfig:
    bot_user_id: str | None = None
    watched_user_id: str | None = None
    harness_selected: str | None = None


def load_secrets(workspace: Path) -> dict[str, str]:
    path = secrets_path(workspace)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_secrets(workspace: Path, secrets: dict[str, str]) -> None:
    path = secrets_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(secrets, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def write_workspace_config(
    workspace: Path,
    *,
    app_id: str,
    bot_user_id: str,
    app_name: str,
    trigger_mode: str | None = None,
    watched_user_id: str | None = None,
) -> None:
    path = config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_harness = "codex"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("selected:"):
                existing_harness = line.split(":", 1)[1].strip() or "null"
                break
    path.write_text(
        "\n".join(
            [
                "# Innie local workspace config.",
                "# Non-secret metadata belongs here. Tokens should be stored separately.",
                "workspace_version: 1",
                "slack:",
                "  configured: true",
                f"  app_id: {app_id}",
                f"  bot_user_id: {bot_user_id}",
                f"  app_name: {app_name}",
                f"  trigger_mode: {trigger_mode or 'bot_mention'}",
                f"  watched_user_id: {watched_user_id or 'null'}",
                "harness:",
                f"  selected: {existing_harness}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def read_workspace_config(workspace: Path) -> WorkspaceConfig:
    path = config_path(workspace)
    if not path.exists():
        return WorkspaceConfig()
    section: str | None = None
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw_line.startswith(" ") and stripped.endswith(":"):
            section = stripped[:-1]
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        full_key = f"{section}.{key.strip()}" if section else key.strip()
        values[full_key] = _null_to_none(value.strip())
    return WorkspaceConfig(
        bot_user_id=values.get("slack.bot_user_id"),
        watched_user_id=values.get("slack.watched_user_id"),
        harness_selected=values.get("harness.selected"),
    )


def _null_to_none(value: str) -> str | None:
    return None if value in {"", "null", "None"} else value
