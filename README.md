# Innie

![Innie wordmark](assets/logo/innie-wordmark.svg)

**Every worker deserves an innie: an AI work-self you can trigger from Slack.**

![Innie Slack-to-agent workflow](assets/demo/innie-flow.gif)

Innie is the thinnest customizable layer between Slack and agent harnesses like
Codex, Claude Code, OpenCode, Goose, and future tools. You run Innie in your own
dev environment, local or cloud. It keeps the work durable, visible, resumable,
and observable while the harness does the actual agent work.

## What Is Innie And Why?

Innie is a Slack-first sidekick runtime for AI coding agents and software
automation agents. It runs in your dev environment, local or cloud, so the
agent can use the same repo, tools, skills, MCPs, credentials, and workspace
access that you already use with the underlying harness.

The bet is simple: harness tools will keep getting better. Codex, Claude Code,
OpenCode, Goose, and future harnesses should own planning, coding, tool use,
permissions, and model behavior. Innie should own the small product shell around
them:

- Slack trigger in, Slack reply out.
- Easy harness switching through a thin adapter boundary.
- Your environment, your access: the agent can do what the selected harness can
  do in that workspace.
- Durable sessions, queued follow-ups, and restart recovery.
- Visible progress without exposing private chain-of-thought.
- Local schedules for recurring work.
- Observability for task history, failures, usage, and health.
- Hooks and harness adapters so teams can customize behavior without forking the
  runtime.

Innie is not a new agent loop, policy engine, or semantic memory system. It is
the minimum layer that makes an agent harness feel like a dependable worker.

## How It Works

```text
Slack
  user asks from phone or desktop
    |
Innie
  session state, queue, hooks, progress, schedules, observability
    |
Harness adapter
  Codex, Claude Code, OpenCode, Goose, or custom runtime
    |
Your dev environment
  repo, tests, skills, MCPs, tools, logs, artifacts
```

## Install

Innie is in early design. The runtime is not packaged yet.

Clone the repo:

```bash
git clone https://github.com/darinyu/innie.git
cd innie
```

Current artifacts:

- [Initial plan](docs/initial-plan.md)
- [Logo assets](assets/logo)
- [Demo animation](assets/demo/innie-flow.gif)

Target install experience:

```bash
innie init
innie slack setup
innie run
```

## Dependencies

Planned runtime dependencies:

- Python asyncio runtime.
- SQLite for local durable session state.
- A Slack app for DM and channel mention triggers.
- At least one installed harness, such as Codex, Claude Code, OpenCode, or
  Goose.
- Optional MCP servers, skills, CLIs, and credentials from your own dev
  environment.

Current repo assets have no runtime dependency. The demo GIF is generated with
Python and Pillow via [assets/demo/generate_innie_flow_gif.py](assets/demo/generate_innie_flow_gif.py).

## Contribute

Good first contributions:

- Tighten the Slack onboarding flow.
- Implement the SQLite session store.
- Add the first harness adapter.
- Improve lifecycle hooks.
- Improve observability events and status output.
- Refine the README, logo, or demo animation.

Design constraints:

- Keep Innie thin.
- Keep state durable.
- Keep Slack useful first.
- Keep harness behavior behind adapters.
- Prefer simple local defaults before distributed infrastructure.

Read the [initial plan](docs/initial-plan.md), open an issue, or send a small
PR.
