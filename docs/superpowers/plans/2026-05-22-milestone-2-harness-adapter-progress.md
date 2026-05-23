# Milestone 2 Harness Adapter And Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace placeholder session work with the first real harness adapter path, normalized progress events, Slack progress/final output delivery, and basic artifact collection.

**Supported Harness For V0:** V0 supports Codex CLI as its first real harness. The adapter boundary remains harness-neutral so later versions can add Claude Code, OpenCode, Goose, and custom runtimes after the Codex path proves the event schema and Slack loop. The `scripted` adapter exists only for deterministic tests.

**Architecture:** Keep Innie as the durable product shell and put harness-specific behavior behind a small adapter interface. The session actor creates one durable task per claimed inbox turn, streams normalized Codex events into SQLite, renders safe progress to Slack, records Codex capabilities, and collects artifacts only when the Codex adapter exposes them.

**Tech Stack:** Python 3.10+, stdlib `asyncio`, `sqlite3`, subprocess execution for the first Codex CLI adapter, `unittest`.

---

## File Structure

- Create `src/innie/harness.py`: harness-neutral dataclasses, event kinds, capability metadata, adapter protocol, and a test-only scripted adapter.
- Create `src/innie/tasks.py`: durable task creation, status transitions, event append helpers, usage/artifact persistence helpers.
- Create `src/innie/progress.py`: maps safe lifecycle/harness events to Slack thread text.
- Create `src/innie/adapters/__init__.py`: adapter package exports.
- Create `src/innie/adapters/codex.py`: first subprocess adapter using `codex exec --json`.
- Modify `src/innie/bootstrap.py`: make the v0 user-facing harness dependency check require Codex.
- Modify `src/innie/db.py`: add `tasks`, adapter capability rows, task-linked event/artifact columns, and indexes.
- Modify `src/innie/runtime.py`: replace placeholder output with adapter execution and progress delivery.
- Modify `src/innie/control.py`: include current task and last harness event in status output.
- Modify `src/innie/cli.py`: show task rows, task-linked events, artifacts, and adapter capability metadata in `innie logs`.
- Modify `README.md`: describe Codex CLI as the v0 supported harness and list other harnesses as future adapters.
- Add tests in `tests/test_harness.py`, `tests/test_tasks.py`, `tests/test_progress.py`, `tests/test_codex_adapter.py`, `tests/test_milestone2_acceptance.py`; update `tests/test_runtime.py`, `tests/test_control.py`, and `tests/test_milestone1_acceptance.py`.

## Harness Support Decision

- V0 supported harness: `codex`, implemented by `src/innie/adapters/codex.py`.
- V0 default runtime adapter map: `{"codex": CodexCliAdapter()}`.
- V0 test helper: `scripted`, implemented inside `src/innie/harness.py` and used only by unit/acceptance tests.
- Future adapter examples: `claude`, `opencode`, `goose`. Do not present them as installed v0 runtime support until real adapters exist.

## Task 1: Define The Harness Event Contract

**Files:**
- Create: `src/innie/harness.py`
- Test: `tests/test_harness.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import asyncio
import unittest

from innie.harness import (
    HarnessArtifact,
    HarnessCapabilities,
    HarnessEvent,
    ScriptedHarnessAdapter,
    TaskRequest,
    TokenUsage,
)


class HarnessContractTest(unittest.TestCase):
    def test_usage_cache_hit_rate_is_zero_without_input_tokens(self) -> None:
        self.assertEqual(0.0, TokenUsage().cache_hit_rate)

    def test_usage_cache_hit_rate_uses_input_tokens(self) -> None:
        usage = TokenUsage(input_tokens=100, cache_read_tokens=25)
        self.assertEqual(0.25, usage.cache_hit_rate)

    def test_scripted_adapter_streams_events_and_collects_artifacts(self) -> None:
        adapter = ScriptedHarnessAdapter(
            events=[
                HarnessEvent(type="started", message="started"),
                HarnessEvent(type="progress", message="running tests"),
                HarnessEvent(type="output", message="done"),
                HarnessEvent(type="completed", message="completed"),
            ],
            artifacts=[HarnessArtifact(kind="summary", path="summary.md")],
        )
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="ship it",
            workspace=".",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> tuple[list[str], list[str]]:
            handle = await adapter.start_task(request)
            event_types = [event.type async for event in adapter.stream_events(handle.task_id)]
            artifacts = await adapter.collect_artifacts(handle.task_id)
            return event_types, [artifact.kind for artifact in artifacts]

        event_types, artifact_kinds = asyncio.run(run())
        self.assertEqual(["started", "progress", "output", "completed"], event_types)
        self.assertEqual(["summary"], artifact_kinds)
        self.assertTrue(adapter.capabilities.supports_streaming)
        self.assertTrue(adapter.capabilities.supports_autonomous_mode)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_harness -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'innie.harness'`.

