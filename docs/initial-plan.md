# Innie Initial Plan

## Summary

Innie is an open-source sidekick layer that sits between users and agent
harnesses. The human is the outie; the Slack bot is the innie for a software
system; the harness does the work behind that boundary. Innie provides the
product shell around agent execution: triggers, task lifecycle, memory
contracts, policy, approvals, audit, schedules, and Slack-native collaboration.
It delegates the inner agent loop to existing harnesses such as Codex, Claude
Code, OpenCode, Goose, or future runtimes.

The guiding principle is:

> Innie owns the operating envelope. The harness owns the agent loop.

## Goals

- Provide a thin, generic control layer for personal and team AI sidekicks.
- Support multiple harnesses through small adapters.
- Make chat-triggered and scheduled agent work observable, resumable, and
  auditable.
- Treat Slack as the v0 outie interface: the human-facing place where users
  ask, approve, interrupt, and receive work.
- Keep durable memory and task state portable through simple file-backed
  conventions.
- Enforce policy and approval boundaries outside the harness.
- Avoid becoming a full agent framework or replacing Codex, Claude Code, or
  other harnesses.

## Non-Goals

- Do not build a new ReAct loop.
- Do not build a proprietary model runtime.
- Do not require one canonical harness.
- Do not hide harness-specific capabilities when users deliberately choose
  them.
- Do not make Slack the only interface.
- Do not require cloud hosting for the MVP.

## Product Shape

```text
User
  Slack (v0 outie interface) / CLI / Web / GitHub / cron
    |
Innie
  routing, policy, memory, approvals, audit, schedules
    |
Harness adapter
  Codex / Claude Code / OpenCode / Goose / custom
    |
Workspace or sandbox
  repo, shell, tools, MCPs, secrets, browser
```

## Responsibilities

### Innie Owns

- Trigger ingestion from CLI, Slack, cron, webhooks, and eventually GitHub.
- Task lifecycle: create, queue, start, stream, pause, resume, cancel, retry,
  timeout, and archive.
- Actor and workspace context: who asked, where the task runs, and where output
  should go.
- Policy bundles: allowed tools, denied tools, approval-required actions, and
  network or secret boundaries.
- Memory contracts: where durable memory is mounted, who owns it, how it is
  scoped, and how it is retained.
- Approval workflows: request human approval before irreversible or sensitive
  actions.
- Audit logs: append-only task events, tool requests, approvals, artifacts, and
  final outcomes.
- Harness adapters: a small compatibility layer for each supported runtime.

### Harnesses Own

- Planning, acting, observing, and revising.
- Code editing, test execution, and task-specific debugging.
- Context packing within a session.
- Harness-native tools, skills, plugins, and subagents.
- Harness-native self-improvement, when allowed by policy.
- Producing task artifacts such as diffs, summaries, logs, and PR drafts.

## Adapter Contract

The first adapter contract should stay intentionally small:

```ts
export interface HarnessAdapter {
  startTask(request: TaskRequest): Promise<TaskHandle>;
  sendInput(taskId: string, input: UserInput): Promise<void>;
  cancelTask(taskId: string): Promise<void>;
  streamEvents(taskId: string): AsyncIterable<HarnessEvent>;
  collectArtifacts(taskId: string): Promise<Artifact[]>;
}
```

The request object should be policy-rich but harness-neutral:

```ts
export type TaskRequest = {
  goal: string;
  actor: ActorIdentity;
  trigger: TriggerContext;
  workspace: WorkspaceRef;
  memory: MemoryMount[];
  policy: PolicyBundle;
  secrets: SecretGrant[];
  output: OutputTarget;
};
```

Adapters may expose optional capability metadata:

```ts
export type HarnessCapabilities = {
  supportsStreaming: boolean;
  supportsResume: boolean;
  supportsStructuredArtifacts: boolean;
  supportsInteractiveApproval: boolean;
  supportsSubagents: boolean;
};
```

## Initial Interfaces

### Slack

