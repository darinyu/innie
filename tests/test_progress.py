from __future__ import annotations

import unittest

from innie.harness import HarnessEvent, TokenUsage
from innie.progress import SLACK_FINAL_TEXT_LIMIT, SlackProgressRenderer


class SlackProgressRendererTest(unittest.TestCase):
    def test_renders_lifecycle_progress_and_final_output(self) -> None:
        renderer = SlackProgressRenderer()

        self.assertIsNone(renderer.render("task_1", HarnessEvent(type="started")))
        self.assertEqual(
            "Innie is working",
            renderer.render("task_1", HarnessEvent(type="progress", message="running tests")),
        )
        self.assertEqual(
            "ship complete",
            renderer.render("task_1", HarnessEvent(type="output", message="ship complete")),
        )
        self.assertIsNone(renderer.render("task_1", HarnessEvent(type="completed")))

    def test_formats_markdown_for_slack_mrkdwn(self) -> None:
        renderer = SlackProgressRenderer()

        self.assertEqual(
            "*Cost And Access*\nUse `innie run`.",
            renderer.render("task_1", HarnessEvent(type="output", message="**Cost And Access**\nUse `innie run`.")),
        )

    def test_renders_tool_use_as_slack_progress_widget(self) -> None:
        renderer = SlackProgressRenderer()

        self.assertEqual(
            "*Innie is searching the web*\n> pricing model",
            renderer.render(
                "task_1",
                HarnessEvent(
                    type="tool_use",
                    message="pricing model",
                    payload={"tool_name": "web_search"},
                ),
            ),
        )

    def test_renders_tool_use_as_block_kit_progress_widget(self) -> None:
        renderer = SlackProgressRenderer()

        widget = renderer.render_widget(
            "task_1",
            HarnessEvent(
                type="tool_use",
                message="pricing model",
                payload={"tool_name": "web_search"},
            ),
        )

        self.assertIsNotNone(widget)
        self.assertEqual("*Innie is searching the web*\n> pricing model", widget.text)
        self.assertEqual(
            [
                {
                    "type": "plan",
                    "block_id": "innie-progress-plan",
                    "title": "Innie is searching the web",
                    "tasks": [
                        {
                            "task_id": "latest",
                            "title": "pricing model",
                            "status": "in_progress",
                        }
                    ],
                },
            ],
            widget.blocks,
        )

    def test_hides_progress_text_inside_thinking_widget(self) -> None:
        renderer = SlackProgressRenderer()

        widget = renderer.render_widget(
            "task_1",
            HarnessEvent(type="progress", message="I will check recent primary sources first."),
        )

        self.assertIsNotNone(widget)
        self.assertEqual("Innie is working", widget.text)
        self.assertEqual(
            [
                {
                    "type": "plan",
                    "block_id": "innie-progress-plan",
                    "title": "Innie is working",
                    "tasks": [
                        {
                            "task_id": "latest",
                            "title": "Working",
                            "status": "in_progress",
                        }
                    ],
                },
            ],
            widget.blocks,
        )

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

    def test_thinking_progress_is_not_saved_as_final_detail(self) -> None:
        renderer = SlackProgressRenderer()

        self.assertIsNone(renderer.detail_line(HarnessEvent(type="progress", message="checking context")))
        self.assertIsNone(
            renderer.detail_line(HarnessEvent(type="tool_use", message="web search", payload={"tool_name": "web_search"}))
        )
        self.assertIsNone(renderer.detail_line(HarnessEvent(type="usage", usage=TokenUsage(input_tokens=10))))

    def test_renders_final_output_without_progress_details(self) -> None:
        renderer = SlackProgressRenderer()

        widget = renderer.render_final_widget(
            "task_1",
            HarnessEvent(type="output", message="**Final**\nDone."),
            [
                "checking context",
                "reading inputs",
            ],
        )

        self.assertIsNotNone(widget)
        self.assertEqual("*Final*\nDone.", widget.text)
        self.assertEqual(
            [
                {
                    "type": "section",
                    "block_id": "innie-final-output",
                    "expand": True,
                    "text": {"type": "mrkdwn", "text": "*Final*\nDone."},
                },
            ],
            widget.blocks,
        )

    def test_renders_expanded_final_output_with_progress_details(self) -> None:
        renderer = SlackProgressRenderer()

        widget = renderer.render_expanded_final_widget(
            "task_1",
            "Final answer",
            ["first", "second"],
        )

        self.assertIsNotNone(widget)
        self.assertEqual(
            [
                {
                    "type": "context",
                    "block_id": "innie-progress-details-context",
                    "elements": [{"type": "mrkdwn", "text": "Progress details"}],
                },
                {
                    "type": "section",
                    "block_id": "innie-progress-details",
                    "expand": False,
                    "text": {"type": "mrkdwn", "text": "first\nsecond"},
                },
                {
                    "type": "actions",
                    "block_id": "innie-progress-details-actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "innie_hide_progress_details",
                            "text": {"type": "plain_text", "text": "show less"},
                            "value": "task_1",
                        }
                    ],
                },
                {"type": "divider", "block_id": "innie-final-divider"},
                {
                    "type": "section",
                    "block_id": "innie-final-output",
                    "expand": True,
                    "text": {"type": "mrkdwn", "text": "Final answer"},
                },
            ],
            widget.blocks,
        )

    def test_final_output_without_progress_details_is_also_expanded(self) -> None:
        renderer = SlackProgressRenderer()

        widget = renderer.render_final_widget("task_1", HarnessEvent(type="output", message="Final answer"), [])

        self.assertIsNotNone(widget)
        self.assertEqual(
            [
                {
                    "type": "section",
                    "block_id": "innie-final-output",
                    "expand": True,
                    "text": {"type": "mrkdwn", "text": "Final answer"},
                },
            ],
            widget.blocks,
        )

    def test_splits_long_final_output_at_newlines_with_progress_only_on_first_message(self) -> None:
        renderer = SlackProgressRenderer()
        first_line = "a" * (SLACK_FINAL_TEXT_LIMIT - 5)
        second_line = "second message"
        final_text = f"{first_line}\n{second_line}"

        messages = renderer.render_final_messages(
            "task_1",
            HarnessEvent(type="output", message=final_text),
            ["checking context"],
        )

        self.assertEqual(2, len(messages))
        self.assertEqual(first_line, messages[0].text)
        self.assertEqual(second_line, messages[1].text)
        self.assertEqual("section", messages[0].blocks[0]["type"])
        self.assertEqual("section", messages[1].blocks[0]["type"])
        self.assertEqual("innie-final-output", messages[1].blocks[0]["block_id"])
        self.assertNotIn("Progress details", str(messages[0].blocks))
        self.assertNotIn("Progress details", str(messages[1].blocks))


if __name__ == "__main__":
    unittest.main()
