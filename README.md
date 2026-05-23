# Innie

![Innie wordmark](assets/logo/innie-wordmark.svg)

**Every worker deserves an innie: an AI work-self you can trigger from Slack, any where, any time.**

> Innie is an early prototype. The repo contains the first local setup,
> Slack setup, durable state, hooks, session inspection, Codex and echo adapter
> paths, and runtime building blocks, but it is not production-ready yet.

![Innie Slack-to-agent workflow](assets/demo/innie-flow.gif)

## What Is Innie?

Innie is the thinnest customizable layer between Slack and agent harnesses like
Codex, Claude Code, OpenCode, Goose, and future tools. You run Innie in your own
dev environment, local or cloud. It keeps work durable, visible, resumable, and
observable while the selected harness does the actual agent work.

The human is the **Outie**: the person who asks from Slack, follows progress,
and receives the result.

The bet is simple: **harnesses keep getting better**. Innie should not replace
their planning, coding, permissions, tools, or model behavior. Innie owns the
operating envelope around them:

- **Slack in, Slack out**: trigger work from Slack and get replies back in the
  thread.
- **Harness-neutral boundary**: keep Codex CLI, Claude Code, OpenCode, Goose,
  and custom runtimes behind adapters.
- **Your environment, your access**: run the harness where your repos, CLIs,
  MCP servers, credentials, and local tools already work.
- **Durable by default**: persist sessions, queued follow-ups, progress events,
  schedules, recovery state, and observability data.

Innie is not a new agent loop, policy engine, model runtime, or semantic memory
system. It is the minimum product shell that can make a harness feel like a
dependable worker.

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

## Quickstart

The path is:

1. Download the repo.
2. Check and install dependencies with the provided script.
3. Set up the Slack bot with the guided setup wizard.
4. Start the fun.

### 1. Download The Repo

```bash
git clone https://github.com/darinyu/innie.git
cd innie
```

### 2. Check And Install Dependencies

Install the `innie` command from this checkout. The provided script checks local
dependencies and installs the command:

```bash
python3 scripts/install.py
```

### 3. Set Up The Slack Bot

Create local state and start the guided Slack setup wizard:

```bash
innie init
```

To create local state without Slack setup:

```bash
innie init --skip-slack-setup
```

To run the Slack setup wizard later:

```bash
innie slack setup
```

For the guided Slack checklist, see
[`docs/slack-setup.md`](docs/slack-setup.md).

### 4. Start The Fun

Run a first smoke test, then keep Innie running when you are ready:

```bash
innie run --once --harness codex
innie run
```

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

`--once` is a smoke-test mode: Innie connects, waits for one routed Slack event,
processes it, prints the session id and log command, then exits.

Run continuously with:

```bash
innie run
```

Stop it with Ctrl-C.

Use `--harness echo` when you want to debug Slack routing without starting
Codex.

## Development

Innie is a Python project built with Hatchling.

```bash
python3 -m pip install -e .
python3 -m pytest
```

Useful local commands:

```bash
innie init --skip-slack-setup
innie status <session-id>
innie logs <session-id>
innie cancel <session-id>
```

Read [`docs/initial-plan.md`](docs/initial-plan.md) for the current product and
architecture plan.

## Requirements

- Python 3.10+.
- SQLite 3 for local durable session state.
- Rich for colored, wrapped terminal setup screens. `scripts/install.py` asks
  before installing it, and Innie falls back to plain text if you skip it.
- A Slack app for DM and channel mention triggers.
- Codex CLI. V0 supports Codex; Claude Code, OpenCode, Goose, and custom
  runtimes are future adapters.
- Optional MCP servers, skills, CLIs, and credentials from your own dev
  environment.

## Local State And Secrets

Innie stores local runtime state under `.innie/` in the selected workspace.
Slack tokens and generated Slack metadata are part of that setup flow.

Do not commit `.innie/` or Slack credentials. The prototype is designed for
local development first, so review generated files and permissions before using
it in a shared or remote environment.

## Roadmap

Near-term prototype milestones:

- Harden the Codex path into the first stable adapter contract.
- Improve Slack-triggered task progress and result delivery.
- Persist enough state to explain, retry, or resume interrupted work.
- Make lifecycle hooks stable enough for local customization.
- Improve observability events, status output, and failure diagnostics.
- Add production-oriented docs after the local prototype proves the core loop.

## Contributing

This repo is still early, so small, focused PRs are the best way to contribute.

Good first areas:

- Tighten the Slack onboarding flow.
- Improve setup validation and error messages.
- Add focused tests for session, hook, runtime, and adapter behavior.
- Implement or refine a harness adapter.
- Improve observability events and status output.
- Refine docs, logo assets, and demo materials.

Design constraints:

- Keep Innie thin.
- Keep state durable.
- Keep Slack useful first.
- Keep harness behavior behind adapters.
- Prefer simple local defaults before distributed infrastructure.

Open-source hygiene still to add:

- `LICENSE` file matching the MIT license declared in `pyproject.toml`.
- `CONTRIBUTING.md` with development setup, test expectations, and PR guidance.
- `SECURITY.md` with vulnerability reporting and secret-handling expectations.
- `CODE_OF_CONDUCT.md` if the project wants an explicit community standard.
- GitHub issue templates once the contribution surface is clearer.

## License

`pyproject.toml` declares this project as MIT licensed. A root `LICENSE` file
still needs to be added before the project should be treated as properly
packaged for open-source distribution.
