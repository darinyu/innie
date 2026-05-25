from __future__ import annotations

import unittest

from innie.harness import HarnessEvent
from innie.runtime_terminal import format_terminal_event


class RuntimeTerminalTests(unittest.TestCase):
    def test_format_terminal_event_includes_tool_name_and_compact_preview(self) -> None:
        event = HarnessEvent(
            type="tool_use",
            message="web\n\nsearch   query",
            payload={"tool_name": "web_search"},
        )

        line = format_terminal_event("sess_1", "task_1", event)

        self.assertEqual("session sess_1 task task_1 tool_use web_search: web search query", line)

    def test_format_terminal_event_truncates_long_messages(self) -> None:
        event = HarnessEvent(type="output", message="x" * 200)

        line = format_terminal_event("sess_1", "task_1", event)

        self.assertEqual(f"session sess_1 task task_1 output: {'x' * 177}...", line)


if __name__ == "__main__":
    unittest.main()
