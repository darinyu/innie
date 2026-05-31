from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from importlib import metadata
import json
import os
from typing import Callable, Protocol


def innie_dir(workspace: Path) -> Path:
    return workspace.resolve() / ".innie"


def secrets_path(workspace: Path) -> Path:
    return innie_dir(workspace) / "secrets.json"


def config_path(workspace: Path) -> Path:
    return innie_dir(workspace) / "config.yaml"


@dataclass(frozen=True)
class SecretStoreConfig:
    provider: str = "local"
    path: str | None = None


@dataclass(frozen=True)
class WorkspaceConfig:
    bot_user_id: str | None = None
    watched_user_id: str | None = None
    harness_selected: str | None = None
    secret_store: SecretStoreConfig = field(default_factory=SecretStoreConfig)


class SecretStore(Protocol):
    description: str

    def load(self) -> dict[str, str]:
        ...

    def write(self, secrets: dict[str, str]) -> None:
        ...


SecretStoreFactory = Callable[[Path, SecretStoreConfig], SecretStore]


class LocalFileSecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def description(self) -> str:
        return f"{self.path} (0600)"

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, secrets: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(secrets, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(self.path, 0o600)


_SECRET_STORE_FACTORIES: dict[str, SecretStoreFactory] = {}


def load_secrets(workspace: Path) -> dict[str, str]:
    return secret_store_for_workspace(workspace).load()


def write_secrets(workspace: Path, secrets: dict[str, str]) -> None:
    secret_store_for_workspace(workspace).write(secrets)


def register_secret_store(provider: str, factory: SecretStoreFactory) -> None:
    if provider == "local":
        raise ValueError("local secret store is built in")
    _SECRET_STORE_FACTORIES[provider] = factory


def unregister_secret_store(provider: str) -> None:
    _SECRET_STORE_FACTORIES.pop(provider, None)


def secret_store_for_workspace(workspace: Path, config: WorkspaceConfig | None = None) -> SecretStore:
    workspace = workspace.resolve()
    config = config or read_workspace_config(workspace)
    store_config = config.secret_store
    if store_config.provider == "local":
        return LocalFileSecretStore(_local_secret_path(workspace, store_config.path))
    factory = _SECRET_STORE_FACTORIES.get(store_config.provider)
    if factory is None:
        factory = _load_secret_store_entry_point(store_config.provider)
    if factory is None:
        raise RuntimeError(
            f"Secret store provider `{store_config.provider}` is not registered. "
            "Register a SecretStore factory or install an `innie.secret_stores` entry point before running Innie."
        )
    return factory(workspace, store_config)


def _load_secret_store_entry_point(provider: str) -> SecretStoreFactory | None:
    try:
        entry_points = metadata.entry_points(group="innie.secret_stores")
    except TypeError:  # pragma: no cover - Python <3.10 compatibility shape
        entry_points = metadata.entry_points().get("innie.secret_stores", [])
    for entry_point in entry_points:
        if entry_point.name != provider:
            continue
        factory = entry_point.load()
        _SECRET_STORE_FACTORIES[provider] = factory
        return factory
    return None


def _local_secret_path(workspace: Path, configured_path: str | None) -> Path:
    if configured_path is None:
        return secrets_path(workspace)
    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path


def write_workspace_config(
    workspace: Path,
    *,
    app_id: str,
    bot_user_id: str,
    app_name: str,
    watched_user_id: str | None = None,
) -> None:
    path = config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_harness = "codex"
    existing_secret_store = SecretStoreConfig()
    if path.exists():
        existing = read_workspace_config(workspace)
        existing_harness = existing.harness_selected or "codex"
        existing_secret_store = existing.secret_store
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
                f"  watched_user_id: {watched_user_id or 'null'}",
                "harness:",
                f"  selected: {existing_harness}",
                "secrets:",
                f"  provider: {existing_secret_store.provider}",
                f"  path: {existing_secret_store.path or '.innie/secrets.json'}",
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
        secret_store=SecretStoreConfig(
            provider=values.get("secrets.provider") or "local",
            path=values.get("secrets.path"),
        ),
    )


def _null_to_none(value: str) -> str | None:
    return None if value in {"", "null", "None"} else value
