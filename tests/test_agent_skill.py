from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AgentSkillTest(unittest.TestCase):
    def test_innie_skill_has_required_frontmatter_and_core_guidance(self) -> None:
        skill = ROOT / "agent" / "innie" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("---\n"))
        self.assertIn("name: innie", text)
        self.assertIn("description:", text)
        self.assertIn("Slack event arrives", text)
        self.assertIn("secret-store boundary", text)
        self.assertIn("TaskRequest", text)
        self.assertIn("HarnessEvent", text)
        self.assertIn("OpenClaw", text)

    def test_openai_agent_metadata_points_at_innie_skill(self) -> None:
        metadata = ROOT / "agent" / "innie" / "agents" / "openai.yaml"
        text = metadata.read_text(encoding="utf-8")

        self.assertIn("display_name: Innie", text)
        self.assertIn("short_description:", text)
        self.assertIn("default_prompt:", text)
