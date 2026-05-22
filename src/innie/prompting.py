from __future__ import annotations

import sys
import termios
from typing import Callable, TextIO


def mask_secret(value: str) -> str:
    if len(value) <= 5:
        return "*" * len(value)
    return value[:5] + ("*" * (len(value) - 5))


def prompt_masked_secret(
    prompt: str,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    set_echo: Callable[[bool], None] | None = None,
) -> str:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stdout.write(prompt)
    stdout.flush()
    echo_control = set_echo or _terminal_echo_control(stdin)
    echo_control(False)
    try:
        value = stdin.readline().rstrip("\n")
    finally:
        echo_control(True)
    stdout.write(f"\n{mask_secret(value)}\n")
    stdout.flush()
    return value


def _terminal_echo_control(stdin: TextIO) -> Callable[[bool], None]:
    if not hasattr(stdin, "isatty") or not stdin.isatty():
        return lambda _enabled: None

    fd = stdin.fileno()
    original = termios.tcgetattr(fd)

    def set_echo(enabled: bool) -> None:
        attrs = termios.tcgetattr(fd)
        if enabled:
            attrs[3] = original[3]
        else:
            attrs[3] = attrs[3] & ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)

    return set_echo