- [ ] **Step 3: Add the harness contract**

Implement `src/innie/harness.py` with:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


HarnessEventType = Literal[
    "started",
    "progress",
    "tool_use",
    "tool_result",
    "output",
    "usage",
    "completed",
    "failed",
    "canceled",
]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_read_tokens / self.input_tokens if self.input_tokens else 0.0


@dataclass(frozen=True)
class HarnessEvent:
    type: HarnessEventType
    message: str | None = None
    usage: TokenUsage | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessArtifact:
    kind: str
    path: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessCapabilities:
    supports_streaming: bool = False
    supports_resume: bool = False
    supports_structured_artifacts: bool = False
    supports_native_approval: bool = False
    supports_autonomous_mode: bool = False
    supports_subagents: bool = False


@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    session_id: str
    goal: str
    workspace: str
    output_target: str
    execution_mode: str
    recovery_context: dict[str, Any]


@dataclass(frozen=True)
class TaskHandle:
    task_id: str
    harness_id: str
    resume_id: str | None = None


class HarnessAdapter(Protocol):
    harness_id: str
    capabilities: HarnessCapabilities

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        ...

    async def send_input(self, task_id: str, input: str) -> None:
        ...

    async def cancel_task(self, task_id: str) -> None:
        ...

    def stream_events(self, task_id: str) -> AsyncIterator[HarnessEvent]:
        ...

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        ...


