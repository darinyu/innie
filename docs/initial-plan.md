# Innie Initial Plan

## Summary

Innie is an open-source sidekick layer that sits between users and agent
harnesses. The human is the outie; the Slack bot is the innie for a software
system; the harness does the work behind that boundary. Innie exists to solve
one core problem: agent harnesses can do useful work, but they do not provide a
small, durable, Slack-native operating envelope for a user's software system.

The value proposition is:

> Give any software system an innie that can be reached from Slack, acknowledge
> work immediately, show progress, survive restarts, resume sessions, run
> schedules, and leave an observable task trail.

Innie provides the minimum product shell around agent execution: Slack-native
interaction, durable task state, a harness adapter, autonomous execution,
schedules, and observability.
It delegates the inner agent loop to existing harnesses such as Codex, Claude
Code, OpenCode, Goose, or future runtimes.

Innie should be recoverable by default. A process crash, deploy, laptop sleep,
container restart, or network interruption should not erase the task, recovery,
or observability state needed to understand and resume work.

The guiding principle is:

> Innie owns the operating envelope. The harness owns the agent loop.

Design principles:

- Solve the core problem first: make a user's software reachable from Slack and
  make the resulting work durable, visible, resumable, and observable.
- Keep Innie minimal: own the product envelope, not planning, tool policy,
  semantic memory, or model behavior.
- Make Innie easy to extend through stable hooks, adapter boundaries, and
  observable lifecycle events, while keeping the core runtime small.
- Prefer boring local durability before distributed infrastructure: SQLite,
  append-only events, clear timestamps, and explicit cleanup.
- Make every v0 feature prove the value proposition directly. If it does not
  improve Slack usability, recovery, schedules, or observability, move it later.

## Goals

- Provide a thin, generic sidekick layer for one user-owned innie per install.
- Treat Slack as the v0 outie interface: the human-facing place where users
  ask, interrupt, follow progress, and receive work.
- Make chat-triggered and scheduled agent work recoverable by default: task
  state, queued input, harness events, observability events, output
  destinations, and timestamps should survive restarts.
- Provide enough lifecycle hooks to customize the product experience without
  forking Innie, starting with Slack acknowledgment and progress rendering.
- Provide observability for operators and users: structured logs, task
  timelines, health checks, cost and token usage where available, and failure
  diagnostics.
- Provide convenient Slack bot onboarding, ideally through a one-time setup
  script that creates or configures the Slack app, stores local config, and
  verifies the bot can receive and send messages.
- Keep only the durable state needed to recover task execution. Long-term
  memory, profiles, preferences, and semantic recall belong to the harness until
  there is a concrete reason to standardize them.
- Run autonomous mode by default, using the harness's own permissions,
  sandboxing, and safety behavior.
- Start with one harness adapter. Add more adapters only after the first
  adapter proves the session, recovery, and observability model.
- Avoid becoming a full agent framework or replacing Codex, Claude Code, or
  other harnesses.

## Non-Goals

- Do not build a new ReAct loop.
- Do not build a proprietary model runtime.
- Do not require one canonical harness.
- Do not build a routing layer for multiple innies in the MVP.
- Do not build a policy engine in the MVP.
- Do not build long-term semantic memory in the MVP.
- Do not make approval mediation part of the default execution path.
- Do not emulate approvals with fragile prompt protocols in v0.
- Do not hide harness-specific capabilities when users deliberately choose
  them.
- Do not build every possible interface before Slack works end to end.
- Do not build the web operator console in v0.
- Do not require cloud hosting for the MVP.
- Do not let hooks replace Innie's durable state machine.
- Do not let hooks block core lifecycle progress indefinitely.

## Product Shape

```text
User
  Slack (v0 outie interface) / cron
    |
Innie
  durable session/task state, lifecycle hooks, autonomous execution,
  observability, schedules
    |
Harness adapter
  Codex / Claude Code / OpenCode / Goose / custom
    |
Workspace or sandbox
  repo, shell, tools, MCPs, secrets, browser
```

## Responsibilities

### Innie Owns

- Trigger ingestion from Slack and schedules for the single configured innie.
- Session and task lifecycle: create, queue, start, stream, pause, resume,
  cancel, retry, timeout, and archive.
- Durable state transitions: persist session state, task state, emitted events,
  artifacts, and recovery checkpoints before reporting progress.
- Minimal recovery state: enough context to restart, resume, or explain a task
  after interruption.
- Output context: where results should go.
- Autonomous execution by default: start the harness with configured runtime
  settings and observe the work without mediating every tool decision.
- Observability: structured logs, metrics, trace spans, task timelines, health
  checks, failure reports, and append-only task history.
- Lifecycle hooks: typed extension points for Slack acknowledgment, progress
  rendering, harness events, output delivery, schedules, and cleanup.
- Schedules: durable recurring triggers that start harness tasks at configured
  times.
- Harness adapters: a small compatibility layer for supported runtimes, starting
  with one adapter.

### Harnesses Own

- Planning, acting, observing, and revising.
- Code editing, test execution, and task-specific debugging.
- Context packing within a session.
- Harness-native tools, skills, plugins, and subagents.
- Harness-native self-improvement.
- Tool safety, sandboxing, permission settings, and approval decision points.
- Native approval flows, unless a future adapter exposes a real API and Innie
  deliberately opts into displaying and forwarding those requests.
- Long-term semantic memory, profiles, preferences, and retrieval.
- Producing task artifacts such as diffs, summaries, logs, and PR drafts.

## Adapter Contract

The first adapter contract should stay intentionally small:

```python
class HarnessAdapter(Protocol):
    async def start_task(self, request: TaskRequest) -> TaskHandle: ...
    async def send_input(self, task_id: str, input: UserInput) -> None: ...
    async def cancel_task(self, task_id: str) -> None: ...
    async def stream_events(self, task_id: str) -> AsyncIterator[HarnessEvent]: ...
    async def collect_artifacts(self, task_id: str) -> list[Artifact]: ...
```

The request object should be recovery-friendly but harness-neutral:

