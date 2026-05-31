from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest

from innie.config import (
    SecretStoreConfig,
    load_secrets,
    read_workspace_config,
    register_secret_store,
    secret_store_for_workspace,
    unregister_secret_store,
    write_secrets,
    write_workspace_config,
)


class MemorySecretStore:
    description = "memory://innie-test"

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    def load(self) -> dict[str, str]:
        return dict(self.secrets)

    def write(self, secrets: dict[str, str]) -> None:
        self.secrets = dict(secrets)


class SecretStoreTest(unittest.TestCase):
    def tearDown(self) -> None:
        unregister_secret_store("memory-test")

    def test_default_secret_store_is_local_file_with_restrictive_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            write_secrets(workspace, {"slack_bot_token": "xoxb-test"})

            path = workspace / ".innie" / "secrets.json"
            self.assertEqual({"slack_bot_token": "xoxb-test"}, load_secrets(workspace))
            self.assertTrue(path.exists())
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            self.assertIn("secrets.json (0600)", secret_store_for_workspace(workspace).description)

    def test_local_secret_store_can_use_configured_path_outside_innie_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path = workspace / ".innie" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "workspace_version: 1",
                        "secrets:",
                        "  provider: local",
                        "  path: ../secrets/innie.json",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            write_secrets(workspace, {"slack_app_token": "xapp-test"})

            self.assertEqual({"slack_app_token": "xapp-test"}, load_secrets(workspace))
            self.assertTrue((workspace.parent / "secrets" / "innie.json").exists())

    def test_workspace_config_preserves_custom_secret_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path = workspace / ".innie" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "workspace_version: 1",
                        "harness:",
                        "  selected: claude",
                        "secrets:",
                        "  provider: vault",
                        "  path: team/innie/slack",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            write_workspace_config(workspace, app_id="A1", bot_user_id="U_BOT", app_name="innie", watched_user_id="U_USER")

            config = read_workspace_config(workspace)
            self.assertEqual("claude", config.harness_selected)
            self.assertEqual(SecretStoreConfig(provider="vault", path="team/innie/slack"), config.secret_store)
            text = config_path.read_text(encoding="utf-8")
            self.assertIn("provider: vault", text)
            self.assertIn("path: team/innie/slack", text)

    def test_registered_secret_store_can_back_load_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            store = MemorySecretStore()
            register_secret_store("memory-test", lambda _workspace, _config: store)
            config_path = workspace / ".innie" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "workspace_version: 1",
                        "secrets:",
                        "  provider: memory-test",
                        "  path: team/innie/slack",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            write_secrets(workspace, {"slack_bot_token": "xoxb-remote"})

            self.assertEqual({"slack_bot_token": "xoxb-remote"}, load_secrets(workspace))
            self.assertEqual("memory://innie-test", secret_store_for_workspace(workspace).description)

    def test_unknown_secret_store_provider_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_path = workspace / ".innie" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("workspace_version: 1\nsecrets:\n  provider: missing-store\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "missing-store"):
                load_secrets(workspace)
