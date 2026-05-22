from __future__ import annotations

from io import StringIO
import json
import textwrap
from typing import Callable


OutputFn = Callable[[str], None]


class WizardUI:
    def __init__(self, output: OutputFn) -> None:
        self._output = output

    def step(self, label: str, title: str, body: str) -> None:
        rich_rendered = _rich_panel(label, title, body)
        if rich_rendered:
            self._output(rich_rendered)
            return
        wrapped = "\n".join(textwrap.fill(line, width=88) for line in body.splitlines())
        self._output(f"\n{label} - {title}\n{wrapped}\n")

    def manifest(self, manifest: dict) -> str:
        manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
        rich_rendered = _rich_manifest(manifest_json)
        if rich_rendered:
            self._output(rich_rendered)
        else:
            self._output(f"\nPaste this manifest:\n\n{manifest_json}\n")
        return manifest_json

    def info(self, message: str) -> None:
        self._output(message)

    def clear(self) -> None:
        self._output("\033[2J\033[H")


def _rich_panel(label: str, title: str, body: str) -> str | None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
    except ImportError:
        return None

    file = StringIO()
    console = Console(file=file, force_terminal=True, color_system="auto", width=88)
    text = Text(body)
    console.print(Panel(text, title=f"[bold cyan]{label}[/] [bold]{title}[/]", border_style="cyan"))
    return file.getvalue().rstrip()


def _rich_manifest(manifest_json: str) -> str | None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.syntax import Syntax
    except ImportError:
        return None

    file = StringIO()
    console = Console(file=file, force_terminal=True, color_system="auto", width=96)
    syntax = Syntax(manifest_json, "json", theme="ansi_dark", word_wrap=True)
    console.print(Panel(syntax, title="[bold cyan]Slack app manifest[/]", border_style="cyan"))
    return file.getvalue().rstrip()
