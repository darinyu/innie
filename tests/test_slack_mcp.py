from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.config import write_secrets
from innie.slack_mcp import SlackMcpServer, _workspace_token


class FakeSlackApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.form_calls: list[tuple[str, dict]] = []
        self.reply_pages: list[dict] | None = None
        self.next_result: dict | None = None

    def api_call(self, method: str, payload: dict) -> dict:
        self.calls.append((method, payload))
        if self.next_result is not None:
            return self.next_result
        if method == "conversations.replies":
            if self.reply_pages is not None:
                page_index = len([call for call in self.form_calls if call[0] == "conversations.replies"]) - 1
                return self.reply_pages[page_index]
            return {
                "ok": True,
                "messages": [
                    {"user": "U1", "ts": "100.1", "text": "root question"},
                    {"user": "U2", "ts": "100.2", "text": "prior reply"},
                    {"user": "U3", "ts": "100.3", "text": "tagged question"},
                    {"user": "U4", "ts": "100.4", "text": "follow up"},
                ],
            }
        if method == "conversations.history":
            return {
                "ok": True,
                "messages": [
                    {"user": "U1", "ts": "200.1", "text": "deploy note"},
                    {"user": "U2", "ts": "200.2", "text": "unrelated"},
                ],
            }
        raise AssertionError(method)

    def api_form_call(self, method: str, payload: dict) -> dict:
        self.form_calls.append((method, payload))
        if self.next_result is not None:
            return self.next_result
        return self.api_call(method, payload)


