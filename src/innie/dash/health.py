from __future__ import annotations

from pathlib import Path
from typing import Any


def health_summary(workspace: Path, db_path: Path, log_path: Path) -> dict[str, Any]:
    return {
        "workspace": str(workspace),
        "store": _file_summary(db_path),
        "log": _file_summary(log_path),
    }


def _file_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "sizeBytes": 0, "modifiedAt": None}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "sizeBytes": stat.st_size,
        "modifiedAt": stat.st_mtime,
    }
