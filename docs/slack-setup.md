# Slack Setup Guide

This guide mirrors `innie init` and `innie slack setup`.

Slack setup usually takes 5-8 minutes. Innie writes a manifest for you, then
guides you through copying a few values from Slack.

## Step 1/6: Name The App

Time: about 1 minute.

Innie asks for:

- Slack app name: the app name in Slack's developer console.
- Bot display name: the name people see in Slack messages.

Defaults are safe:

```text
Slack app name: innie
Bot display name: Innie
```

You can change both later in Slack app settings.

Screenshot placeholder:

```text
[screenshot: Slack app display information fields]
```

## Step 2/6: Choose Trigger Mode

Time: about 1 minute.

Mode 1 is the safer default:

```text
Respond when someone tags the bot, like @Innie.
```

Use this when you want Innie to act only when people explicitly mention the
bot.

Mode 2 is for personal triage:

```text
Respond when someone tags you, like @<username>.
```

Use this when you want Innie to help with messages directed at you. This mode
requires channel/group message events. Innie uses the installing Slack user ID
returned by OAuth so you do not need to copy your member ID during normal setup.

Screenshot placeholder:

```text
[screenshot: trigger mode selection in Innie terminal]
```

## Step 3/6: Create The Slack App From Manifest

Time: 2-3 minutes, about 6 clicks.

Innie prints the manifest directly in the terminal and also writes it to
`.innie/slack-manifest.json`.

In Slack API:

```text
Open https://api.slack.com/apps
Click Create New App -> From an app manifest -> select workspace -> paste manifest
```

After you copy the manifest, return to the terminal and press Enter. Innie clears
the manifest from the terminal and moves to the next step.

Screenshot placeholders:

```text
[screenshot: Create New App button]
[screenshot: From an app manifest option]
[screenshot: manifest paste editor]
```

## Step 4/6: Copy App Credentials

Time: about 1 minute, about 2 clicks.

In Slack API:

```text
Basic Information -> App Credentials
```

Copy these into the wizard:

- Client ID
- Client Secret
- App ID

Screenshot placeholder:

```text
[screenshot: Basic Information App Credentials section]
```

## Step 5/6: Install With OAuth

Time: 1-2 minutes.

Innie prints an OAuth URL and starts a local callback server:

```text
http://localhost:8765/callback
```

If the browser can reach the local callback, Innie receives the OAuth code
automatically.

If you are running Innie in a cloud or remote dev environment, the browser may
fail to load `localhost`. That is fine. Copy the final callback URL from the
browser address bar and paste it back into the wizard. Innie will extract the
`code` value.

Screenshot placeholders:

```text
[screenshot: Slack OAuth approval page]
[screenshot: browser address bar containing code query parameter]
```

## Step 6/6: Create Socket Mode Token

Time: about 1 minute, about 4 clicks.

In Slack API:

```text
Basic Information -> App-Level Tokens -> Generate Token and Scopes
```

Add this scope:

```text
connections:write
```

Copy the generated token into Innie. It should start with:

```text
xapp-
```

Screenshot placeholder:

```text
[screenshot: App-Level Tokens form with connections:write scope]
```

## Done

Innie validates:

- bot token works with Slack auth
- Socket Mode can open
- tokens are stored locally with restrictive permissions
- non-secret Slack metadata is written to `.innie/config.yaml`
