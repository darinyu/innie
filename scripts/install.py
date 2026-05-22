from __future__ import annotations

import argparse
from pathlib import Path
import os
import stat
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the innie command for this checkout.")
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=Path.home() / ".local" / "bin",
        help="Directory where the innie command should be written",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    target = args.bin_dir / "innie"
    args.bin_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(_launcher(src_dir), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"Installed innie command: {target}")
    if str(args.bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add this directory to PATH if needed: {args.bin_dir}")
    print("Start with: innie init")
    return 0


def _launcher(src_dir: Path) -> str:
    return f"""#!/usr/bin/env python3
from pathlib import Path
import runpy
import sys

sys.path.insert(0, {str(src_dir)!r})
sys.argv[0] = "innie"
runpy.run_module("innie", run_name="__main__")
"""


if __name__ == "__main__":
    raise SystemExit(main())