```python
@dataclass(frozen=True)
class TaskRequest:
    goal: str
    trigger: TriggerContext
    workspace: WorkspaceRef
    output: OutputTarget
    recovery: RecoveryContext
```

Adapters may expose optional capability metadata:

```python
@dataclass(frozen=True)
class HarnessCapabilities:
    supports_streaming: bool
    supports_resume: bool
    supports_structured_artifacts: bool
    supports_native_approval: bool
    supports_autonomous_mode: bool
    supports_subagents: bool
```

Capabilities are descriptive, not requirements. V0 succeeds with streaming,
cancel, autonomous mode, and either resume support or an explicit
`fresh_context` recovery path.

Harness events should include normalized usage metadata when the harness
provides it:

```python
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
```

Cache hit rate is an observability metric, not a core optimization loop. Innie
should record and display it, but prompt packing and cache optimization belong
inside harnesses or harness-specific adapters.

## Lifecycle Hooks

Innie should provide a small, typed hook system so installs can customize the
product experience without changing the core runtime. This is inspired by
OpenClaw-style extension points: the core owns the state machine, while hooks
customize behavior at stable lifecycle moments.

Hooks should be async Python callables in v0. Later they can also be external
commands or webhooks. The v0 hook set should be intentionally small: hooks
exist to make Slack behavior and observability customizable, not to become a
plugin platform.

```python
class LifecycleHook(Protocol):
    async def __call__(self, event: InnieLifecycleEvent, ctx: HookContext) -> HookResult: ...


@dataclass(frozen=True)
class HookContext:
    store: TaskStore
    slack: SlackActions | None
    logger: Logger
    config: Mapping[str, Any]


@dataclass(frozen=True)
class HookResult:
    status: Literal["ok", "skipped", "failed"]
    message: str | None = None
```

Hook rules:

- Hooks receive typed event payloads and helper actions; they should not mutate
  in-memory session actor state directly.
- Hooks may perform side effects such as Slack reactions, Slack messages,
  metrics, traces, notifications, or custom artifact writes.
- Innie persists the lifecycle event before running hooks when the event affects
  recovery.
- Hook results and failures are recorded as observability events.
- Hooks have timeouts. A slow hook should not stall Slack intake, session
  routing, harness execution, or cleanup indefinitely.
- Hooks are ordered by configuration. By default, failures are logged and do not
  stop the lifecycle unless a hook is explicitly marked `required`.
- Hooks must be idempotency-aware because Slack retries and process restarts can
  invoke the same lifecycle point more than once.
- Hooks should receive stable ids: `event_id`, `session_id`, `task_id`, and
  Slack `channel_id`/`thread_ts` when available.

V0 hook points:

- `trigger.accepted`: Innie accepted the trigger and will create or resume a
  session.
- `session.created`: durable session row created.
- `session.resuming`: dormant or interrupted session is being rehydrated.
- `session.dormant`: live actor was evicted while durable state remains.
- `session.input_queued`: input was persisted into the session inbox.
- `task.created`: task row created.
- `task.started`: harness turn is about to start.
- `harness.event`: normalized harness event was received.
- `task.output_ready`: final output or artifact is ready to deliver.
- `task.completed`: task completed successfully.
- `task.failed`: task failed.
- `task.canceled`: user or runtime canceled the task.
- `schedule.due`: schedule fired and is about to create a session or task.
- `cleanup.finished`: cleanup completed.

Future hook points can add `trigger.received`, `trigger.rejected`,
`session.closed`, native approval events, and more detailed cleanup lifecycle
when real installs need them.

Slack default hooks should be implemented as normal hooks, not hardcoded into
the session manager:

```yaml
hooks:
  trigger.accepted:
    - id: slack-eyes
      kind: builtin
      name: slack_ack_started
      timeout_ms: 2000
      required: false
      config:
        mode: reaction
        emoji: eyes
        threaded: false
```

For the v0 Slack experience, `slack_ack_started` should react to the original
Slack event with `:eyes:` or post an unthreaded acknowledgment near the root
message. This tells the outie that Innie has started looking before the harness
has produced meaningful output.

## Initial Interfaces

### Slack

Slack is the v0 outie interface. It is where humans interact with the innie:
they ask for work, interrupt running tasks, follow progress, and receive
results. Other interfaces should be possible later, but the first product
experience should prove that every software system can have a useful
Slack-native work self.

Onboarding should be as close to one command as possible. The v0 setup path can
be a one-time script that asks for the minimum Slack credentials, writes
`.innie/config.yaml`, verifies the bot identity, and sends a test message.

Expected behavior:

- Messages that tag the configured user are supported from day one.
- A configured-user mention creates a session and its first task.
- A configurable `slack.event_received` hook can acknowledge accepted messages
  before the session starts. The default hook should post an unthreaded
  `:eyes:` reaction or message at the Slack root event so the outie knows Innie
  started looking at it.
- The session streams visible progress back to the thread.
- Users can ask for status or cancel a task.
- In v0, tasks run autonomously using the harness adapter's configured runtime
  mode.
- Later, native harness approval requests can be shown in-thread when the
  selected adapter supports them.
- The install represents one user's innie. Teams should run multiple user-owned
  innies instead of one shared team innie.

## Concurrency And Sessions

Concurrency is the core design constraint. Innie should treat each Slack thread,
scheduled run, or future interface conversation as a durable
`session`. A session is the unit users understand, the unit the future web
console groups by, and the unit Innie can recover after restart.

The v0 rule:

> Many sessions can run at the same time. One session can run only one harness
> turn at a time.

This avoids the failure mode where two user messages in the same Slack thread
start two independent Codex or Claude Code processes that edit the same
workspace, post competing answers, or corrupt each other's context.

Session identity should be explicit and Slack-native:

- A configured-user mention starts a new session rooted at the mention message.
- Replies in an existing Innie thread map back to the same session.
- A scheduled run creates a session with `trigger_type = schedule` and an
  output target such as a configured Slack channel or thread.
- There is still only one configured, user-owned innie per install. This is
  session lookup, not routing across multiple innies or team bots.

The asyncio implementation should use an actor model:

