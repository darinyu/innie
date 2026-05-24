# Slack File Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download inbound Slack file attachments into local Innie state, include staged local paths in harness prompts, and clean up old staged files.

**Architecture:** Add a focused `slack_files` module for extraction, staging, persistence, and prompt formatting. Keep adapters unchanged: runtime enriches `TaskRequest.goal`, so Codex and Claude both receive the same local file paths. Extend cleanup to count and delete eligible `.innie/files` paths together with completed task cleanup.

**Tech Stack:** Python standard library, SQLite, `unittest`, existing Innie runtime and Slack web client.

---

### Task 1: Persist And Stage Slack Files

**Files:**
- Create: `src/innie/slack_files.py`
- Modify: `src/innie/db.py`
- Modify: `src/innie/slack_client.py`
- Modify: `src/innie/pipeline.py`
- Test: `tests/test_slack_files.py`

- [ ] **Step 1: Write failing tests**

Cover metadata extraction, safe local filename staging with a fake downloader, failed downloads becoming records, and duplicate event idempotency.

- [ ] **Step 2: Verify tests fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_slack_files -v`

- [ ] **Step 3: Implement schema and staging**

Add `slack_files` table, downloader protocol, `stage_slack_files_for_trigger()`, and `SlackWebClient.download_file()`.

- [ ] **Step 4: Verify tests pass**

Run: `PYTHONPATH=src python3 -m unittest tests.test_slack_files -v`

### Task 2: Enrich Harness Prompts For Codex And Claude

**Files:**
- Modify: `src/innie/runtime.py`
- Modify: `src/innie/inbox.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Use a recording adapter under both `codex` and `claude` harness ids to prove task goals include staged file paths and warnings.

- [ ] **Step 2: Verify tests fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_runtime.RuntimeTest.test_manager_appends_slack_file_paths_to_codex_goal tests.test_runtime.RuntimeTest.test_manager_appends_slack_file_paths_to_claude_goal -v`

- [ ] **Step 3: Implement prompt enrichment**

Fetch `slack_files` rows for the claimed inbox row and append `Attached files` / `Attachment warnings` sections before `create_task()`.

- [ ] **Step 4: Verify tests pass**

Run the focused runtime tests again.

### Task 3: Clean Up Old Staged Files

**Files:**
- Modify: `src/innie/cleanup.py`
- Test: `tests/test_cleanup.py`

- [ ] **Step 1: Write failing cleanup tests**

Cover preview/apply counting and deleting old staged files under `.innie/files`, while refusing to delete a `slack_files.local_path` outside that directory.

- [ ] **Step 2: Verify tests fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_cleanup -v`

- [ ] **Step 3: Implement cleanup**

Include eligible `slack_files` rows for sessions with cleanup-eligible completed tasks. Delete only files under `.innie/files`, delete their rows, and include counts in the existing preview.

- [ ] **Step 4: Verify tests pass**

Run cleanup tests again.

### Task 4: Full Verification

**Files:**
- All changed files

- [ ] **Step 1: Run focused tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_slack_files tests.test_runtime tests.test_cleanup -v`

- [ ] **Step 2: Run full test suite**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests -v`

- [ ] **Step 3: Check formatting whitespace**

Run: `git diff --check`
