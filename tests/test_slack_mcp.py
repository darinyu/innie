from __future__ import annotations

import unittest

from innie.slack_mcp import SlackMcpServer


class FakeSlackApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def api_call(self, method: str, payload: dict) -> dict:
        self.calls.append((method, payload))
        if method == "conversations.replies":
            return {
                "ok": True,
                "messages": [
                    {"user": "U1", "ts": "100.1", "text": "root question"},
                    {"user": "U2", "ts": "100.2", "text": "follow up"},
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


class SlackMcpServerTest(unittest.TestCase):
    def test_lists_read_only_slack_tools(self) -> None:
        server = SlackMcpServer(FakeSlackApi())

        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(["slack_get_thread", "slack_get_channel_history", "slack_find_messages"], names)

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

        self.assertEqual(("conversations.replies", {"channel": "C1", "ts": "100.1", "limit": 2}), client.calls[0])
        text = response["result"]["content"][0]["text"]
        self.assertIn("root question", text)
        self.assertIn("follow up", text)

    def test_find_messages_filters_recent_history_by_query(self) -> None:
        server = SlackMcpServer(FakeSlackApi())

        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "slack_find_messages", "arguments": {"channel": "C1", "query": "deploy"}},
            }
        )

        text = response["result"]["content"][0]["text"]
        self.assertIn("deploy note", text)
        self.assertNotIn("unrelated", text)


if __name__ == "__main__":
    unittest.main()
