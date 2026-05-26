from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from .config import innie_dir, load_secrets


MCP_SERVER_NAME = "innie_slack"
SLACK_BOT_TOKEN_ENV = "INNIE_SLACK_BOT_TOKEN"
SLACK_MCP_TOOLS = ("slack_get_thread", "slack_get_message", "slack_get_permalink", "slack_get_channel_history")


def slack_mcp_process_env(workspace: str, recovery_context: dict | None = None) -> dict[str, str] | None:
    token = load_secrets(Path(workspace)).get("slack_bot_token")
    if not token:
        return None
    env = dict(os.environ)
    env[SLACK_BOT_TOKEN_ENV] = token
    return env


def slack_mcp_config_path(workspace: str) -> str | None:
    if slack_mcp_process_env(workspace) is None:
        return None
    workspace_path = str(Path(workspace).resolve())
    path = innie_dir(Path(workspace)) / "runtime" / "slack-mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    MCP_SERVER_NAME: {
                        "command": sys.executable,
                        "args": ["-m", "innie.slack_mcp", "--workspace", workspace_path],
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
    workspace_path = str(Path(workspace).resolve())
    args = [
        "-c",
        f"mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(sys.executable)}",
        "-c",
        f"mcp_servers.{MCP_SERVER_NAME}.args={json.dumps(['-m', 'innie.slack_mcp', '--workspace', workspace_path])}",
    ]
    for tool_name in SLACK_MCP_TOOLS:
        args.extend(["-c", f"mcp_servers.{MCP_SERVER_NAME}.tools.{tool_name}.approval_mode=\"approve\""])
    return tuple(args)
