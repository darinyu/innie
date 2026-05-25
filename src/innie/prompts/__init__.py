from __future__ import annotations

from functools import cache
from importlib import resources


_HARNESS_SYSTEM_PROMPT = "harness_system_prompt.md"


@cache
def load_harness_system_prompt() -> str:
    return resources.files(__package__).joinpath(_HARNESS_SYSTEM_PROMPT).read_text(encoding="utf-8")
