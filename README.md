# Innie

[![CI](https://github.com/darinyu/innie/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/darinyu/innie/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![PyPI](https://img.shields.io/pypi/v/innie.svg)](https://pypi.org/project/innie/)

![Innie wordmark](assets/logo/innie-wordmark.svg)

**Every worker deserves an innie: an AI work-self you can trigger from Slack anywhere, any time.**

> Innie is an early prototype. The repo contains local setup, guided Slack
> setup, durable state, hooks, session inspection, the local dashboard, and
> Codex and Claude Code adapter paths.

![Innie Slack-to-agent workflow](assets/demo/innie-flow.gif)

## What Is Innie?

Innie is the thinnest customizable layer between Slack and agent harnesses. The
current repo supports Codex CLI and Claude Code; custom runtimes are future
adapter targets. You run Innie in your own dev environment, local or cloud. It
keeps Slack-triggered work durable, visible, resumable where the harness supports
it, and observable while the selected harness does the actual agent work.

The human is the **Outie**: the person who asks from Slack, follows progress,
and receives the result.

The bet is simple: **harnesses keep getting better**. Innie should not replace
their planning, coding, permissions, tools, or model behavior. Innie owns the
operating envelope around them:

- **Slack in, Slack out**: trigger work from Slack and get replies back in the
  thread.
- **Harness adapter boundary**: keep Codex CLI, Claude Code, and future
  runtimes behind adapters.
- **Your environment, your access**: run the harness where your repos, CLIs,
  MCP servers, credentials, and local tools already work.
- **Durable by default**: persist sessions, queued follow-ups, progress events,
  harness resume ids, hooks, artifacts, and observability data.

Innie is *NOT* a new agent loop, policy engine, model runtime, or semantic memory
system. It is the minimum product shell that can make a harness feel like a
dependable worker.

## How It Works

```text
Slack
  user asks from phone or desktop
    |
Innie
  session state, queue, hooks, progress, dashboard, observability
    |
Harness adapter
  Codex CLI, Claude Code, echo, or future runtime
    |
Your dev environment
  repo, tests, skills, MCPs, tools, logs, artifacts
```

## Quickstart

This path gets a local checkout connected to Slack, verifies one routed event,
and then leaves you with a continuous Innie worker plus a local dashboard.

Before you start, have:

- Python 3.10+.
- A Slack workspace where you can create and install an app.
- Codex CLI or Claude Code installed in the same environment where Innie will
  run. Codex is the default harness; Claude is opt-in.

### 1. Download The Repo

```bash
git clone https://github.com/darinyu/innie.git
cd innie
```

### 2. Install The Local Command

Install the `innie` command from this checkout:

```bash
python3 scripts/install.py
```

The installer checks runtime dependencies, offers to install the optional Rich
terminal UI, and writes or refreshes an `innie` launcher. It is safe to rerun
after pulling updates.

If the printed install directory is not on your shell `PATH`, add it before
continuing. The default is usually `~/.local/bin`.

### 3. Set Up The Slack Bot

Create local state and start the Slack app wizard:

```bash
innie init
```

The wizard takes about 5-8 minutes. It writes a Slack manifest to
`.innie/slack-manifest.json`, walks you through creating the Slack app, stores
Slack tokens locally, and writes non-secret metadata to `.innie/config.yaml`.

At the end of setup, invite the Slack app to each channel where Innie should
listen. In Slack, type:

```text
/invite @Innie
```

Use the bot display name you chose if you renamed it.

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

### 4. Run One Slack Smoke Test

Start Innie in one-event mode:

```bash
innie run --once --harness codex
```

In Slack, tag either the bot or the configured watched user in a channel where
the app is installed:

```text
@Innie hello
```

When Innie accepts the event, it processes one task, prints the session id and a
log command, replies in Slack, and exits. Use the log command if you want to
inspect the durable session:

```bash
innie logs <session_id>
```

### 5. Keep Innie Running

Run continuously when the smoke test works:

```bash
innie run
```

Stop it with Ctrl-C.

Open the local dashboard in another terminal:

```bash
innie dash
```

By default, the dashboard listens on `http://127.0.0.1:8765`. It is read-only
and can run beside `innie run`.

## Run

Use `--once` whenever you want a bounded smoke test: Innie connects, waits for
one accepted Slack event, processes it, prints the session id and log command,
then exits.

Run with Claude Code instead of Codex:

```bash
innie run --once --harness claude
```

Run the diagnostic echo adapter when you want to test routing without starting
Codex or Claude:

```bash
innie run --once --event-file event.json --harness echo
```

Run continuously with a specific harness:

```bash
innie run --harness codex
```

Useful inspection commands:

```bash
innie status <session_id>
innie logs <session_id>
innie cancel <session_id>
innie cleanup
```

`innie cleanup` is a dry run by default. Pass `--apply` only when you are ready
to delete eligible old completed local task state.

## Troubleshooting First Run

- `innie: command not found`: add the installer output directory to `PATH`, or
  rerun `python3 scripts/install.py --bin-dir <directory-on-your-path>`.
- `Slack bot user id is missing`: run `innie slack setup` again and complete the
  wizard.
- Innie never accepts your Slack message: invite the app to the channel, confirm
  the bot or watched user was mentioned, and try `innie run --once --harness echo`
  to isolate Slack routing from harness behavior.
- Codex or Claude does not start: confirm the selected CLI works from the same
  shell where you started `innie run`.

## Development

Innie is a Python project built with Hatchling.

```bash
python3 -m pip install -e .
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Useful local commands:

```bash
innie init --skip-slack-setup
innie dash
```

`innie dash` starts a lightweight local web dashboard for the selected
workspace. It reads `.innie/innie.db` and `.innie/logs/innie.log` directly, so it
is read-only and can be run alongside `innie run`. The dashboard is intended for
local inspection of sessions, task events, hooks, artifacts, health, and logs.

Read [`docs/initial-plan.md`](docs/initial-plan.md) for the current product and
architecture plan.

## Install From PyPI

Innie is published on PyPI as an alpha package:

```bash
pipx install innie
```

For local development, prefer the checkout install path in Quickstart so changes
in `src/` are easy to inspect and test.

The release path builds clean wheel and source distributions, validates package
metadata, smoke-tests the installed wheel in CI, and publishes through PyPI
trusted publishing. See [`docs/pypi-release.md`](docs/pypi-release.md) for the
release checklist.

## Requirements

- Python 3.10+.
- SQLite 3 for local durable session state.
- Rich for colored, wrapped terminal setup screens. `scripts/install.py` asks
  before installing it, and Innie falls back to plain text if you skip it.
- A Slack app installed in channels where Innie should respond when people tag
  the bot or the watched user.
- Codex CLI or Claude Code CLI. Codex remains the default; Claude is opt-in via
  `--harness claude`. OpenCode, Goose, and custom runtimes are future adapters.
- Optional MCP servers, skills, CLIs, and credentials from your own dev
  environment.

## Local State And Secrets

Innie stores local runtime state under `.innie/` in the selected workspace.
Important files include:

- `.innie/config.yaml`: non-secret workspace and Slack metadata.
- `.innie/secrets.json`: local Slack tokens.
- `.innie/innie.db`: durable sessions, tasks, progress, hooks, and artifacts.
- `.innie/logs/innie.log`: local run logs.

It is safe to rerun `innie init`. Existing local state and Slack configuration
are kept. Rerun `innie slack setup` when you intentionally want to update Slack
tokens or app settings.

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
- Add scheduled runs after the Slack-triggered loop is stable.
- Add OpenCode, Goose, or custom adapters after the Codex and Claude paths prove
  the adapter contract.
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

- `CODE_OF_CONDUCT.md` if the project wants an explicit community standard.
- GitHub issue templates once the contribution surface is clearer.

## License

Innie is licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE)
for details.