class SlackMcpServerTest(unittest.TestCase):
    def test_lists_read_only_slack_tools(self) -> None:
        server = SlackMcpServer(FakeSlackApi())

        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(["slack_get_thread", "slack_get_message", "slack_get_permalink", "slack_get_channel_history"], names)

    def test_get_thread_can_mark_current_message(self) -> None:
        client = FakeSlackApi()
        server = SlackMcpServer(client)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "slack_get_thread",
                    "arguments": {"channel": "C1", "thread_ts": "100.1", "current_ts": "100.3"},
                },
            }
        )

        self.assertEqual(("conversations.replies", {"channel": "C1", "ts": "100.1", "limit": 100}), client.form_calls[0])
        text = response["result"]["content"][0]["text"]
        self.assertIn("Slack thread C1 100.1", text)
        self.assertIn("root question", text)
        self.assertIn("prior reply", text)
        self.assertIn("100.3 [current]: tagged question", text)
        self.assertNotIn("follow up", text)

    def test_get_thread_paginates_until_current_message(self) -> None:
        client = FakeSlackApi()
        client.reply_pages = [
            {
                "ok": True,
                "messages": [
                    {"user": "U1", "ts": "100.1", "text": "root question"},
                    {"user": "U2", "ts": "100.2", "text": "old reply"},
                ],
                "response_metadata": {"next_cursor": "cursor-2"},
            },
            {
                "ok": True,
                "messages": [
                    {"user": "U3", "ts": "100.3", "text": "previous reply"},
                    {"user": "U4", "ts": "100.4", "text": "current reply"},
                ],
            },
        ]
        server = SlackMcpServer(client)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "slack_get_thread",
                    "arguments": {"channel": "C1", "thread_ts": "100.1", "current_ts": "100.4"},
                },
            }
        )

        self.assertEqual(
            [
                ("conversations.replies", {"channel": "C1", "ts": "100.1", "limit": 100}),
                ("conversations.replies", {"channel": "C1", "ts": "100.1", "limit": 100, "cursor": "cursor-2"}),
            ],
            client.form_calls,
        )
        text = response["result"]["content"][0]["text"]
        self.assertIn("root question", text)
        self.assertIn("old reply", text)
        self.assertIn("previous reply", text)
        self.assertIn("100.4 [current]: current reply", text)

    def test_get_message_fetches_channel_message_by_ts(self) -> None:
        client = FakeSlackApi()
        server = SlackMcpServer(client)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "slack_get_message", "arguments": {"channel": "C1", "ts": "200.1"}},
            }
        )

        self.assertEqual(("conversations.history", {"channel": "C1", "latest": "200.1", "inclusive": True, "limit": 1}), client.calls[0])
        text = response["result"]["content"][0]["text"]
        self.assertIn("Slack message C1 200.1", text)
        self.assertIn("deploy note", text)

    def test_get_permalink_returns_coordinates_and_context(self) -> None:
        server = SlackMcpServer(FakeSlackApi())

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "slack_get_permalink",
                    "arguments": {
                        "url": "https://example.slack.com/archives/C1/p0000000100300000?thread_ts=100.1&cid=C1"
                    },
                },
            }
        )

        text = response["result"]["content"][0]["text"]
        self.assertIn("Slack permalink", text)
        self.assertIn("channel: C1", text)
        self.assertIn("message_ts: 100.3", text)
        self.assertIn("thread_ts: 100.1", text)
        self.assertIn('slack_get_thread(channel="C1", thread_ts="100.1", current_ts="100.3")', text)
        self.assertIn("100.3 [current]: tagged question", text)

    def test_get_permalink_to_thread_root_returns_full_thread(self) -> None:
        server = SlackMcpServer(FakeSlackApi())

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "slack_get_permalink",
                    "arguments": {"url": "https://example.slack.com/archives/C1/p0000000100100000"},
                },
            }
        )

        text = response["result"]["content"][0]["text"]
        self.assertIn("message_ts: 100.1", text)
        self.assertIn("thread_ts: 100.1", text)
        self.assertIn('slack_get_thread(channel="C1", thread_ts="100.1")', text)
        self.assertIn("100.1 [current]: root question", text)
        self.assertIn("prior reply", text)
        self.assertIn("tagged question", text)
        self.assertIn("follow up", text)

    def test_get_thread_current_root_keeps_current_ts_semantics(self) -> None:
        server = SlackMcpServer(FakeSlackApi())

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "slack_get_thread",
                    "arguments": {"channel": "C1", "thread_ts": "100.1", "current_ts": "100.1"},
                },
            }
        )

        text = response["result"]["content"][0]["text"]
        self.assertIn("100.1 [current]: root question", text)
        self.assertNotIn("prior reply", text)
        self.assertNotIn("tagged question", text)
        self.assertNotIn("follow up", text)

    def test_get_thread_calls_conversations_replies(self) -> None:
        client = FakeSlackApi()
        server = SlackMcpServer(client)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "slack_get_thread", "arguments": {"channel": "C1", "thread_ts": "100.1", "limit": 2}},
            }
        )

        self.assertEqual(("conversations.replies", {"channel": "C1", "ts": "100.1", "limit": 2}), client.form_calls[0])
        text = response["result"]["content"][0]["text"]
        self.assertIn("root question", text)
        self.assertIn("prior reply", text)
        self.assertIn("tagged question", text)
        self.assertIn("follow up", text)

    def test_slack_api_errors_are_reported(self) -> None:
        client = FakeSlackApi()
        client.next_result = {
            "ok": False,
            "error": "invalid_arguments",
            "response_metadata": {"messages": ["[ERROR] missing required field: channel"]},
        }
        server = SlackMcpServer(client)

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "slack_get_thread", "arguments": {"channel": "C1", "thread_ts": "100.1"}},
            }
        )

        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("conversations.replies failed: invalid_arguments", text)
        self.assertIn("missing required field: channel", text)

    def test_workspace_token_reads_slack_secret(self) -> None:
        self.assertIsNone(_workspace_token(None))

        with tempfile.TemporaryDirectory() as tmp:
            write_secrets(Path(tmp), {"slack_bot_token": "xoxb-workspace-token"})

            self.assertEqual("xoxb-workspace-token", _workspace_token(tmp))

if __name__ == "__main__":
    unittest.main()
