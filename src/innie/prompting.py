from __future__ import annotations

import sys
from typing import TextIO


def mask_secret(value: str) -> str:
    if len(value) <= 5:
        return "*" * len(value)
    return value[:5] + ("*" * (len(value) - 5))


def prompt_masked_secret(
    prompt: str,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> str:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stdout.write(prompt)
    stdout.flush()
    value = stdin.readline().rstrip("\n")
    stdout.write(f"{mask_secret(value)}\n")
    stdout.flush()
    return value