- One process owns one asyncio event loop.
- A manager task ingests Slack events, schedule ticks, CLI commands, and harness
  lifecycle events.
- Each live session has one `asyncio.Task` and one `asyncio.Queue` inbox.
- External events never call a harness adapter directly. They become durable
  session events and then inbox messages for the session actor.
- The session actor is the only code path that starts harness turns, sends
  follow-up input to an active harness, cancels a harness turn, or mutates the
  in-memory session state.
- The runtime session registry is only a reconstructable cache of live actors.
  SQLite is the source of truth.

The session actor should have a small state machine:

```text
idle -> executing -> idle
idle -> dormant
dormant -> resuming -> executing
resuming -> idle
idle -> executing -> canceling -> interrupted
idle -> executing -> failed
idle -> executing -> completed
interrupted -> executing
```

Queued messages are drained only at safe points:

- When the session is `idle`.
- Immediately after a harness turn finishes.
- Never by starting a second harness turn while the session is already
  `executing`.

For v0, the busy-session behavior should stay simple:

- Follow-up messages in a busy session are persisted and queued.
- Explicit cancel requests are allowed and mapped to the adapter's cancel or
  interrupt operation when supported.
- Innie does not classify every follow-up as interrupt-vs-batch in v0.
- Innie does not inject mid-turn steering unless a future adapter exposes a
  reliable native input channel for it.

Idle sessions should be canceled and resumed:

- If a session has no active harness turn, no queued input, and no recent user
  activity, Innie should close the live session actor and cancel or disconnect
  its harness runtime after `idle_session_ttl`. A reasonable local default is
  5 minutes.
- Innie should also have a `hard_live_session_ttl` for live actors that have
  stayed alive too long even with periodic activity. A reasonable local default
  is 30 minutes.
- This is a runtime eviction, not data deletion. The durable `sessions`, `tasks`,
  `task_events`, `session_inbox`, artifacts, output target, and harness resume
  identifiers remain in SQLite.
- The session state becomes `dormant`, `last_active_at` is preserved, and
  `dormant_at` records when the live actor was evicted.
- The next Slack reply, status request, cancel request, or scheduled continuation
  rehydrates the session actor from SQLite.
- If the harness adapter supports resume, Innie resumes the same harness
  conversation using the stored resume identifier.
- If the harness adapter does not support resume, Innie starts a fresh harness
  turn with the stored recovery context and marks the task timeline with
  `resume_mode = fresh_context`.
- Idle cancellation must never happen while the session is `executing`,
  `canceling`, or holding queued input.

This is deliberately simpler than full personal-assistant runtimes. Richer
active/passive session behavior, watched conversations, and optional mid-turn
intervention can stay outside the MVP. Innie should keep the concurrency
invariant without absorbing a larger product model.

The manager should also enforce per-user install bounds:

- `max_live_sessions`: maximum hydrated session actors.
- `max_session_queue_depth`: maximum queued user messages per session before
  asking the outie to wait or cancel.
- `idle_session_ttl`: idle time before closing a live actor and harness runtime
  while preserving durable state.
- `hard_live_session_ttl`: maximum live actor age before forced runtime
  rotation.

These bounds prevent one user's Slack activity or schedules from exhausting the
host. V0 should not add a separate global `max_active_harness_turns`; each
session already has at most one active harness turn, and `max_live_sessions`
plus idle reaping is enough for a personal install.

Important asyncio rules:

- Keep session creation atomic under the event loop: do the check-and-register
  sequence without an `await` between lookup and insertion.
- Avoid locks for single-event-loop state if a no-`await` critical section is
  enough; use SQLite transactions for durable state.
- Remove a session actor from the runtime registry before awaiting its close, so
  new messages do not get delivered to a half-closed actor.
- Any stream fan-in helper must cancel and then await feeder tasks during
  shutdown. Otherwise old Slack, harness, or queue reader tasks can leak and
  post late events after the session is already closed.
- Blocking harness operations must use async subprocess APIs, async SDKs, or
  `asyncio.to_thread`; they must not block the event loop.

### CLI

The CLI is the first developer and admin interface. It should exist in v0 for
local testing, setup, debugging, and automation, but it is not the primary
outie experience.

Example commands:

```bash
innie run "inspect this repo and suggest the next implementation step"
innie status <session-id>
innie logs <session-id>
innie cancel <session-id>
```

### Web Operator Console

The webapp is a future phase, not v0. Its purpose is inspection and recovery,
not replacing Slack as the outie interface.

The console should read from SQLite and provide native groupings over sessions:

- Active sessions.
- Waiting sessions.
- Interrupted sessions.
- Failed sessions.
- Completed sessions.
- Scheduled sessions.
- Sessions grouped by harness.
- Sessions grouped by Slack thread or output target.

It should also show Innie status:

- Slack connectivity.
- Harness adapter availability.
- Task store health.
- Worker liveness.
- Schedule runner status.
- Last cleanup run.
- Recent failures.
- Database and artifact storage size.

The webapp should be read-only by default. Recovery actions such as retry,
cancel, cleanup, or resume can be added later behind explicit buttons.

## Recovery State Layout

The MVP should use SQLite for durable recovery state. SQLite is lightweight,
local, easy to back up, easy to query, and gives Innie indexes and timestamps
without building a pile of ad hoc JSONL scanners. Innie should not maintain
long-term memory in the MVP; it should maintain only the recovery state required
to resume or explain tasks.

```text
.innie/
  config.yaml
  innie.db
  artifacts/
    <session-id>/
      <task-id>/
  schedules/
    schedules.json   # optional export/import format; source of truth is SQLite
```

The harness may keep its own memory. Innie should only store recovery state:
session identity, task request, output target, queued inputs, last known state,
checkpoints, artifacts, and observability events.

The core tables should all include `created_at` and `updated_at` where
applicable. Append-only event tables should include `created_at`. Any row that
may be cleaned up later should also include enough timestamp data to support
retention jobs without reading task payloads.

Suggested SQLite shape:

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  slack_channel_id TEXT,
  slack_thread_ts TEXT,
  schedule_id TEXT,
  harness_id TEXT NOT NULL,
  harness_resume_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_active_at TEXT,
  dormant_at TEXT,
  dormant_reason TEXT,
  completed_at TEXT
);

