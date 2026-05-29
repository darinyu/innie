from __future__ import annotations

from pathlib import Path
import json
import os
import threading
import tempfile
import unittest
from unittest import mock

from innie.slack_setup import (
    _collect_oauth_code,
    _oauth_lines,
    _plain_text,
    build_manifest,
    run_slack_setup,
)


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
        )

        events = manifest["settings"]["event_subscriptions"]["bot_events"]
        self.assertEqual(["message.channels", "message.groups"], events)
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
            self.assertIn("Next         innie run --once --harness codex", "\n".join(result.messages))
            self.assertIn("innie run --once --harness codex", "\n".join(result.messages))
            self.assertIn("innie run --once --harness claude", "\n".join(result.messages))
            result_text = "\n".join(result.messages)
            self.assertIn("Slack setup complete.", result_text)
            self.assertIn("Status       ok", result_text)
            self.assertIn("App          innie (A123)", result_text)
            self.assertIn("Bot user     U_BOT", result_text)
            self.assertIn("Trigger      watched user mention", result_text)
            self.assertIn("Tokens       saved to .innie/secrets.json (0600)", result_text)
            self.assertIn("Dashboard    innie dash", result_text)
            self.assertIn("=" * 88, result_text)
            self.assertIn("|___|_| |_|_| |_|_|\\___|", result_text)
            self.assertNotIn("Inspect", result_text)
            self.assertNotIn("Logs", result_text)
            self.assertNotIn("Slack setup started.", result_text)
            self.assertNotIn("Step 3/6 - Wrote Slack manifest", result_text)
            self.assertNotIn("Open Slack OAuth URL", result_text)
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
            self.assertIn("Configure the watched user", output_text)
            self.assertIn("tags the installing Slack user", output_text)
            self.assertIn("https://api.slack.com/apps", output_text)
            self.assertIn('"display_information"', output_text)
            self.assertIn("BEGIN SLACK APP MANIFEST", output_text)
            self.assertIn("END SLACK APP MANIFEST", output_text)
            self.assertIn("\033[2J\033[H", output_text)

    def test_setup_saves_watched_user_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            answers = iter(
                [
                    "support-innie",
                    "Support Innie",
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
            self.assertIn("watched_user_id: U_INSTALLER", config)

    def test_xapp_token_prompt_retries_blank_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            answers = iter(
                [
                    "",
                    "",
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

    def test_oauth_collector_prints_clear_local_and_remote_actions(self) -> None:
        server = FakeOAuthServer()
        outputs: list[str] = []
        url = "https://slack.com/oauth/v2/authorize?client_id=123"

        with mock.patch("innie.slack_setup.http.server.HTTPServer", return_value=server):
            code = _collect_oauth_code(
                "http://localhost:8765/callback",
                url,
                prompt=lambda _text: self.fail("prompt should not block before callback"),
                output=outputs.append,
                messages=[],
                timeout_seconds=2,
                on_server_started=lambda: "auto-code",
            )

        self.assertEqual("auto-code", code)
        output_text = "\n".join(outputs)
        self.assertIn(url, output_text)
        self.assertIn("LOCAL BROWSER:", output_text)
        self.assertIn("REMOTE BROWSER:", output_text)
        self.assertIn("Innie continues automatically", output_text)
        self.assertNotIn("wait for the paste fallback below", output_text)

    def test_oauth_instructions_put_url_second_and_label_local_remote_paths(self) -> None:
        url = "https://slack.com/oauth/v2/authorize?client_id=123"

        text = _plain_text(_oauth_lines(url))

        lines = text.splitlines()
        self.assertEqual("Step 5/6 - Install the app with OAuth.", lines[0])
        self.assertEqual(url, lines[1])
        self.assertIn("LOCAL BROWSER:", text)
        self.assertIn("REMOTE BROWSER:", text)
        self.assertIn("Innie continues automatically", text)
        self.assertIn("copy the browser's final callback URL and paste it when prompted", text)
        self.assertNotIn("wait for the paste fallback below", text)


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
