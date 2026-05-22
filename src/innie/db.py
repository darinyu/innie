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
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE(slack_channel_id, slack_root_ts)
        );

        CREATE TABLE IF NOT EXISTS slack_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
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
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        """
    )
