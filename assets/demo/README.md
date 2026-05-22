# Demo Animation

`innie-flow.gif` shows the intended Innie workflow:

1. A user triggers Innie from Slack on a phone.
2. Innie starts an agent harness in the user's own dev environment, local or cloud.
3. The agent uses the same workspace resources through skills, MCPs, tools, and repository access.
4. The agent triages an issue, writes code, and runs checks.
5. Innie replies back to the Slack thread.

Regenerate it with:

```bash
/Users/zyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 assets/demo/generate_innie_flow_gif.py
```
