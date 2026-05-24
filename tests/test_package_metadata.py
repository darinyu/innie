from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


class PackageMetadataTest(unittest.TestCase):
    def test_pyproject_metadata_is_ready_for_pypi_release(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('requires = ["hatchling>=1.27"]', pyproject)
        self.assertIn('name = "innie"', pyproject)
        self.assertIn(f'version = "{_package_version(project_root)}"', pyproject)
        self.assertIn('version = "0.1.0"', pyproject)
        self.assertIn('license = "Apache-2.0"', pyproject)
        self.assertIn('license-files = ["LICENSE"]', pyproject)
        self.assertIn('requires-python = ">=3.10"', pyproject)
        self.assertIn('keywords = ["agents", "ai", "automation", "codex", "slack"]', pyproject)
        self.assertIn('innie = "innie.cli:main"', pyproject)
        self.assertIn('Homepage = "https://github.com/darinyu/innie"', pyproject)
        self.assertIn('Source = "https://github.com/darinyu/innie"', pyproject)
        self.assertIn('Issues = "https://github.com/darinyu/innie/issues"', pyproject)
        self.assertIn('terminal = ["rich>=13"]', pyproject)
        self.assertIn('"Programming Language :: Python :: 3.10"', pyproject)
        self.assertNotIn("Private :: Do Not Upload", pyproject)


def _package_version(project_root: Path) -> str:
    spec = importlib.util.spec_from_file_location("innie", project_root / "src" / "innie" / "__init__.py")
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load innie package metadata")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.__version__


if __name__ == "__main__":
    unittest.main()