Slack is the v0 outie interface. It is where humans interact with the innie:
they ask for work, approve sensitive actions, interrupt running tasks, and
receive results. Other interfaces should be possible later, but the first
product experience should prove that every software system can have a useful
Slack-native work self.

Expected behavior:

- A DM or mention creates a task.
- The task streams progress back to the thread.
- Users can ask for status or cancel a task.
- Risky actions ask for approval in-thread.
- Team installs can restrict channels and allowed users.
- Each installed bot represents the innie for a specific user, team, repo,
  service, or software system.

### CLI

The CLI is the first developer and admin interface. It should exist in v0 for
local testing, setup, debugging, and automation, but it is not the primary
outie experience.

Example commands:

```bash
innie run "inspect this repo and suggest the next implementation step"
innie status <task-id>
innie logs <task-id>
innie cancel <task-id>
innie approve <approval-id>
```

## Memory Layout

The MVP should use file-backed memory so users can inspect, diff, back up, and
version it.

```text
.innie/
  config.yaml
  memory/
    profile.md
    preferences.md
    facts.md
    runbooks/
    projects/
  tasks/
    <task-id>/
      request.json
      events.jsonl
      artifacts/
  audit/
    YYYY-MM-DD.jsonl
```

Memory is mounted into the harness as context or files, but Innie owns the
storage contract and retention policy.

## Policy Model

The MVP policy file should be readable and conservative:

```yaml
tools:
  allow:
    - shell.read
    - shell.test
    - git.diff
  require_approval:
    - git.push
    - github.pr.create
    - slack.post.channel
    - file.delete
  deny:
    - shell.rm
    - shell.sudo
    - network.external

secrets:
  default: deny
  grants:
    - name: github-token
      scope: repo
      mode: read
```

Policy enforcement starts in Innie. Individual harnesses may also enforce their
own policies, but Innie must not rely on harness-specific behavior as the only
guardrail.

## MVP Milestones

### Milestone 1: Slack Outie Interface

- Initialize a local `.innie/` workspace.
- Connect a Slack app to an Innie workspace.
- Route DMs and mentions into tasks.
- Stream progress and final output to Slack threads.
- Support status, cancel, and approval interactions.
- Persist request, events, artifacts, and final status.
- Provide minimal CLI commands for setup, debugging, and local task inspection.

### Milestone 2: Codex And Claude Code Adapters

- Add adapters for Codex and Claude Code.
- Normalize streaming events into a common event schema.
- Collect basic artifacts such as summaries, diffs, and command logs.
- Document harness capability differences instead of hiding them.

### Milestone 3: Policy And Approval MVP

- Load policy from `.innie/config.yaml`.
- Block denied actions when Innie can observe them.
- Pause tasks for approval-required actions.
- Persist approval decisions in the audit log.

### Milestone 4: Multi-Innie Installs

- Support more than one innie in the same Slack workspace.
- Scope an innie to a user, team, repo, service, or software system.
- Keep memory, policy, audit, and secrets isolated per innie.
- Make the install identity obvious in Slack messages and audit records.

### Milestone 5: Scheduled Work And Memory

- Add recurring tasks.
- Mount file-backed memory into harness sessions.
- Save daily task summaries and useful runbooks.
- Keep memory updates explicit and auditable.

## Open Questions

- Should Innie be TypeScript-first, Python-first, or split into a small core
  plus language-specific adapters?
- Should harness adapters run as subprocesses first, or should adapters prefer
  SDKs when available?
- What is the minimum event schema that works across Codex, Claude Code,
  OpenCode, and Goose?
- How much policy can be enforced generically before sandbox integration is
  required?
- Which Slack surface should define the v0 experience: DM-first, channel
  mention-first, or both from day one?

## Recommended First Build

Start with a Slack-first TypeScript service and a file-backed task store.
Implement the CLI only as the setup and debugging path. Add one harness adapter
first, then add the second adapter only after the event schema survives real
Slack-triggered task execution.

This keeps Innie honest: a thin control layer, not a new agent framework.
