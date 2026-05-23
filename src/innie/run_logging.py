from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import innie_dir


OutputFn = Callable[[str], None]


class RunLogger:
    def __init__(
        self,
        workspace: Path,
        *,
        output: OutputFn,
        max_bytes: int = 1_000_000,
        backup_count: int = 5,
    ) -> None:
        self.path = innie_dir(workspace) / "logs" / "innie.log"
        self._output = output
        self._max_bytes = max_bytes
        self._backup_count = backup_count

    def emit(self, message: str) -> None:
        line = f"{self._timestamp()} {message}"
        self._output(line)
        self._write(line)

    def _write(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed(len(line.encode("utf-8")) + 1)
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{line}\n")

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if self._max_bytes <= 0 or not self.path.exists():
            return
        if self.path.stat().st_size + incoming_bytes <= self._max_bytes:
            return
        for index in range(self._backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists():
                if index + 1 > self._backup_count:
                    source.unlink()
                else:
                    source.replace(target)
        first_backup = self.path.with_name(f"{self.path.name}.1")
        self.path.replace(first_backup)
