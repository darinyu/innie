import sqlite3
from pathlib import Path


def create_sample_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            slack_channel_id TEXT,
            slack_root_ts TEXT,
            slack_thread_ts TEXT,
            trigger_type TEXT,
            output_target TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            harness_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            locked_by TEXT,
            locked_at TEXT,
            lock_expires_at TEXT,
            harness_resume_id TEXT
        );
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL,
            goal TEXT NOT NULL,
            output_target TEXT NOT NULL,
            harness_id TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE session_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            slack_event_id TEXT,
            slack_channel_id TEXT NOT NULL,
            slack_message_ts TEXT NOT NULL,
            slack_thread_ts TEXT,
            sender_user_id TEXT,
            text TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL,
            processed_at TEXT
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            task_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE hook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            hook_name TEXT NOT NULL,
            dedupe_key TEXT,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            task_id TEXT,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("sess_done", "C1", "1", "1", "slack", "slack", "completed", "claude", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", None, None, None, None),
            ("sess_running", "C1", "2", "2", "slack", "slack", "running", "codex", "2026-01-01T00:02:00Z", "2026-01-01T00:05:00Z", "worker-1", "2026-01-01T00:04:00Z", "2026-01-01T00:07:00Z", None),
            ("sess_failed", "C2", "3", "3", "slack", "slack", "failed", "claude", "2026-01-01T00:03:00Z", "2026-01-01T00:04:00Z", None, None, None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("task_done", "sess_done", "completed", "summarize repo", "slack", "claude", "autonomous", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "2026-01-01T00:01:00Z"),
            ("task_running", "sess_running", "running", "debug checkout flow", "slack", "codex", "autonomous", "2026-01-01T00:02:00Z", "2026-01-01T00:05:00Z", None),
            ("task_failed", "sess_failed", "failed", "ship deploy", "slack", "claude", "autonomous", "2026-01-01T00:03:00Z", "2026-01-01T00:04:00Z", "2026-01-01T00:04:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO session_inbox(session_id, slack_event_id, slack_channel_id, slack_message_ts, slack_thread_ts, sender_user_id, text, payload_json, status, created_at, processed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("sess_running", "E1", "C1", "2.1", "2", "U1", "debug checkout", "{}", "queued", "2026-01-01T00:02:01Z", None),
            ("sess_running", "E2", "C1", "2.2", "2", "U1", "also inspect tests", "{}", "queued", "2026-01-01T00:02:02Z", None),
        ],
    )
    conn.executemany(
        "INSERT INTO task_events(id, session_id, task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "sess_done", "task_done", "harness.completed", '{"message":"done"}', "2026-01-01T00:01:00Z"),
            (2, "sess_running", "task_running", "worker.session.claimed", "{}", "2026-01-01T00:02:03Z"),
            (3, "sess_running", "task_running", "harness.progress", '{"message":"running tests"}', "2026-01-01T00:05:00Z"),
            (4, "sess_failed", "task_failed", "harness.failed", '{"message":"boom"}', "2026-01-01T00:04:00Z"),
        ],
    )
    conn.execute(
        "INSERT INTO hook_events(session_id, hook_name, dedupe_key, status, duration_ms, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess_running", "slack.event.accepted", "hook:1", "ok", 12, "{}", "2026-01-01T00:02:04Z"),
    )
    conn.commit()
    conn.close()