class ScriptedHarnessAdapter:
    harness_id = "scripted"
    capabilities = HarnessCapabilities(
        supports_streaming=True,
        supports_autonomous_mode=True,
        supports_structured_artifacts=True,
    )

    def __init__(self, *, events: list[HarnessEvent], artifacts: list[HarnessArtifact] | None = None) -> None:
        self._events = events
        self._artifacts = artifacts or []
        self._task_id: str | None = None
        self.canceled: list[str] = []

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        self._task_id = request.task_id
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError("mid-turn input is not supported by the scripted adapter")

    async def cancel_task(self, task_id: str) -> None:
        self.canceled.append(task_id)

    async def stream_events(self, task_id: str) -> AsyncIterator[HarnessEvent]:
        if task_id != self._task_id:
            raise KeyError(task_id)
        for event in self._events:
            yield event

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        if task_id != self._task_id:
            raise KeyError(task_id)
        return list(self._artifacts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_harness -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/innie/harness.py tests/test_harness.py
git commit -m "feat: define harness adapter contract"
```

## Task 2: Add Durable Task Storage

**Files:**
- Modify: `src/innie/db.py`
- Create: `src/innie/tasks.py`
- Test: `tests/test_tasks.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.harness import HarnessArtifact, HarnessCapabilities, HarnessEvent, TokenUsage
from innie.tasks import append_harness_event, create_task, record_adapter_capabilities, record_artifacts, set_task_status


class TaskStorageTest(unittest.TestCase):
    def test_task_events_usage_artifacts_and_capabilities_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = connect(Path(tmp) / "innie.db")
            initialize_schema(db)

            task = create_task(
                db,
                session_id="sess_1",
                goal="run tests",
                output_target="slack:D1:100.1",
                harness_id="codex",
                execution_mode="autonomous",
            )
            append_harness_event(
                db,
                task,
                HarnessEvent(
                    type="usage",
                    usage=TokenUsage(input_tokens=10, output_tokens=5, cache_read_tokens=2),
                    payload={"raw": "kept"},
                ),
            )
            record_artifacts(db, task, [HarnessArtifact(kind="summary", path="summary.md", metadata={"lines": 3})])
            record_adapter_capabilities(db, "codex", HarnessCapabilities(supports_streaming=True))
            set_task_status(db, task.id, "completed")
            db.commit()

            stored_task = db.execute("SELECT * FROM tasks WHERE id = ?", (task.id,)).fetchone()
            stored_event = db.execute("SELECT * FROM task_events WHERE task_id = ?", (task.id,)).fetchone()
            stored_artifact = db.execute("SELECT * FROM artifacts WHERE task_id = ?", (task.id,)).fetchone()
            stored_caps = db.execute("SELECT * FROM harness_capabilities WHERE harness_id = 'codex'").fetchone()

            self.assertEqual("completed", stored_task["status"])
            self.assertEqual("harness.usage", stored_event["event_type"])
            self.assertIn('"input_tokens": 10', stored_event["payload_json"])
            self.assertEqual("summary", stored_artifact["kind"])
            self.assertIn("supports_streaming", stored_caps["capabilities_json"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_tasks -v`

Expected: FAIL because `innie.tasks` does not exist.

- [ ] **Step 3: Extend schema**

Update `initialize_schema()` in `src/innie/db.py` to create these tables/columns:

```sql
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
```

Also call `_ensure_column(db, "task_events", "task_id", "TEXT REFERENCES tasks(id) ON DELETE CASCADE")`, `_ensure_column(db, "artifacts", "task_id", "TEXT REFERENCES tasks(id) ON DELETE CASCADE")`, and `_ensure_column(db, "artifacts", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")`, then add indexes for `tasks(session_id, created_at)`, `tasks(status, updated_at)`, `task_events(task_id, created_at)`, and `artifacts(task_id, created_at)`.

- [ ] **Step 4: Add task helpers**

Implement `src/innie/tasks.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import uuid

from .harness import HarnessArtifact, HarnessCapabilities, HarnessEvent


@dataclass(frozen=True)
class TaskRecord:
    id: str
    session_id: str
    goal: str
    output_target: str
    harness_id: str
    execution_mode: str
    status: str


def create_task(
    db: sqlite3.Connection,
    *,
    session_id: str,
    goal: str,
    output_target: str,
    harness_id: str,
    execution_mode: str = "autonomous",
) -> TaskRecord:
    task_id = f"task_{uuid.uuid4().hex[:16]}"
    db.execute(
        """
        INSERT INTO tasks(id, session_id, status, goal, output_target, harness_id, execution_mode)
        VALUES(?, ?, 'created', ?, ?, ?, ?)
        """,
        (task_id, session_id, goal, output_target, harness_id, execution_mode),
    )
    return TaskRecord(task_id, session_id, goal, output_target, harness_id, execution_mode, "created")


def set_task_status(db: sqlite3.Connection, task_id: str, status: str) -> None:
    completed_sql = ", completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')" if status in {"completed", "failed", "canceled"} else ""
    db.execute(
        f"""
        UPDATE tasks
        SET status = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            {completed_sql}
        WHERE id = ?
        """,
        (status, task_id),
    )


def append_harness_event(db: sqlite3.Connection, task: TaskRecord, event: HarnessEvent) -> None:
    payload = {
        "type": event.type,
        "message": event.message,
        "payload": event.payload,
        "usage": None if event.usage is None else event.usage.__dict__,
    }
    db.execute(
        """
        INSERT INTO task_events(session_id, task_id, event_type, payload_json)
        VALUES(?, ?, ?, ?)
        """,
        (task.session_id, task.id, f"harness.{event.type}", json.dumps(payload, sort_keys=True)),
    )


def record_artifacts(db: sqlite3.Connection, task: TaskRecord, artifacts: list[HarnessArtifact]) -> None:
    for artifact in artifacts:
        db.execute(
            """
            INSERT INTO artifacts(session_id, task_id, kind, path, metadata_json)
            VALUES(?, ?, ?, ?, ?)
            """,
            (task.session_id, task.id, artifact.kind, artifact.path, json.dumps(artifact.metadata, sort_keys=True)),
        )


def record_adapter_capabilities(db: sqlite3.Connection, harness_id: str, capabilities: HarnessCapabilities) -> None:
    db.execute(
        """
        INSERT INTO harness_capabilities(harness_id, capabilities_json)
        VALUES(?, ?)
        ON CONFLICT(harness_id) DO UPDATE SET
            capabilities_json = excluded.capabilities_json,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (harness_id, json.dumps(capabilities.__dict__, sort_keys=True)),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_tasks -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/innie/db.py src/innie/tasks.py tests/test_tasks.py
git commit -m "feat: persist harness tasks and events"
```

## Task 3: Render Safe Slack Progress

**Files:**
- Create: `src/innie/progress.py`
- Test: `tests/test_progress.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import unittest

from innie.harness import HarnessEvent, TokenUsage
from innie.progress import SlackProgressRenderer


class SlackProgressRendererTest(unittest.TestCase):
    def test_renders_lifecycle_progress_and_final_output(self) -> None:
        renderer = SlackProgressRenderer()
        self.assertEqual("Started task task_1.", renderer.render("task_1", HarnessEvent(type="started")))
        self.assertEqual("Progress: running tests", renderer.render("task_1", HarnessEvent(type="progress", message="running tests")))
        self.assertEqual("Done:\nship complete", renderer.render("task_1", HarnessEvent(type="output", message="ship complete")))
        self.assertEqual("Task task_1 completed.", renderer.render("task_1", HarnessEvent(type="completed")))

    def test_renders_usage_without_private_reasoning(self) -> None:
        renderer = SlackProgressRenderer()
        text = renderer.render(
            "task_1",
            HarnessEvent(
                type="usage",
                usage=TokenUsage(input_tokens=10, output_tokens=4, cache_read_tokens=5),
                payload={"chain_of_thought": "never show this"},
            ),
        )
        self.assertEqual("Usage: 10 input, 4 output, 50% cache hit.", text)
        self.assertNotIn("never show", text)

    def test_skips_tool_payload_without_message(self) -> None:
        renderer = SlackProgressRenderer()
        self.assertIsNone(renderer.render("task_1", HarnessEvent(type="tool_result", payload={"private": "raw"})))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_progress -v`

Expected: FAIL because `innie.progress` does not exist.

- [ ] **Step 3: Add progress renderer**

Implement `src/innie/progress.py` with:

```python
from __future__ import annotations

from .harness import HarnessEvent


class SlackProgressRenderer:
    def render(self, task_id: str, event: HarnessEvent) -> str | None:
        if event.type == "started":
            return f"Started task {task_id}."
        if event.type == "progress" and event.message:
            return f"Progress: {event.message}"
        if event.type == "tool_use" and event.message:
            return f"Using tool: {event.message}"
        if event.type == "tool_result" and event.message:
            return f"Tool result: {event.message}"
        if event.type == "output" and event.message:
            return f"Done:\n{event.message}"
        if event.type == "usage" and event.usage:
            cache_pct = int(event.usage.cache_hit_rate * 100)
            return (
                f"Usage: {event.usage.input_tokens} input, "
                f"{event.usage.output_tokens} output, {cache_pct}% cache hit."
            )
        if event.type == "completed":
            return f"Task {task_id} completed."
        if event.type == "failed":
            return f"Task {task_id} failed: {event.message or 'no error message'}"
        if event.type == "canceled":
            return f"Task {task_id} canceled."
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_progress -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/innie/progress.py tests/test_progress.py
git commit -m "feat: render safe Slack progress"
```

## Task 4: Make V0 User-Facing Harness Support Codex

**Files:**
- Modify: `src/innie/bootstrap.py`
- Modify: `README.md`
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing bootstrap expectation**

Update the harness dependency test in `tests/test_bootstrap.py` so the missing v0 harness message names Codex:

```python
result = init_workspace(Path(tmp), assume_yes=True)
self.assertTrue(any("none found: codex" in message for message in result.messages))
self.assertFalse(any("claude" in message.lower() for message in result.messages))
self.assertFalse(any("opencode" in message.lower() for message in result.messages))
self.assertFalse(any("goose" in message.lower() for message in result.messages))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_bootstrap -v`

Expected: FAIL because `bootstrap.py` still reports `codex, claude, opencode, goose`.

- [ ] **Step 3: Restrict v0 dependency detection to Codex**

In `src/innie/bootstrap.py`, change:

```python
SUPPORTED_HARNESSES = ("codex", "claude", "opencode", "goose")
```

to:

```python
SUPPORTED_HARNESSES = ("codex",)
```

and change the missing harness detail from:

```python
"none found: codex, claude, opencode, goose"
```

to:

```python
"none found: codex"
```

- [ ] **Step 4: Update README v0 dependency wording**

Change the dependency bullet in `README.md` from:

```markdown
- At least one installed agent harness, such as Codex CLI, Claude Code,
  OpenCode, or Goose.
```

to:

```markdown
- Codex CLI. V0 supports Codex; Claude Code, OpenCode, Goose, and custom
  runtimes are future adapters.
```

- [ ] **Step 5: Run bootstrap tests**

Run: `python -m unittest tests.test_bootstrap -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/innie/bootstrap.py README.md tests/test_bootstrap.py
git commit -m "docs: make codex the v0 supported harness"
```

## Task 5: Add The Codex CLI Adapter

**Files:**
- Create: `src/innie/adapters/__init__.py`
- Create: `src/innie/adapters/codex.py`
- Test: `tests/test_codex_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import asyncio
import json
import unittest

from innie.adapters.codex import CodexCliAdapter
from innie.harness import TaskRequest


class FakeProcess:
    def __init__(self, lines: list[dict], returncode: int = 0) -> None:
        self.stdout = asyncio.StreamReader()
        self.returncode = returncode
        for line in lines:
            self.stdout.feed_data((json.dumps(line) + "\n").encode("utf-8"))
        self.stdout.feed_eof()

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15


class CodexCliAdapterTest(unittest.TestCase):
    def test_maps_json_events_to_normalized_harness_events(self) -> None:
        process = FakeProcess(
            [
                {"type": "session.started"},
                {"type": "agent_message_delta", "delta": "working"},
                {"type": "token_count", "input_tokens": 10, "output_tokens": 5},
                {"type": "agent_message", "message": "final answer"},
                {"type": "session.finished"},
            ]
        )

        async def spawn(*args: str, cwd: str):
            return process

        adapter = CodexCliAdapter(spawn=spawn)
        request = TaskRequest(
            task_id="task_1",
            session_id="sess_1",
            goal="write tests",
            workspace="/tmp/work",
            output_target="slack:D1:100.1",
            execution_mode="autonomous",
            recovery_context={},
        )

        async def run() -> list[tuple[str, str | None]]:
            handle = await adapter.start_task(request)
            return [(event.type, event.message) async for event in adapter.stream_events(handle.task_id)]

        events = asyncio.run(run())
        self.assertEqual(
            [
                ("started", "Codex started."),
                ("progress", "working"),
                ("usage", None),
                ("output", "final answer"),
                ("completed", "Codex completed."),
            ],
            events,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_codex_adapter -v`

Expected: FAIL because `innie.adapters.codex` does not exist.

- [ ] **Step 3: Add adapter package**

Create `src/innie/adapters/__init__.py`:

```python
from __future__ import annotations

from .codex import CodexCliAdapter

__all__ = ["CodexCliAdapter"]
```

- [ ] **Step 4: Implement Codex adapter**

Implement `src/innie/adapters/codex.py` with a subprocess boundary using `codex exec --json --cd <workspace> <goal>`. Keep event mapping conservative because Codex JSON event names may evolve.

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import json

from ..harness import HarnessArtifact, HarnessCapabilities, HarnessEvent, TaskHandle, TaskRequest, TokenUsage


SpawnFn = Callable[..., Awaitable[asyncio.subprocess.Process]]


class CodexCliAdapter:
    harness_id = "codex"
    capabilities = HarnessCapabilities(
        supports_streaming=True,
        supports_resume=False,
        supports_structured_artifacts=False,
        supports_native_approval=False,
        supports_autonomous_mode=True,
        supports_subagents=True,
    )

    def __init__(self, *, spawn: SpawnFn | None = None) -> None:
        self._spawn = spawn or self._default_spawn
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start_task(self, request: TaskRequest) -> TaskHandle:
        process = await self._spawn(
            "codex",
            "exec",
            "--json",
            "--cd",
            request.workspace,
            request.goal,
            cwd=request.workspace,
        )
        self._processes[request.task_id] = process
        return TaskHandle(task_id=request.task_id, harness_id=self.harness_id)

    async def send_input(self, task_id: str, input: str) -> None:
        raise NotImplementedError("Codex exec does not support mid-turn input")

    async def cancel_task(self, task_id: str) -> None:
        process = self._processes.get(task_id)
        if process is not None and process.returncode is None:
            process.terminate()

    async def stream_events(self, task_id: str):
        process = self._processes[task_id]
        stdout = process.stdout
        if stdout is None:
            yield HarnessEvent(type="failed", message="Codex stdout was not captured")
            return
        async for raw_line in stdout:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                yield HarnessEvent(type="progress", message=line)
                continue
            event = _map_codex_event(payload)
            if event is not None:
                yield event
        returncode = await process.wait()
        if returncode == 0:
            yield HarnessEvent(type="completed", message="Codex completed.")
        else:
            yield HarnessEvent(type="failed", message=f"Codex exited with status {returncode}")

    async def collect_artifacts(self, task_id: str) -> list[HarnessArtifact]:
        return []

    async def _default_spawn(self, *args: str, cwd: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )


def _map_codex_event(payload: dict) -> HarnessEvent | None:
    event_type = str(payload.get("type", ""))
    if event_type in {"session.started", "run.started"}:
        return HarnessEvent(type="started", message="Codex started.", payload=payload)
    if event_type in {"agent_message_delta", "exec_command_begin", "exec_command_output_delta"}:
        message = payload.get("delta") or payload.get("message") or payload.get("command")
        return HarnessEvent(type="progress", message=str(message) if message else None, payload=payload)
    if event_type in {"agent_message", "final_message", "run.completed"}:
        message = payload.get("message") or payload.get("text") or payload.get("last_message")
        return HarnessEvent(type="output", message=str(message) if message else None, payload=payload)
    if event_type in {"token_count", "usage"}:
        return HarnessEvent(
            type="usage",
            usage=TokenUsage(
                input_tokens=int(payload.get("input_tokens", 0) or 0),
                output_tokens=int(payload.get("output_tokens", 0) or 0),
                cache_read_tokens=int(payload.get("cache_read_tokens", 0) or 0),
                cache_write_tokens=int(payload.get("cache_write_tokens", 0) or 0),
                cost_usd=payload.get("cost_usd"),
            ),
            payload=payload,
        )
    if event_type in {"session.finished"}:
        return None
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_codex_adapter -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/innie/adapters/__init__.py src/innie/adapters/codex.py tests/test_codex_adapter.py
git commit -m "feat: add codex harness adapter"
```

## Task 6: Wire Runtime To Adapter And Slack Progress

**Files:**
- Modify: `src/innie/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Update runtime tests**

Change `tests/test_runtime.py` to inject `ScriptedHarnessAdapter` and a fake Slack reply client:

```python
class FakeSlackReplies:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        self.messages.append((channel, thread_ts, text))
```

In `test_manager_processes_sessions_concurrently_until_idle`, construct:

```python
from innie.harness import HarnessEvent, ScriptedHarnessAdapter

slack = FakeSlackReplies()
adapter = ScriptedHarnessAdapter(
    events=[
        HarnessEvent(type="started"),
        HarnessEvent(type="progress", message="working"),
        HarnessEvent(type="output", message="done"),
        HarnessEvent(type="completed"),
    ]
)
manager = SessionManager(path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
```

Expected assertions:

```python
self.assertEqual(["idle", "idle"], statuses)
self.assertEqual(2, events.count("harness.output"))
self.assertIn(("D1", "100.1", "Progress: working"), slack.messages)
self.assertIn(("D2", "200.1", "Done:\ndone"), slack.messages)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_runtime -v`

Expected: FAIL because `SessionManager` does not accept adapters, Slack replies, or workspace.

- [ ] **Step 3: Update runtime constructor**

Modify `SessionManager.__init__` to accept:

```python
def __init__(
    self,
    db_path: Path,
    *,
    adapters: dict[str, HarnessAdapter] | None = None,
    slack: SlackReplyClient | None = None,
    workspace: Path | None = None,
) -> None:
```

Default `adapters` to `{"codex": CodexCliAdapter()}`, `workspace` to `db_path.parent.parent`, and `slack` to `None`.

- [ ] **Step 4: Replace placeholder actor work**

In `SessionActor.run_until_idle`, after claiming an inbox row:

1. Create a task with `create_task()`.
2. Record adapter capabilities with `record_adapter_capabilities()`.
3. Set task/session to `running`.
4. Start the adapter with a `TaskRequest`.
5. Stream adapter events, append each with `append_harness_event()`, and post rendered progress with `SlackProgressRenderer` when `slack` is configured.
6. Mark task `completed`, `failed`, or `canceled` based on terminal event.
7. Collect artifacts and call `record_artifacts()`.
8. Mark inbox done and return session to `idle`.

Use this exact Slack target extraction:

```python
channel = row.slack_channel_id
thread_ts = row.slack_thread_ts or row.slack_message_ts
```

- [ ] **Step 5: Preserve milestone 1 compatibility**

Update `tests/test_milestone1_acceptance.py` to pass a scripted adapter and fake Slack replies into `SessionManager`; change the expected output event from `harness.placeholder.output` to `harness.output`.

- [ ] **Step 6: Run runtime and milestone 1 tests**

Run: `python -m unittest tests.test_runtime tests.test_milestone1_acceptance -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/innie/runtime.py tests/test_runtime.py tests/test_milestone1_acceptance.py
git commit -m "feat: run sessions through harness adapters"
```

## Task 7: Surface Task State In Status And Logs

**Files:**
- Modify: `src/innie/control.py`
- Modify: `src/innie/cli.py`
- Test: `tests/test_control.py`
- Test: `tests/test_cli_inspection.py`

- [ ] **Step 1: Update status tests**

In `tests/test_control.py`, insert a task and event before calling `summarize_session()`:

```python
task = create_task(
    db,
    session_id=session.id,
    goal="work",
    output_target=session.output_target,
    harness_id="codex",
)
append_harness_event(db, task, HarnessEvent(type="progress", message="running tests"))
set_task_status(db, task.id, "running")
```

Assert the summary includes:

```python
self.assertIn(f"current_task: {task.id} running", result.text)
self.assertIn("last_event: harness.progress", result.text)
```

- [ ] **Step 2: Run status test to verify it fails**

Run: `python -m unittest tests.test_control -v`

Expected: FAIL because `summarize_session()` does not show task status.

- [ ] **Step 3: Update `summarize_session()`**

Add the latest non-terminal task:

```sql
SELECT id, status, harness_id
FROM tasks
WHERE session_id = ? AND status NOT IN ('completed', 'failed', 'canceled')
ORDER BY created_at DESC
LIMIT 1
```

Append `current_task: none` when there is no active task, otherwise `current_task: <id> <status> via <harness_id>`.

- [ ] **Step 4: Update CLI logs**

In `_format_logs()`, add sections for `tasks:`, `artifacts:`, and `harness_capabilities:` using existing line-oriented formatting. Include `task_id` in task event rows.

- [ ] **Step 5: Run inspection tests**

Run: `python -m unittest tests.test_control tests.test_cli_inspection -v`

Expected: PASS after updating expected strings.

- [ ] **Step 6: Commit**

```bash
git add src/innie/control.py src/innie/cli.py tests/test_control.py tests/test_cli_inspection.py
git commit -m "feat: show harness task state in status and logs"
```

## Task 8: Add Milestone 2 Acceptance Coverage

**Files:**
- Create: `tests/test_milestone2_acceptance.py`

- [ ] **Step 1: Write acceptance test**

```python
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from innie.db import connect, initialize_schema
from innie.harness import HarnessArtifact, HarnessEvent, ScriptedHarnessAdapter, TokenUsage
from innie.pipeline import accept_slack_event
from innie.runtime import SessionManager


class FakeSlack:
    def __init__(self) -> None:
        self.reactions: list[tuple[str, str, str]] = []
        self.messages: list[tuple[str, str, str]] = []

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        self.reactions.append((channel, timestamp, name))

    def post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        self.messages.append((channel, thread_ts, text))


def event() -> dict:
    return {
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "ts": "100.1",
            "text": "run the milestone 2 test",
        },
    }


class Milestone2AcceptanceTest(unittest.TestCase):
    def test_one_harness_adapter_streams_progress_output_usage_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "innie.db"
            db = connect(db_path)
            initialize_schema(db)
            slack = FakeSlack()
            accepted = accept_slack_event(db, event(), bot_user_id="U_BOT", slack=slack, harness_id="scripted")
            db.close()

            adapter = ScriptedHarnessAdapter(
                events=[
                    HarnessEvent(type="started"),
                    HarnessEvent(type="progress", message="checking repository"),
                    HarnessEvent(type="usage", usage=TokenUsage(input_tokens=20, output_tokens=5, cache_read_tokens=10)),
                    HarnessEvent(type="output", message="milestone 2 complete"),
                    HarnessEvent(type="completed"),
                ],
                artifacts=[HarnessArtifact(kind="summary", path=str(Path(tmp) / "summary.md"))],
            )
            manager = SessionManager(db_path, adapters={"scripted": adapter}, slack=slack, workspace=Path(tmp))
            try:
                asyncio.run(manager.run_until_idle())
                task_events = [
                    row["event_type"]
                    for row in manager.db.execute("SELECT event_type FROM task_events WHERE session_id = ?", (accepted.session.id,))
                ]
                artifact_count = manager.db.execute(
                    "SELECT COUNT(*) AS count FROM artifacts WHERE session_id = ?",
                    (accepted.session.id,),
                ).fetchone()["count"]
                capabilities = manager.db.execute(
                    "SELECT capabilities_json FROM harness_capabilities WHERE harness_id = 'scripted'"
                ).fetchone()
            finally:
                manager.close()

            self.assertIn(("D1", "100.1", "eyes"), slack.reactions)
            self.assertIn(("D1", "100.1", "Progress: checking repository"), slack.messages)
            self.assertIn(("D1", "100.1", "Done:\nmilestone 2 complete"), slack.messages)
            self.assertIn("harness.usage", task_events)
            self.assertIn("harness.completed", task_events)
            self.assertEqual(1, artifact_count)
            self.assertIn("supports_structured_artifacts", capabilities["capabilities_json"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run acceptance test**

Run: `python -m unittest tests.test_milestone2_acceptance -v`

Expected: PASS.

- [ ] **Step 3: Run full suite**

Run: `python -m unittest discover -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_milestone2_acceptance.py
git commit -m "test: cover milestone 2 harness progress loop"
```

## Self-Review

- Spec coverage: the plan covers one adapter first, normalized streaming events, autonomous execution, Slack progress/final output, safe progress rendering, artifact collection, and capability differences.
- Placeholder scan: no deferred edge-case placeholders or unspecified "write tests" steps remain.
- Type consistency: `TaskRecord`, `TaskRequest`, `HarnessEvent`, `HarnessCapabilities`, and `SlackProgressRenderer.render()` names are consistent across tasks.
