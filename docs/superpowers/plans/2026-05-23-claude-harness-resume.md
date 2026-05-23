# Claude Harness Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in Claude harness support that behaves like the Codex subprocess adapter and resumes Claude conversations across messages in the same Slack session.

**Architecture:** Add a `ClaudeCliAdapter` beside the Codex adapter. Persist a generic `harness_resume_id` on `sessions`; runtime passes it into each `TaskRequest.recovery_context`, and the Claude adapter maps it to `claude --resume <id>`.

**Tech Stack:** Python 3.10+, unittest, SQLite schema migration via `initialize_schema`, Claude Code CLI stream-json mode.

---

### Task 1: Claude Adapter Tests

**Files:**
- Create: `tests/test_claude_adapter.py`
- Create: `src/innie/adapters/claude.py`
- Modify: `src/innie/adapters/__init__.py`

- [ ] Write failing tests for spawning `claude -p --output-format stream-json --input-format text`, stdin prompt delivery, resume flag usage, stream-json event mapping, session id capture, and stderr-on-failure reporting.
- [ ] Run `python -m unittest tests.test_claude_adapter -v` and verify it fails because `innie.adapters.claude` does not exist.
- [ ] Implement `ClaudeCliAdapter` with the same lifecycle shape as `CodexCliAdapter`.
- [ ] Re-run `python -m unittest tests.test_claude_adapter -v` and verify it passes.

### Task 2: Runtime Resume Persistence

**Files:**
- Modify: `src/innie/db.py`
- Modify: `src/innie/sessions.py`
- Modify: `src/innie/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] Write failing runtime tests proving a Claude resume id from a harness event is persisted to the session, and the next turn receives it in `TaskRequest.recovery_context`.
- [ ] Run the focused runtime tests and verify they fail because `harness_resume_id` is not stored or passed.
- [ ] Add a nullable `sessions.harness_resume_id` column, extend `SessionRecord`, and update runtime to persist `resume_id` from `TaskHandle` or event payload.
- [ ] Re-run the focused runtime tests and verify they pass.

### Task 3: Opt-In Wiring and Docs

**Files:**
- Modify: `src/innie/runner.py`
- Modify: `src/innie/cli.py`
- Modify: `src/innie/bootstrap.py`
- Modify: `README.md`
- Modify: `src/innie/slack_setup.py`
- Test: `tests/test_cli_run.py`
- Test: `tests/test_bootstrap.py`
- Test: `tests/test_slack_setup.py`

- [ ] Write failing tests proving `--harness claude` is accepted, adapter map includes Claude, bootstrap reports `codex, claude` when missing, and setup/docs mention the opt-in command.
- [ ] Implement opt-in registration while keeping `codex` as default.
- [ ] Run focused tests and then the full test suite.
