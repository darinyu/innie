from __future__ import annotations

from pathlib import Path
import json
import os
import threading
import tempfile
import unittest
from unittest import mock

from innie.slack_setup import _collect_oauth_code, build_manifest, run_slack_setup


class FakeSlackApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, dict]] = []

    def post_json(self, method: str, token: str, payload: dict) -> dict:
        self.calls.append((method, token, payload))
        if method == "auth.test":
            return {"ok": True, "user_id": "U_BOT"}
        if method == "apps.connections.open":
            return {"ok": True, "url": "wss://example.test/socket"}
        raise AssertionError(f"unexpected json method {method}")

    def post_form(self, method: str, payload: dict[str, str]) -> dict:
        self.calls.append((method, None, payload))
        if method == "oauth.v2.access":
            return {
                "ok": True,
                "access_token": "xoxb-token",
                "app_id": "A123",
                "authed_user": {"id": "U_INSTALLER"},
            }
        raise AssertionError(f"unexpected form method {method}")


class SlackSetupTest(unittest.TestCase):
    def test_manifest_contains_minimal_dm_and_mention_events(self) -> None:
        manifest = build_manifest(
            app_name="innie",
            display_name="Innie",
            redirect_urls=["http://localhost:8765/callback"],
            include_channel_messages=False,
        )

        events = manifest["settings"]["event_subscriptions"]["bot_events"]
        self.assertEqual(["app_mention", "message.im"], events)
        self.assertTrue(manifest["settings"]["socket_mode_enabled"])
        self.assertTrue(manifest["settings"]["interactivity"]["is_enabled"])
        scopes = manifest["oauth_config"]["scopes"]["bot"]
        self.assertIn("chat:write", scopes)
        self.assertIn("files:read", scopes)
        self.assertIn("files:write", scopes)
        self.assertIn("reactions:read", scopes)

    def test_manual_setup_writes_manifest_config_and_restrictive_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            prompts: list[str] = []
            outputs: list[str] = []
            answers = iter(
                [
                    "",
                    "",
                    "1",
                    "",
                    "client-id",
                    "client-secret",
                    "",
                    "http://localhost:8765/callback?code=abc123",
                    "xapp-valid-token",
                ]
            )

            with (
                mock.patch("innie.slack_setup._port_in_use", return_value=False),
                mock.patch("innie.slack_setup.http.server.HTTPServer", side_effect=OSError("busy")),
            ):
                result = run_slack_setup(
                    workspace,
                    api=FakeSlackApi(),
                    prompt=lambda text: prompts.append(text) or next(answers),
                    prompt_secret=lambda text: prompts.append(text) or next(answers),
                    output=outputs.append,
                    oauth_timeout_seconds=0,
                )

            self.assertTrue(result.ok, result.messages)
            self.assertIn("Next:", "\n".join(result.messages))
            self.assertIn("innie run --once --harness codex", "\n".join(result.messages))
            self.assertIn("innie run --once --harness claude", "\n".join(result.messages))
            manifest = json.loads((workspace / ".innie" / "slack-manifest.json").read_text())
            self.assertEqual("innie", manifest["display_information"]["name"])
            config = (workspace / ".innie" / "config.yaml").read_text()
            self.assertIn("configured: true", config)
            self.assertIn("app_id: A123", config)
            secrets_path = workspace / ".innie" / "secrets.json"
            secrets = json.loads(secrets_path.read_text())
            self.assertEqual("xoxb-token", secrets["slack_bot_token"])
            self.assertEqual("xapp-valid-token", secrets["slack_app_token"])
            mode = os.stat(secrets_path).st_mode & 0o777
            self.assertEqual(0o600, mode)
            prompt_text = "\n".join(prompts)
            self.assertIn("Client ID", prompt_text)
            self.assertIn("Client Secret", prompt_text)
            self.assertIn("App ID", prompt_text)
            self.assertNotIn("OAuth callback mode", prompt_text)
            output_text = "\n".join(outputs)
            self.assertIn("Step 1/6", output_text)
            self.assertIn("modifiable", output_text)
            self.assertIn("Mode 1", output_text)
            self.assertIn("Mode 2", output_text)
            self.assertIn("https://api.slack.com/apps", output_text)
            self.assertIn('"display_information"', output_text)
            self.assertIn("BEGIN SLACK APP MANIFEST", output_text)
            self.assertIn("END SLACK APP MANIFEST", output_text)
            self.assertIn("\033[2J\033[H", output_text)

    def test_user_mention_mode_adds_channel_message_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            answers = iter(
                [
                    "support-innie",
                    "Support Innie",
                    "2",
                    "",
                    "client-id",
                    "client-secret",
                    "A123",
                    "abc123",
                    "xapp-valid-token",
                ]
            )

            with (
                mock.patch("innie.slack_setup._port_in_use", return_value=False),
                mock.patch("innie.slack_setup.http.server.HTTPServer", side_effect=OSError("busy")),
            ):
                result = run_slack_setup(
                    workspace,
                    api=FakeSlackApi(),
                    prompt=lambda _text: next(answers),
                    prompt_secret=lambda _text: next(answers),
                    output=lambda _text: None,
                    oauth_timeout_seconds=0,
                )

            self.assertTrue(result.ok, result.messages)
            manifest = json.loads((workspace / ".innie" / "slack-manifest.json").read_text())
            events = manifest["settings"]["event_subscriptions"]["bot_events"]
            self.assertIn("message.channels", events)
            self.assertIn("message.groups", events)
            config = (workspace / ".innie" / "config.yaml").read_text()
            self.assertIn("trigger_mode: user_mention", config)
            self.assertIn("watched_user_id: U_INSTALLER", config)

    def test_xapp_token_prompt_retries_blank_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            answers = iter(
                [
                    "",
                    "",
                    "1",
                    "",
                    "client-id",
                    "client-secret",
                    "",
                    "abc123",
                    "",
                    "xapp-valid-token",
                ]
            )
            outputs: list[str] = []

            with (
                mock.patch("innie.slack_setup._port_in_use", return_value=False),
                mock.patch("innie.slack_setup.http.server.HTTPServer", side_effect=OSError("busy")),
            ):
                result = run_slack_setup(
                    workspace,
                    api=FakeSlackApi(),
                    prompt=lambda _text: next(answers),
                    prompt_secret=lambda _text: next(answers),
                    output=outputs.append,
                    oauth_timeout_seconds=0,
                )

            self.assertTrue(result.ok, result.messages)
            self.assertIn("No token entered", "\n".join(outputs))

    def test_oauth_collector_continues_automatically_when_callback_arrives(self) -> None:
        server = FakeOAuthServer()

        with mock.patch("innie.slack_setup.http.server.HTTPServer", return_value=server):
            code = _collect_oauth_code(
                "http://localhost:8765/callback",
                "https://slack.com/oauth",
                prompt=lambda _text: self.fail("prompt should not block before callback"),
                output=lambda _text: None,
                messages=[],
                timeout_seconds=2,
                on_server_started=lambda: "auto-code",
            )

        self.assertEqual("auto-code", code)


class FakeOAuthServer:
    def __init__(self) -> None:
        self._code: str | None = None
        self._shutdown = threading.Event()

    def serve_forever(self) -> None:
        self._shutdown.wait(timeout=2)

    def shutdown(self) -> None:
        self._shutdown.set()



if __name__ == "__main__":
    unittest.main()
