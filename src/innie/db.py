from __future__ import annotations

from pathlib import Path
import sqlite3


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            slack_channel_id TEXT,
            slack_root_ts TEXT,
            slack_thread_ts TEXT,
            trigger_type TEXT,
            output_target TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            harness_id TEXT,
            harness_resume_id TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE(slack_channel_id, slack_root_ts)
        );

        CREATE TABLE IF NOT EXISTS slack_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            slack_event_id TEXT NOT NULL UNIQUE,
            trigger_type TEXT NOT NULL,
            slack_channel_id TEXT NOT NULL,
            slack_message_ts TEXT NOT NULL,
            slack_thread_ts TEXT,
            sender_user_id TEXT,
            text TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'accepted',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS session_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            slack_event_id TEXT,
            slack_channel_id TEXT NOT NULL,
            slack_message_ts TEXT NOT NULL,
            slack_thread_ts TEXT,
            sender_user_id TEXT,
            text TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            processed_at TEXT,
            UNIQUE(session_id, slack_event_id),
            UNIQUE(session_id, slack_channel_id, slack_message_ts)
        );

        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'created',
            goal TEXT NOT NULL,
            output_target TEXT NOT NULL,
            harness_id TEXT NOT NULL,
            execution_mode TEXT NOT NULL DEFAULT 'autonomous',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS harness_capabilities (
            harness_id TEXT PRIMARY KEY,
            capabilities_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS hook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            hook_name TEXT NOT NULL,
            dedupe_key TEXT UNIQUE,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS slack_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            slack_event_id TEXT NOT NULL,
            slack_file_id TEXT NOT NULL,
            name TEXT NOT NULL,
            mimetype TEXT,
            filetype TEXT,
            url_private_download TEXT,
            local_path TEXT,
            byte_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE(session_id, slack_event_id, slack_file_id)
        );
        """
    )
    _ensure_column(db, "slack_triggers", "session_id", "TEXT REFERENCES sessions(id) ON DELETE SET NULL")
    _ensure_column(db, "sessions", "locked_by", "TEXT")
    _ensure_column(db, "sessions", "locked_at", "TEXT")
    _ensure_column(db, "sessions", "lock_expires_at", "TEXT")
    _ensure_column(db, "sessions", "harness_resume_id", "TEXT")
    _ensure_column(db, "task_events", "task_id", "TEXT REFERENCES tasks(id) ON DELETE CASCADE")
    _ensure_column(db, "artifacts", "task_id", "TEXT REFERENCES tasks(id) ON DELETE CASCADE")
    _ensure_column(db, "artifacts", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(db, "hook_events", "dedupe_key", "TEXT")
    _ensure_column(db, "slack_files", "url_private_download", "TEXT")
    _ensure_column(db, "slack_files", "byte_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(db, "slack_files", "error", "TEXT")
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tasks_session_created_at
        ON tasks(session_id, created_at)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tasks_status_updated_at
        ON tasks(status, updated_at)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_task_events_task_created_at
        ON task_events(task_id, created_at)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_artifacts_task_created_at
        ON artifacts(task_id, created_at)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_slack_files_session_event
        ON slack_files(session_id, slack_event_id)
        """
    )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hook_events_dedupe_key
        ON hook_events(dedupe_key)
        WHERE dedupe_key IS NOT NULL
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_inbox_status_id
        ON session_inbox(status, id)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_lock_expires_at
        ON sessions(lock_expires_at)
        """
    )


def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
