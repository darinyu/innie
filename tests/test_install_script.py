from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.install import main


class InstallScriptTest(unittest.TestCase):
    def test_install_script_creates_working_innie_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            out = StringIO()
            with mock.patch("scripts.install.importlib.util.find_spec", return_value=object()):
                with redirect_stdout(out):
                    self.assertEqual(0, main(["--bin-dir", str(bin_dir)]))

            command = bin_dir / "innie"
            self.assertTrue(command.exists())
            result = subprocess.run(
                [str(command), "--help"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )

            self.assertIn("Installed innie command", out.getvalue())
            self.assertIn("init", result.stdout)

    def test_install_script_defaults_to_installing_rich(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scripts.install.importlib.util.find_spec", side_effect=_missing_only("rich")):
                with mock.patch("builtins.input", return_value=""):
                    with mock.patch("scripts.install.subprocess.run") as run:
                        out = StringIO()
                        with redirect_stdout(out):
                            self.assertEqual(0, main(["--bin-dir", tmp]))

            run.assert_called_once()
            self.assertIn("Installed rich", out.getvalue())

    def test_install_script_can_skip_rich(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scripts.install.importlib.util.find_spec", side_effect=_missing_only("rich")):
                with mock.patch("builtins.input", return_value="n"):
                    with mock.patch("scripts.install.subprocess.run") as run:
                        out = StringIO()
                        with redirect_stdout(out):
                            self.assertEqual(0, main(["--bin-dir", tmp]))

            run.assert_not_called()
            self.assertIn("Skipping rich install", out.getvalue())

    def test_install_script_can_install_rich_with_yes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scripts.install.importlib.util.find_spec", side_effect=_missing_only("rich")):
                with mock.patch("scripts.install.subprocess.run") as run:
                    with redirect_stdout(StringIO()):
                        self.assertEqual(0, main(["--bin-dir", tmp, "--yes"]))

            run.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[-4:], ["pip", "install", "--user", "rich"])

    def test_install_script_installs_missing_runtime_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("scripts.install.importlib.util.find_spec", side_effect=_missing_only("slack_sdk", "aiohttp")):
                with mock.patch("scripts.install.subprocess.run") as run:
                    out = StringIO()
                    with redirect_stdout(out):
                        self.assertEqual(0, main(["--bin-dir", tmp]))

        command = run.call_args.args[0]
        self.assertEqual(command[-5:], ["pip", "install", "--user", "slack-sdk", "aiohttp"])
        self.assertIn("Installed runtime dependencies", out.getvalue())


def _missing_only(*missing: str):
    missing_set = set(missing)

    def find_spec(name: str):
        return None if name in missing_set else object()

    return find_spec


if __name__ == "__main__":
    unittest.main()
