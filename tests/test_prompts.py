from __future__ import annotations

import unittest

from innie.prompts import load_harness_system_prompt


class HarnessSystemPromptTest(unittest.TestCase):
    def test_prompt_guides_slack_context_and_output_format(self) -> None:
        prompt = load_harness_system_prompt()

        self.assertIn("Slack", prompt)
        self.assertIn("thread", prompt.lower())
        self.assertIn("active harness environment", prompt)
        self.assertIn("on that user's behalf", prompt)
        self.assertIn("Slack-friendly", prompt)


if __name__ == "__main__":
    unittest.main()
