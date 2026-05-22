from __future__ import annotations

import unittest

from innie.harness import HarnessEvent, TokenUsage
from innie.progress import SlackProgressRenderer


class SlackProgressRendererTest(unittest.TestCase):
    def test_renders_lifecycle_progress_and_final_output(self) -> None:
        renderer = SlackProgressRenderer()

        self.assertEqual("Started task task_1.", renderer.render("task_1", HarnessEvent(type="started")))
        self.assertEqual(
            "Progress: running tests",
            renderer.render("task_1", HarnessEvent(type="progress", message="running tests")),
        )
        self.assertEqual(
            "Done:\nship complete",
            renderer.render("task_1", HarnessEvent(type="output", message="ship complete")),
        )
        self.assertEqual("Task task_1 completed.", renderer.render("task_1", HarnessEvent(type="completed")))

    def test_renders_usage_without_private_reasoning(self) -> None:
        renderer = SlackProgressRenderer()

        text = renderer.render(
            "task_1",
            HarnessEvent(
                type="usage",
                usage=TokenUsage(input_tokens=10, output_tokens=4, cache_read_tokens=5),
                payload={"chain_of_thought": "never show this"},
            ),
        )

        self.assertEqual("Usage: 10 input, 4 output, 50% cache hit.", text)
        self.assertNotIn("never show", text)

    def test_skips_tool_payload_without_message(self) -> None:
        renderer = SlackProgressRenderer()

        self.assertIsNone(renderer.render("task_1", HarnessEvent(type="tool_result", payload={"private": "raw"})))


if __name__ == "__main__":
    unittest.main()