CREATE UNIQUE INDEX idx_sessions_slack_thread
  ON sessions(slack_channel_id, slack_thread_ts)
  WHERE slack_channel_id IS NOT NULL AND slack_thread_ts IS NOT NULL;

CREATE INDEX idx_sessions_status_updated_at
  ON sessions(status, updated_at);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,
  goal TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  output_target TEXT NOT NULL,
  harness_id TEXT NOT NULL,
  execution_mode TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX idx_tasks_status_updated_at ON tasks(status, updated_at);
CREATE INDEX idx_tasks_session_created_at ON tasks(session_id, created_at);
CREATE INDEX idx_tasks_completed_at ON tasks(completed_at);

CREATE TABLE task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE INDEX idx_task_events_task_created_at ON task_events(task_id, created_at);
CREATE INDEX idx_task_events_session_created_at ON task_events(session_id, created_at);
CREATE INDEX idx_task_events_created_at ON task_events(created_at);

CREATE TABLE hook_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lifecycle_event_id TEXT NOT NULL,
  hook_id TEXT NOT NULL,
  hook_point TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_hook_events_lifecycle_event_id
  ON hook_events(lifecycle_event_id);

CREATE INDEX idx_hook_events_hook_point_created_at
  ON hook_events(hook_point, created_at);

CREATE TABLE session_inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,
  source_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  claimed_at TEXT,
  processed_at TEXT,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX idx_session_inbox_session_status_created_at
  ON session_inbox(session_id, status, created_at);

CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE INDEX idx_artifacts_session_created_at ON artifacts(session_id, created_at);
CREATE INDEX idx_artifacts_task_created_at ON artifacts(task_id, created_at);

CREATE TABLE schedules (
  id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL,
  cron TEXT NOT NULL,
  goal TEXT NOT NULL,
  output_target TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_run_at TEXT,
  next_run_at TEXT
);

CREATE INDEX idx_schedules_enabled_next_run_at
  ON schedules(enabled, next_run_at);
```

## Recoverability Model

Innie should behave like a durable session runner even when the first
implementation is a local process.

- Every session receives a stable session id before any harness starts.
- Every task receives a stable task id before any harness starts.
- Every Slack follow-up, schedule tick, and cancel is recorded before it is
  delivered to a session actor.
- Every task transition is inserted into `task_events`.
- Lifecycle hook attempts are inserted into `hook_events`.
- The latest session state is stored in `sessions`.
- The latest task state is stored in `tasks`.
- Harness output is streamed as events and periodically checkpointed.
- Harness resume identifiers are stored on the session when the adapter exposes
  them.
- Every session, task, event, queued input, schedule, and artifact record has
  timestamps.
- Idle sessions can be evicted from memory by closing the actor and harness
  runtime while keeping the durable session in `dormant` state.
- Rehydrating a dormant session should be a normal path, not an exceptional
  recovery path.
- Short runtime reaping is separate from long-term cleanup. `idle_session_ttl`
  and `hard_live_session_ttl` reclaim local resources; cleanup commands decide
  when old completed or failed records are deleted.
- On restart, Innie scans session state and either resumes, marks interrupted,
  or asks the outie how to proceed.
- Sessions that were `executing` during a crash should become `interrupted`
  unless the selected harness adapter has a verified resume capability.
- Queued inputs for interrupted sessions remain durable and visible; Innie
  should not silently drop them.

This model should make it possible to swap SQLite for Postgres, S3-backed
artifacts, or a workflow engine later without changing the harness adapter
contract.

## Cleanup And Retention

Cleanup should be cheap because every recoverability table is timestamped and
indexed.

The MVP should support conservative cleanup commands:

```bash
innie cleanup --completed-before 30d
innie cleanup --failed-before 90d
innie cleanup --artifacts-before 30d
innie cleanup --dry-run
```

Cleanup rules:

- Never delete running or waiting sessions.
- Prefer deleting large artifact files before deleting task/event rows.
- Keep enough task metadata to explain that a task happened, even if detailed
  artifacts are removed.
- Always support `--dry-run`.
- Record cleanup itself as an observability event.

## Optional Workflow Backend

Temporal is an optional backend for long-running and recoverable work, but it
should not be required for the core open-source MVP. SQLite plus a local
asyncio runner should be the default until real usage proves that local
durability is not enough.

Use Temporal when an install needs:

- Multi-hour or multi-day tasks.
- Durable timers and schedules.
- Retries across process restarts.
- Visibility into workflow history.
- Horizontal workers and stronger operational guarantees.

Keep the core abstraction independent:

```python
class TaskStore(Protocol):
    async def create_session(self, request: SessionRequest) -> SessionRecord: ...
    async def create_task(self, session_id: str, request: TaskRequest) -> TaskRecord: ...
    async def append_event(self, session_id: str, task_id: str, event: InnieEvent) -> None: ...
    async def enqueue_session_input(self, session_id: str, input: SessionInput) -> None: ...
    async def update_session_state(self, session_id: str, state: SessionState) -> None: ...
    async def update_task_state(self, task_id: str, state: TaskState) -> None: ...
    async def get_session(self, session_id: str) -> SessionRecord: ...
    async def list_recoverable_sessions(self) -> list[SessionRecord]: ...
    async def cleanup(self, request: CleanupRequest) -> CleanupResult: ...


class WorkflowBackend(Protocol):
    async def start(self, session_id: str, task_id: str) -> None: ...
    async def signal(self, session_id: str, signal: WorkflowSignal) -> None: ...
    async def cancel(self, session_id: str) -> None: ...
