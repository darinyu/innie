# Slack File Support Design

## Goal

Support Slack messages with attached files by downloading those files into local Innie state, recording their metadata, and passing their local paths to the selected harness. Cleanup will also remove staged Slack files that are older than the completed-task retention window.

## Scope

This design covers inbound Slack file attachments only. It does not add outbound Slack file uploads for harness artifacts, file preview rendering in Slack, or file content inlining into prompts.

## User Flow

1. A user sends a DM, mention, or reply that routes to Innie and includes Slack file attachments.
2. Innie accepts the event as it does today.
3. Innie downloads each accessible file to `.innie/files/<session_id>/<slack_event_id>/`.
4. Innie records metadata for each staged file.
5. The harness receives the original Slack text plus an `Attached files` section listing absolute local paths.
6. If a file cannot be downloaded, Innie still runs the task and includes a warning in the prompt.

## Architecture

Add a small file ingestion module that owns Slack file metadata extraction, safe filename handling, private URL download, and prompt formatting. Slack intake stays responsible for routing and session resolution. The runtime remains harness-neutral and only receives enriched inbox context when building a `TaskRequest`.

Persist file records in a new `slack_files` table keyed by session, Slack event, and Slack file id. Store original filename, local path, MIME type or Slack filetype, byte count, status, and any failure reason. File bytes live under `.innie/files`, so they are treated as Innie-owned local state.

The Slack web client gets a narrow download method that fetches Slack private file URLs with the bot token. Tests use a fake downloader rather than hitting Slack.

## Prompt Contract

When an inbox row has staged files, append this section to the harness goal:

```text
Attached files:
- /absolute/path/to/file.pdf
- /absolute/path/to/data.csv
```

When a file failed to stage, append:

```text
Attachment warnings:
- report.pdf: download failed: <reason>
```

The adapter contract does not change. Codex and Claude receive a normal text prompt and can access local files through their existing workspace access.

## Cleanup

Extend cleanup preview and apply logic to include `slack_files` rows and their local files for sessions whose completed tasks are eligible for cleanup. For Slack file records, only delete staged files under `.innie/files`; never delete paths outside that directory. Cleanup output counts these files and bytes alongside existing artifact cleanup.

## Error Handling

Missing or inaccessible Slack download URLs become failed file records, not rejected Slack events. Path traversal in filenames is prevented by using the basename and replacing unsafe characters. Duplicate file names in the same event directory get a stable suffix. Duplicate Slack events remain idempotent through the existing event and inbox uniqueness checks.

## Testing

Add focused tests for:

- extracting file metadata from Slack event payloads,
- staging files through a fake Slack file client,
- prompt enrichment with staged paths and warnings,
- idempotent duplicate intake,
- cleanup preview and apply deleting old staged Slack files,
- cleanup refusing to delete file paths outside `.innie/files`.

## Self-Review

The design is intentionally limited to inbound files. It uses local paths instead of prompt inlining to avoid token bloat and binary-file handling. Cleanup scope is tied to existing completed-task retention so staged Slack files follow the same lifecycle as old task state.
