# Security

Innie is a local-first prototype that can interact with Slack tokens, agent
CLIs, source repositories, MCP servers, and credentials already available in
your development environment.

## Supported Versions

The project is pre-1.0. Security fixes target the latest commit on `main` until
the first PyPI alpha release defines versioned support.

## Reporting A Vulnerability

Do not open a public issue with secrets, tokens, private repository details, or
exploit instructions.

Until a dedicated disclosure address exists, report issues privately to the
maintainer through GitHub profile contact channels and include:

- The affected command or setup path.
- The local state or secret type involved.
- A minimal reproduction that avoids real credentials.
- Whether any token or workspace data may have been exposed.

## Secret Handling

- Do not commit `.innie/`.
- Do not paste Slack bot or app tokens into public issues.
- Review generated Slack manifests before sharing them.
- Run Innie only in environments where the selected harness should have access
  to the repositories, CLIs, MCP servers, and credentials available there.
