from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.install import main


class InstallScriptTest(unittest.TestCase):
    def test_install_script_creates_working_innie_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            out = StringIO()
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


if __name__ == "__main__":
    unittest.main()
