from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashPackagingTest(unittest.TestCase):
    def test_pyproject_declares_dash_static_assets_for_distribution(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("[tool.hatch.build.targets.wheel]", pyproject)
        self.assertIn('packages = ["src/innie"]', pyproject)
        self.assertIn("src/innie/dash/web", pyproject)

    def test_release_workflow_builds_artifacts_on_github_release(self) -> None:
        workflow = ROOT / ".github" / "workflows" / "publish.yml"

        source = workflow.read_text(encoding="utf-8")

        self.assertIn("release:", source)
        self.assertIn("types: [published]", source)
        self.assertIn("python -m build", source)
        self.assertIn("actions/upload-artifact", source)


if __name__ == "__main__":
    unittest.main()
