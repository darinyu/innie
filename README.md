# Innie

[![CI](https://github.com/darinyu/innie/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/darinyu/innie/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/innie.svg)](https://pypi.org/project/innie/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

![Innie wordmark](assets/logo/innie-wordmark.svg)

**Every worker deserves an innie: an AI work-self you can trigger from Slack anywhere, any time.**

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

Clone the repo:

```bash
git clone https://github.com/darinyu/innie.git
cd innie
```

Install the `innie` command from this checkout:

```bash
python3 scripts/install.py
```

Create local state and start setup:

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

## Development

Innie is a Python project built with Hatchling.

```bash
python3 -m pip install -e .
PYTHONPATH=src python3 -m unittest discover -s tests -v
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
- Codex CLI or Claude Code CLI. Codex remains the default; Claude is opt-in via
  `--harness claude`. OpenCode, Goose, and custom runtimes are future adapters.
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

- `CONTRIBUTING.md` with development setup, test expectations, and PR guidance.
- `SECURITY.md` with vulnerability reporting and secret-handling expectations.
- `CODE_OF_CONDUCT.md` if the project wants an explicit community standard.
- GitHub issue templates once the contribution surface is clearer.

## License

Innie is licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE)
for details.
