from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import os
import stat
import subprocess
import sys


RUNTIME_DEPENDENCIES = {
    "slack_sdk": "slack-sdk",
    "aiohttp": "aiohttp",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the innie command for this checkout.")
    parser.add_argument(
        "--bin-dir",
        type=Path,
        default=None,
        help="Directory where the innie command should be written",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Approve installing optional UX dependencies such as rich",
    )
    args = parser.parse_args(argv)

    _ensure_runtime_dependencies()
    _ensure_rich(assume_yes=args.yes)

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    bin_dir = args.bin_dir or _default_bin_dir()
    target = bin_dir / "innie"
    already_installed = target.exists()
    bin_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(_launcher(src_dir), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    action = "Updated" if already_installed else "Installed"
    print(f"{action} innie command: {target}")
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add this directory to PATH if needed: {bin_dir}")
    print("Start with: innie init")
    print("Safe to rerun: dependencies are checked again and the launcher is refreshed.")
    return 0


def _default_bin_dir() -> Path:
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    for env_var in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        env_root = os.environ.get(env_var)
        if not env_root:
            continue
        env_bin = Path(env_root) / ("Scripts" if os.name == "nt" else "bin")
        if str(env_bin) in path_entries:
            return env_bin

    return Path.home() / ".local" / "bin"


def _ensure_runtime_dependencies() -> None:
    missing = [
        package
        for module_name, package in RUNTIME_DEPENDENCIES.items()
        if importlib.util.find_spec(module_name) is None
    ]
    if not missing:
        print("Runtime dependencies: available")
        return

    print(f"Runtime dependencies: missing {', '.join(missing)}")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", *missing],
        check=True,
    )
    print("Installed runtime dependencies")


def _ensure_rich(*, assume_yes: bool) -> None:
    if importlib.util.find_spec("rich") is not None:
        print("Rich terminal UI: available")
        return

    print("Rich terminal UI: not installed")
    print("Rich gives Innie colored, wrapped setup screens. Innie can run without it, but setup is harder to read.")
    if not assume_yes:
        answer = input("Install rich now with Python pip? [Y/n] ").strip().lower()
        if answer in {"n", "no"}:
            print("Skipping rich install. Innie will use plain terminal output.")
            return

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "rich"],
        check=True,
    )
    print("Installed rich")


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
