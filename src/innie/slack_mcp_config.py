from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from .config import innie_dir, load_secrets


MCP_SERVER_NAME = "innie_slack"
SLACK_BOT_TOKEN_ENV = "INNIE_SLACK_BOT_TOKEN"


def slack_mcp_process_env(workspace: str) -> dict[str, str] | None:
    token = load_secrets(Path(workspace)).get("slack_bot_token")
    if not token:
        return None
    env = dict(os.environ)
    env[SLACK_BOT_TOKEN_ENV] = token
    return env


def claude_slack_mcp_config_path(workspace: str) -> str | None:
    if slack_mcp_process_env(workspace) is None:
        return None
    path = innie_dir(Path(workspace)) / "runtime" / "slack-mcp-claude.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    MCP_SERVER_NAME: {
                        "command": sys.executable,
                        "args": ["-m", "innie.slack_mcp"],
                    }
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return str(path)


def codex_slack_mcp_config_args(workspace: str) -> tuple[str, ...]:
    if slack_mcp_process_env(workspace) is None:
        return ()
    return (
        "-c",
        f"mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(sys.executable)}",
        "-c",
        f"mcp_servers.{MCP_SERVER_NAME}.args={json.dumps(['-m', 'innie.slack_mcp'])}",
    )
