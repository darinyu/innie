from __future__ import annotations

from pathlib import Path
from typing import Any


class LocalFileLogSource:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def tail(self, *, after_offset: int = 0, limit_bytes: int = 65536) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "exists": False,
                "path": str(self.path),
                "lines": [],
                "next": {"logOffset": 0},
            }

        size = self.path.stat().st_size
        start = min(max(after_offset, 0), size)
        with self.path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(limit_bytes)
            next_offset = handle.tell()

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return {
            "exists": True,
            "path": str(self.path),
            "lines": lines,
            "next": {"logOffset": next_offset},
        }
