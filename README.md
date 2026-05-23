# Innie

![Innie wordmark](assets/logo/innie-wordmark.svg)

**Every worker deserves an innie: an AI work-self you can trigger from Slack anywhere, any time.**

![Innie Slack-to-agent workflow](assets/demo/innie-flow.gif)

Innie is the thinnest customizable layer between Slack and agent harnesses like
Codex, Claude Code, OpenCode, Goose, and future tools. You run Innie in your own
dev environment, local or cloud. It keeps the work durable, visible, resumable,
and observable while the harness does the actual agent work.

## What Is Innie And Why?

Innie is a **Slack-first sidekick runtime** for AI coding agents and software
automation agents. It runs in **your dev environment**, local or cloud, so the
agent can use the same repo, tools, skills, MCPs, credentials, and workspace
access you already use.

The bet: **harnesses keep getting better**. Codex, Claude Code, OpenCode,
Goose, and future tools should own planning, coding, permissions, and model
behavior. Innie owns the **thin product shell** around them:

- **Slack in, Slack out**: trigger work from Slack and get replies back in the
  thread.
- **Harness-neutral**: switch between Codex CLI, Claude Code, OpenCode, Goose,
  or future harnesses through adapters.
- **Your environment, your access**: the agent can do what the selected harness
  can do in that workspace.
- **Durable and visible**: sessions, queued follow-ups, progress, schedules,
  recovery, and observability.

Innie is not a new agent loop, policy engine, or semantic memory system. It is
the minimum layer that makes a harness feel like a dependable worker.

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

Clone the repo:

```bash
git clone https://github.com/darinyu/innie.git
cd innie
```

Install the `innie` command from this checkout:

```bash
python3 scripts/install.py
```

Start setup:

```bash
innie init
```

`innie init` checks local dependencies, creates durable local state in `.innie/`,
and then starts the Slack setup wizard.

For the guided Slack screenshots checklist, see
[`docs/slack-setup.md`](docs/slack-setup.md).

## Run

Test the local route without Slack by feeding one Slack-shaped event file through
the diagnostic echo adapter:

```bash
innie run --once --event-file event.json --harness echo
```

After `innie slack setup`, test one real Slack-routed Codex event and exit:

```bash
innie run --once --harness codex
```

Claude Code is available as an opt-in peer harness:

```bash
innie run --once --harness claude
```

`--once` is a smoke-test mode: Innie connects, waits for one routed Slack event,
processes it, prints the session id and log command, then exits.

Run continuously with:

```bash
innie run
```

Stop it with Ctrl-C.

Use `--harness echo` when you want to debug Slack routing without starting
Codex or Claude.

## Dependencies

Planned runtime dependencies:

- Python 3.10+.
- SQLite 3 for local durable session state.
- Rich for colored, wrapped terminal setup screens. `scripts/install.py` asks
  before installing it, and Innie falls back to plain text if you skip it.
- A Slack app for DM and channel mention triggers. Innie should provide a Slack
  app setup wizard through `innie slack setup`.
- Codex CLI or Claude Code CLI. Codex remains the default; Claude is opt-in via
  `--harness claude`. OpenCode, Goose, and custom runtimes are future adapters.
- Optional MCP servers, skills, CLIs, and credentials from your own dev
  environment.

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
