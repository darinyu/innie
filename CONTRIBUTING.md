# Contributing

Innie is an early prototype. Small, focused pull requests are easiest to
review and keep the local-first workflow stable.

## Development Setup

Install the command from a checkout:

```bash
python3 scripts/install.py
```

Or install the package in editable mode:

```bash
python3 -m pip install -e .
```

## Test Method

Run the full local test suite before opening a PR:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

For packaging changes, also build and inspect the distribution:

```bash
python3 -m pip install build twine
python3 -m build
python3 -m twine check dist/*
```

## Pull Requests

- Keep changes narrow.
- Include a summary and test method.
- Do not commit `.innie/`, Slack tokens, generated secrets, or local logs.
- Prefer docs and tests with behavior changes.
