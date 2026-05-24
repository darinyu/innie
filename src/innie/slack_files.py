from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol

from .slack_events import SlackTrigger


@dataclass(frozen=True)
class SlackFileRecord:
    id: int
    session_id: str
    slack_event_id: str
    slack_file_id: str
    name: str
    mimetype: str | None
    filetype: str | None
    url_private_download: str | None
    local_path: str | None
    byte_count: int
    status: str
    error: str | None


class SlackFileClient(Protocol):
    def download_file(self, url: str, destination: Path) -> int:
        ...


def stage_slack_files_for_trigger(
    db: sqlite3.Connection,
    *,
    workspace: Path,
    session_id: str,
    trigger: SlackTrigger,
    file_client: SlackFileClient,
) -> list[SlackFileRecord]:
    files = _extract_files(trigger.payload)
    if not files:
        return []

    event_dir = workspace.resolve() / ".innie" / "files" / session_id / trigger.event_id
    event_dir.mkdir(parents=True, exist_ok=True)

    records: list[SlackFileRecord] = []
    used_paths = _existing_paths(event_dir)
    for file_info in files:
        slack_file_id = str(file_info.get("id") or "")
        if not slack_file_id:
            continue
        existing = _get_file_record(db, session_id=session_id, slack_event_id=trigger.event_id, slack_file_id=slack_file_id)
        if existing is not None:
            records.append(existing)
            continue

        name = str(file_info.get("name") or file_info.get("title") or slack_file_id)
        mimetype = _optional_str(file_info.get("mimetype"))
        filetype = _optional_str(file_info.get("filetype"))
        url = _optional_str(file_info.get("url_private_download") or file_info.get("url_private"))
        local_path: str | None = None
        byte_count = 0
        status = "failed"
        error: str | None = None

        if not url:
            error = "missing_download_url"
        else:
            destination = _unique_destination(event_dir, name, used_paths)
            used_paths.add(destination)
            try:
                byte_count = int(file_client.download_file(url, destination))
                local_path = str(destination)
                status = "staged"
            except Exception as exc:
                if destination.exists():
                    destination.unlink()
                preview = _untruncated_text_preview(file_info)
                if preview is not None:
                    destination.write_bytes(preview)
                    byte_count = len(preview)
                    local_path = str(destination)
                    status = "staged"
                else:
                    error = str(exc) or exc.__class__.__name__

        db.execute(
            """
            INSERT INTO slack_files(
                session_id,
                slack_event_id,
                slack_file_id,
                name,
                mimetype,
                filetype,
                url_private_download,
                local_path,
                byte_count,
                status,
                error
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                trigger.event_id,
                slack_file_id,
                name,
                mimetype,
                filetype,
                url,
                local_path,
                byte_count,
                status,
                error,
            ),
        )
        records.append(
            _get_file_record(db, session_id=session_id, slack_event_id=trigger.event_id, slack_file_id=slack_file_id)
        )
    return records


def list_files_for_inbox(db: sqlite3.Connection, *, session_id: str, slack_event_id: str | None) -> list[SlackFileRecord]:
    if slack_event_id is None:
        return []
    rows = db.execute(
        """
        SELECT *
        FROM slack_files
        WHERE session_id = ? AND slack_event_id = ?
        ORDER BY id ASC
        """,
        (session_id, slack_event_id),
    ).fetchall()
    return [_to_record(row) for row in rows]


def format_file_prompt_sections(records: list[SlackFileRecord]) -> str:
    attached = [record.local_path for record in records if record.status == "staged" and record.local_path]
    warnings = [record for record in records if record.status != "staged"]
    sections: list[str] = []
    if attached:
        sections.append("Attached files:\n" + "\n".join(f"- {path}" for path in attached))
    if warnings:
        lines = []
        for record in warnings:
            reason = record.error or record.status
            lines.append(f"- {record.name}: download failed: {reason}")
        sections.append("Attachment warnings:\n" + "\n".join(lines))
    return "\n\n".join(sections)


def build_goal_with_files(goal: str, records: list[SlackFileRecord]) -> str:
    sections = format_file_prompt_sections(records)
    if not sections:
        return goal
    return f"{goal.rstrip()}\n\n{sections}" if goal.strip() else sections


def _extract_files(payload: dict[str, Any]) -> list[dict[str, Any]]:
    event = payload.get("event") if isinstance(payload, dict) else None
    files = event.get("files") if isinstance(event, dict) else None
    if not isinstance(files, list):
        return []
    return [file_info for file_info in files if isinstance(file_info, dict)]


def _get_file_record(
    db: sqlite3.Connection,
    *,
    session_id: str,
    slack_event_id: str,
    slack_file_id: str,
) -> SlackFileRecord | None:
    row = db.execute(
        """
        SELECT *
        FROM slack_files
        WHERE session_id = ? AND slack_event_id = ? AND slack_file_id = ?
        """,
        (session_id, slack_event_id, slack_file_id),
    ).fetchone()
    return _to_record(row) if row is not None else None


def _to_record(row: sqlite3.Row) -> SlackFileRecord:
    return SlackFileRecord(
        id=row["id"],
        session_id=row["session_id"],
        slack_event_id=row["slack_event_id"],
        slack_file_id=row["slack_file_id"],
        name=row["name"],
        mimetype=row["mimetype"],
        filetype=row["filetype"],
        url_private_download=row["url_private_download"],
        local_path=row["local_path"],
        byte_count=row["byte_count"],
        status=row["status"],
        error=row["error"],
    )


def _existing_paths(event_dir: Path) -> set[Path]:
    if not event_dir.exists():
        return set()
    return {path for path in event_dir.iterdir() if path.is_file()}


def _unique_destination(event_dir: Path, name: str, used_paths: set[Path]) -> Path:
    safe_name = _safe_filename(name)
    candidate = event_dir / safe_name
    if candidate not in used_paths and not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        candidate = event_dir / f"{stem}-{index}{suffix}"
        if candidate not in used_paths and not candidate.exists():
            return candidate
        index += 1


def _safe_filename(name: str) -> str:
    base = Path(name).name.strip() or "file"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return safe or "file"


def _untruncated_text_preview(file_info: dict[str, Any]) -> bytes | None:
    preview = file_info.get("preview")
    if not isinstance(preview, str):
        return None
    if file_info.get("preview_is_truncated") is not False:
        return None
    mimetype = _optional_str(file_info.get("mimetype")) or ""
    filetype = _optional_str(file_info.get("filetype")) or ""
    mode = _optional_str(file_info.get("mode")) or ""
    if not (mimetype.startswith("text/") or filetype == "text" or mode == "snippet"):
        return None
    return preview.encode("utf-8")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