```

The default backend can be a local durable queue. A Temporal backend can
implement the same interface for production installs without making Temporal a
hard dependency.

## Observability Model

Observability is part of the product, not an afterthought. Users and operators
should be able to answer:

- What is this innie doing right now?
- Why is this task waiting?
- Which harness and model were involved?
- What did the task cost?
- What failed, and can it be resumed?
- Did the task run autonomously?
- What output was delivered to the outie?

The MVP should emit structured local events first and leave room for OpenTelemetry
export later:

- Task timeline events for Slack and CLI status.
- Structured logs for service debugging.
- Metrics for task count, duration, failures, token usage, cache hit rate, and
  cost when the adapter exposes them.
- Trace spans around trigger handling, task store writes, harness calls,
  schedules, and output delivery.
- Health checks for Slack connectivity, task store access, harness availability,
  and worker liveness.

The progress renderer is part of observability. It should render structured
runtime events such as "started", "using tool", "running command", "waiting",
"queued follow-up", and "completed". It should not expose private chain of
thought, and it should not depend on the agent loop choosing to narrate every
step.

## Execution Model

Autonomous mode is the default. Innie starts the selected harness with the
adapter's configured runtime settings, streams events back to Slack, persists
recovery state, and records observability data. The harness is responsible for
its own permissions, sandboxing, and safety behavior.

Native approval mode is later work. Innie should support it only when a harness
adapter exposes a real approval API. The future shape is simple: adapter emits a
native approval request, Innie persists and renders it in Slack, the outie
chooses, Innie persists the decision, and the adapter forwards it back to the
harness. Innie should not fake this with prompt-only conventions in v0.

## Schedule Model

Schedules are first-class because the innie should be able to work without a
fresh human prompt every time.

The first implementation can support one simple durable schedule path:

```json
[
  {
    "id": "daily-summary",
    "enabled": true,
    "cron": "0 17 * * 1-5",
    "goal": "Prepare a short end-of-day summary for this software system.",
    "output": {
      "type": "slack-thread",
      "channel": "configured-default-channel"
    }
  }
]
```

Scheduled tasks should create normal task records, emit normal observability
events, and recover after restart.

## MVP Milestones

### Milestone 1: Slack To Durable Session

Goal: prove the smallest useful product loop before adding real harness work.
A Slack message tagging the configured user should become a durable session,
acknowledge the user immediately, preserve follow-ups in order, and expose
enough local state to debug what happened.

Task 1: Local workspace bootstrap.

- Goal: make every install self-contained and inspectable.
- Provide a low-dependency install script so a cloned checkout can add `innie`
  to the user's command line without downloading a Python build backend.
- Build `innie init` to check local dependencies before creating runtime state:
  Python 3.10+, SQLite 3, Slack configuration availability, and at least one
  supported harness command such as Codex CLI, Claude Code, OpenCode, or Goose.
- If a dependency is missing, explain what is missing and ask the user before
  attempting any install, download, package-manager command, or config write.
- Keep the dependency check shell-agnostic: use Python subprocess execution,
  direct executable lookup, and structured prompts instead of shell-specific
  snippets, aliases, profiles, or `source` instructions.
- After dependencies are accepted or intentionally skipped, create `.innie/`,
  `.innie/config.yaml`, `.innie/innie.db`, and `.innie/artifacts/`.
- Initialize SQLite schema for sessions, task events, inbox rows, hook events,
  and artifacts.
- By default, continue from `innie init` into the Slack setup wizard. Provide an
  explicit skip flag only for tests and local state-only debugging.
- Done when a fresh clone can run one command and produce a valid local Innie
  command, then run `innie init` to create recoverable local state and start
  Slack setup, with clear dependency status and no dependency installation
  unless the user explicitly approved it.

Task 2: Slack app setup wizard.

- Goal: make Slack onboarding convenient enough for a new user to complete
  without reading Slack app docs.
- Build `innie slack setup` as a guided wizard that keeps the implementation
  independent and open-source friendly.
- Start by checking for existing Slack config and tokens. If complete config
  exists, validate it with Slack `auth.test` and ask before refreshing or
  replacing it.
- Generate an Innie Slack app manifest from a minimal template:
  - display name and app name chosen by the user.
  - Socket Mode enabled.
  - bot events for `message.channels` and `message.groups`.
  - bot scopes for receiving channel messages and posting replies/reactions,
    starting with the smallest useful set.
- Ask for a one-time Slack App Configuration token only if the user wants Innie
  to create or update the app manifest automatically. Do not store this token.
- If automatic app creation is enabled, call Slack manifest APIs to create the
  app from the generated manifest. If not, print or write the manifest so the
  user can paste it into Slack manually.
- Run the Slack OAuth install flow with both local and cloud-friendly modes:
  - local mode can start a callback server on a clearly documented localhost
    port, check the port first, and ask before killing any process that occupies
    it.
  - cloud or remote-dev mode should not assume Slack can reach the machine
    running Innie. If the user has a public callback URL, register and use it.
    Otherwise, register a localhost redirect URL, show the authorization URL,
    ask the user to complete it in their browser, and let them paste back the
    final callback URL or the `code` query parameter from the browser.
  - the wizard should explain exactly what to copy back, where to paste it, why
    the callback page may fail to load in remote-dev mode, and how long the code
    remains useful.
- Exchange the OAuth code for a bot token. If user-token support is added later,
  make it optional and explain why it is needed before requesting user scopes.
- Guide the user through creating an app-level Socket Mode token with
  `connections:write`. This may remain a manual Slack UI step; validate that the
  pasted token has the expected `xapp-` shape before storing it.
- Store bot and app-level tokens in a local secret store or files with
  restrictive permissions. Store non-secret app metadata in `.innie/config.yaml`.
- Validate the final setup before returning success:
  - bot token passes `auth.test`.
  - app-level token can open a Socket Mode connection.
  - bot can post or react to a test message.
  - configured events are received through Socket Mode.
- Done when setup can confirm "bot can receive events" and "bot can send or
  react" before the runtime starts, without requiring the user to manually
  discover Slack scopes, event subscriptions, or token types.

Task 3: Slack event intake.

- Goal: turn Slack messages that tag the configured user into normalized
  triggers for the single user-owned innie.
- Support configured-user mentions from day one.
- Ignore self-echoes, retries, unsupported event types, and messages not meant
  for the configured innie.
- Persist the accepted trigger before any visible response.
- Done when test events produce deterministic trigger records and rejected
  events explain why they were ignored.

Task 4: Immediate Slack acknowledgment.

- Goal: make the outie see that Innie started looking before any agent work
  exists.
- Add the minimal lifecycle hook runner.
- Implement the default `trigger.accepted` hook that posts or reacts with
  `:eyes:` at the Slack root message.
- Record hook attempts, duration, and failures as observability events.
- Done when every accepted Slack trigger gets one idempotent acknowledgment and
  duplicate Slack retries do not create duplicate reactions or messages.

Task 5: Durable session identity.

- Goal: make Slack threads map to recoverable Innie sessions.
- Create one durable session per Slack root message.
- Map replies in an existing Innie thread back to the same session.
- Store Slack channel id, root/thread ts, trigger type, output target, status,
  timestamps, and configured harness id.
- Done when the same Slack thread always resolves to the same session after
  process restart.

Task 6: Durable inbox and ordering.

- Goal: make concurrent Slack activity safe before any real harness adapter is
  added.
- Persist every accepted message into `session_inbox` before handing it to an
  in-memory actor.
- Preserve ordering within one session while allowing different sessions to be
  active concurrently.
- Keep busy-session follow-ups queued instead of starting a second turn.
- Done when two Slack threads can progress independently and two messages in the
  same thread are processed in stored order.

Task 7: Session actor skeleton.

- Goal: establish the asyncio concurrency shape without depending on Codex,
  Claude Code, or any external harness.
- Add one manager task and one live actor per hydrated session.
- Actors should read durable inbox rows, transition session state, append task
  events, and emit placeholder output.
- The runtime registry is only a reconstructable cache; SQLite remains the
  source of truth.
- Done when killing and restarting the process can rehydrate non-terminal
  sessions and continue from the stored inbox.

Task 8: Minimal Slack status and cancel.

- Goal: give the outie basic control over a session even before full harness
  execution exists.
- Support status requests that summarize session state, queued inputs, last
  event, and output target.
- Support cancel requests that mark the session or current placeholder task as
  canceled and stop the live actor safely.
- Done when a user can ask "status" or "cancel" in the Slack thread and see a
  deterministic response backed by SQLite state.

Task 9: Local inspection CLI.

- Goal: make development and debugging possible without a web console.
- Provide minimal commands: `innie status <session-id>`,
  `innie logs <session-id>`, and `innie cancel <session-id>`.
- Read from SQLite and display session state, inbox rows, task events, hook
  events, and latest output target.
- Done when a developer can debug a Slack-triggered session entirely from local
  CLI output.

Task 10: Milestone 1 acceptance test.

- Goal: prove the Slack-to-durable-session loop end to end.
- Use a fake Slack event fixture and a local SQLite database.
- Verify accepted trigger persistence, `:eyes:` acknowledgment hook execution,
  session creation, inbox ordering, placeholder task events, status, cancel, and
  restart rehydration.
- Done when the test passes without network access or a real harness.

### Milestone 2: One Harness Adapter And Progress

- Add one harness adapter first.
- Normalize the minimum streaming events needed for progress, output,
  completion, failure, cancellation, usage, and artifact collection.
- Run autonomous mode by default using the adapter's configured runtime
  settings.
- Stream visible progress and final output to Slack threads.
- Render progress from Innie lifecycle and harness events, not private chain of
  thought.
- Collect basic artifacts such as summaries, diffs, and command logs when the
  adapter exposes them.
- Record the adapter's real capability differences instead of pretending every
  harness behaves the same.

### Milestone 3: Concurrent Sessions And Recovery

Milestone 3 should use a producer/consumer queue model rather than treating
`max_live_sessions` as the main capacity limit.

Task 1: Add Durable Worker And Lock State.

Goal: Represent worker ownership, session leases, and recoverable in-flight work
in SQLite before changing runtime behavior.

- Add a runner `run_id` concept and worker ids such as `worker-1`.
- Add durable session lease fields: `locked_by`, `locked_at`,
  `lock_expires_at`.
- Add enough inbox/task status values to distinguish `queued`, `processing`,
  `done`, `failed`, `canceled`, and `interrupted`.
- Keep locks as leases, not permanent booleans. A lock is active only when
  `locked_by` is set and `lock_expires_at` is in the future.
- Acceptance: schema migration is backward compatible and existing tests still
  pass.

Task 2: Split Slack Intake From Work Execution.

Goal: Make Slack intake a producer that persists accepted requests and returns to
listening without waiting for harness capacity.

- Keep event acceptance, session resolution, trigger persistence, and inbox
  enqueueing in the intake path.
- Remove the assumption that a received Slack event must immediately drain that
  session's inbox.
- Wake the worker pool after enqueueing work, but keep the queued request durable
  even if no worker is currently available.
- Acceptance: `innie run` can accept multiple Slack events quickly while workers
  are busy, and every accepted event appears in `session_inbox`.

Task 3: Implement Global Fair Queue Claiming.

Goal: Let workers claim the oldest eligible queued request without letting one
busy session block unrelated sessions.

- Add a global claim function that finds the oldest queued inbox row whose
  session is not actively locked.
- Atomically claim the inbox row and session lease in one short SQLite
  transaction.
- If the oldest queued row belongs to a locked session, skip it and claim the
  next eligible row.
- Never hold an open transaction while awaiting Codex, Slack, socket reads, or
  any external call.
- Acceptance: an old locked-session row does not block a newer unrelated queued
  row.

Task 4: Add The Async Worker Pool.

Goal: Replace the per-session actor model with a fixed producer/consumer worker
pool owned by `innie run`.

- Run up to `max_workers` asyncio worker loops in the single `innie run`
  process. Do not use Python threads or Python multiprocessing for this
  milestone.
- Use `max_workers` as the capacity limit for active harness work. Durable
  sessions may outnumber live workers.
- Keep worker loops alive while `innie run` is running. Idle workers should wait
  on an in-process wake signal, sleep, or back off when no eligible work exists.
- Start a harness subprocess only while a worker is actively running a claimed
  task.
- Acceptance: `max_workers=2` processes two different sessions concurrently and
  `max_workers=1` drains queued work deterministically.

Task 5: Preserve Per-Session Ordering.

Goal: Ensure only one active harness turn can run for a session while unrelated
sessions can continue.

- A worker creates or resumes the target session only while processing its
  claimed request.
- Same-session follow-ups remain queued while the session lease is active.
- After the harness turn finishes, fails, or is canceled, release the session
  lease and let any worker claim the next eligible request.
- Acceptance: same-session messages process sequentially, while different
  sessions can run concurrently up to `max_workers`.

Task 6: Add Lease Renewal And Startup Recovery.

Goal: Prevent permanent SQLite locks after crashes and make interrupted work
recoverable.

- Renew `lock_expires_at` periodically while a worker is running a harness task.
- On startup, clear stale leases from previous/dead runs before workers claim new
  work.
- Mark tasks left in `running` as `interrupted`.
- Move inbox rows left in `processing` back to `queued` or mark them
  `interrupted`, depending on whether the adapter can safely resume.
- Store harness resume identifiers where the adapter exposes them.
- Acceptance: killing `innie run` during active work does not permanently lock
  the session, and the next run explains the recovery decision in logs.

Task 7: Make Failures Graceful By Default.

Goal: Keep `innie run` alive for unrelated work whenever an individual task,
worker, harness, Slack call, or log write fails.

- Wrap worker task execution so exceptions mark the affected task failed or
  interrupted, release the session lease, and persist the error context.
- Treat Slack progress/final delivery failures as non-fatal task events.
- On shutdown signals or KeyboardInterrupt, stop accepting new events, let
  in-flight workers finish when practical, interrupt remaining work after a
  bounded grace period, release leases, and print a clear terminal summary.
- Acceptance: a worker failure does not crash the whole runner when unrelated
  work can continue, and graceful shutdown releases leases and leaves queued work
  recoverable.

Task 8: Add Worker Debug Logging.

Goal: Make concurrency and recovery bugs debuggable from terminal output and
`innie logs`.

- Record structured worker lifecycle events: worker start/stop, idle, claim
  attempt, claim success, skipped locked session, harness start, harness finish,
  lock renewal, lock release, and startup recovery action.
- Include `run_id`, `worker_id`, `session_id`, `inbox_id`, `task_id`,
  `harness_id`, `lock_expires_at`, and status transition wherever applicable.
- Persist worker/recovery events to SQLite in addition to terminal output so
  `innie logs <session>` can explain what happened after terminal scrollback is
  gone.
- Acceptance: worker claim/release/recovery events are visible in `innie logs`.

Task 9: Add Compact Worker Health Output.

Goal: Give operators liveness and capacity feedback without noisy per-worker
logs.

- In `--verbose`, print only the compact worker heartbeat on startup,
  claim/release, and about every 30 seconds:
  `workers: total=7 idle=6 running=1 queued=4 locked_sessions=1`
- Keep detailed worker lifecycle events in logs rather than flooding stdout.
- Acceptance: verbose mode emits the compact heartbeat with accurate total,
  idle, running, queued, and locked session counts.

Task 10: Add Status And Cancel Semantics.

Goal: Make user controls work consistently across queued, running, completed,
and interrupted requests.

- Support status inspection for queued, processing, running, completed, failed,
  canceled, and interrupted work.
- Support canceling queued requests before they are claimed.
- Support canceling or interrupting running requests through the adapter when
  possible, otherwise mark the request for cancellation and stop at the next safe
  point.
- Acceptance: status and cancel interactions report the correct state and do not
  corrupt session ordering.

Task 11: Run The Milestone 3 E2E Pass.

Goal: Verify the producer/consumer runtime with Slack and the first adapter
before adding more adapters.

- Test `max_workers=2` with two different Slack sessions and confirm concurrent
  harness work.
- Test multiple messages in one Slack thread and confirm same-session ordering.
- Test a locked old session plus a newer unrelated request and confirm the newer
  request runs.
- Test crash/restart recovery for stale leases and interrupted work.
- Test Slack final/progress delivery failure handling without crashing the
  runner.
- Acceptance: the E2E pass demonstrates no permanent locks, no unrelated-session
  starvation, graceful failure behavior, and enough logs to debug failures.

### Milestone 4: Cleanup And Retention

Milestone 4 should be a small, safe cleanup command. Keep v1 conservative:
clean only old completed local state, dry run by default, and never touch active
or recoverable work.

Task 1: Define The V1 Cleanup Rule.

Goal: Make the cleanup behavior simple enough to trust.

- Files:
  - Modify: `docs/initial-plan.md`
  - Modify: `src/innie/cli.py`
- V1 only considers completed tasks older than 30 days.
- V1 deletes only local SQLite rows and artifact files under `.innie/`.
- V1 does not delete Slack messages, harness workspace files, failed tasks,
  interrupted tasks, queued work, running work, locked sessions, or run logs.
- Acceptance: `innie cleanup --help` says dry run is default, `--apply` is
  required to delete, and v1 only cleans completed tasks older than 30 days.

Task 2: Add A Read-Only Cleanup Preview.

Goal: Show what cleanup would remove without changing anything.

- Files:
  - Create: `src/innie/cleanup.py`
  - Test: `tests/test_cleanup.py`
- Find completed tasks with `completed_at` older than 30 days.
- Include related `task_events` and artifacts for those tasks.
- Include artifact files only if their paths are inside `.innie/`.
- Return a small preview: task count, event count, artifact count, byte estimate,
  and task ids.
- Acceptance: preview finds old completed tasks and ignores recent, failed,
  interrupted, queued, running, and locked work.

Task 3: Add `innie cleanup` Dry Run.

Goal: Let users run cleanup safely as a no-op first.

- Files:
  - Modify: `src/innie/cli.py`
  - Modify: `src/innie/cleanup.py`
  - Test: `tests/test_cli_cleanup.py`
- Add `innie cleanup` command.
- Dry run is the default.
- Print the preview from Task 2.
- Do not write to SQLite and do not delete files.
- Acceptance: `innie cleanup` changes nothing and prints what `--apply` would
  remove.

Task 4: Add `innie cleanup --apply`.

Goal: Delete only the exact local state shown by the preview.

- Files:
  - Modify: `src/innie/cleanup.py`
  - Modify: `src/innie/cli.py`
  - Test: `tests/test_cleanup.py`
  - Test: `tests/test_cli_cleanup.py`
- Require explicit `--apply`.
- Recompute the preview immediately before deleting.
- Delete eligible `.innie/` artifact files.
- Delete related artifact rows, task events, and task rows.
- Leave the session row in place for now, with no task history for the cleaned
  task. Session deletion can be a later improvement.
- Treat missing artifact files as already clean.
- Acceptance: apply removes old completed task data and leaves active,
  recoverable, recent, failed, and interrupted work untouched.

Task 5: Show Cleanup Results In Logs.

Goal: Make cleanup visible without adding a full observability system.

- Files:
  - Modify: `src/innie/cleanup.py`
  - Modify: `src/innie/cli.py`
  - Test: `tests/test_cli_inspection.py`
- Record one `task_events` row per affected session when `--apply` deletes data:
  `event_type='cleanup.applied'`.
- Include the deleted task ids and counts in the payload.
- `innie logs <session>` already prints task events, so no new UI surface is
  needed.
- Acceptance: after apply, `innie logs <session>` shows the cleanup event.

Task 6: Run Cleanup E2E.

Goal: Verify cleanup is safe on realistic local state.

- Files:
  - Test: `tests/test_milestone4_acceptance.py`
- Create old completed, recent completed, failed, interrupted, queued, and
  running tasks in a test workspace.
- Run dry run and verify it reports only old completed task state.
- Run apply and verify protected work remains intact.
- Run `innie logs` after cleanup and verify the cleanup event is visible.
- Acceptance: rerunning cleanup after apply reports no candidates, and active or
  recoverable work is never deleted.

### Milestone 5: Observability

- Add structured task timelines and health checks.
- Emit local metrics and trace-style events.
- Add hook observability: hook duration, failures, skipped hooks, and required
  hook failures.
- Persist hook attempts and failures as observability events.
- Add worker, queue, cleanup, schedule, Slack, and harness health summaries.

### Milestone 6: Scheduled Work

- Add durable recurring tasks.
- Start scheduled work through the same task lifecycle as Slack-triggered work.
- Emit schedule execution events to observability.
- Recover missed or interrupted schedules after restart.

### Later: More Adapters

- Add a second adapter after the first adapter proves the Slack, recovery, and
  observability model.
- Document where the adapter differs instead of hiding important harness
  behavior.

### Later: Native Approval Support

- Add native approval support only for adapters that expose real approval APIs.
- Render native approval requests in Slack.
- Persist approval requests and decisions in the task event stream.
- Recover pending native approvals after restart and repost or relink them in
  Slack.

### Later: Optional Temporal Backend

- Document the workflow backend interface after local SQLite recovery works.
- Add Temporal only for installs that need multi-hour tasks, durable waits,
  stronger schedule guarantees, or horizontal workers.

### Later: Web Operator Console

- Add a local webapp backed by SQLite.
- Show native session groupings by status, harness, schedule, and Slack thread.
- Show Innie status for Slack, harness adapters, task store, workers, schedules,
  cleanup, and storage size.
- Keep the first console read-only.
- Add explicit recovery actions later.

## Resolved Defaults

- Adapter execution: prefer SDK or app-server adapters when they expose stable
  turn lifecycle, resume, streaming, interrupt, and usage metadata. Otherwise
  use subprocess adapters. Core Innie should not care which transport an
  adapter uses.
- Minimum event schema: normalize to lifecycle events (`started`, `progress`,
  `tool_use`, `tool_result`, `output`, `usage`, `completed`, `failed`,
  `canceled`) plus raw harness payload references for debugging. Native
  `approval_request` can be added later. Do not force every harness event into
  a lossy common shape.
- Default concurrency: each user-owned innie should default to
  `max_live_sessions = 10`. Do not add `max_active_harness_turns` in v0 unless
  real usage shows that live idle sessions and active harness turns need
  separate limits.
- Idle runtime limits: personal installs should default to
  `idle_session_ttl = 5m` and `hard_live_session_ttl = 30m`.
- Busy-session input: v0 always queues follow-ups and supports explicit cancel.
  Mid-turn input is an adapter capability for later, only allowed when the
  adapter has a real native input API and tests prove it cannot create ghost
  turns or bypass safety.
- Workflow backend: use the local SQLite-backed queue for Slack-triggered work,
  short tasks, idle resume, and schedules in v0. Use optional Temporal for
  multi-hour or multi-day tasks, durable waits, high-value scheduled workflows,
  and horizontally scaled workers.
- Observability format: write Innie-native JSON events locally first, using
  OpenTelemetry-compatible field names where obvious. Add OTLP exporters later
  without making OpenTelemetry a required dependency in v0.
- Hook blocking behavior: only pre-start hooks that are explicitly configured
  as `required` may block progress, such as install-specific validation or
  workspace preparation. Slack acknowledgment, metrics, notifications, output
  mirroring, cleanup reporting, and most observability hooks are best-effort.
- Approval support: autonomous mode is the default. Native approval mediation is
  only enabled per adapter after the adapter exposes a real approval API. Do not
  emulate approvals through prompt conventions in v0.
- Recovery context: store only session identity, trigger, output target, queued
  inputs, state transitions, harness resume id, usage, artifacts, and compact
  summaries needed to resume or explain the task. Do not store long-term
  semantic memory, user profiles, or large prompt context in Innie core.

## Recommended First Build

Start with a Slack-first Python asyncio service and a SQLite-backed session
store. Implement the CLI only as the setup and debugging path. Add one harness
adapter first, then add the second adapter only after the event schema survives
real Slack-triggered task execution.

Make the first store boring and durable: timestamped session rows, task rows,
append-only event rows, hook event rows, queued input rows, harness resume
identifiers, schedule rows, and artifact files referenced from SQLite. Do not
add Temporal or native approval mediation to the v0 runtime, but shape lifecycle
interfaces so both can be added without changing Slack or harness adapters.

This keeps Innie honest: a thin control layer, not a new agent framework.
