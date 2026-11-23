# Security policy

## Supported versions

Security fixes go into the latest release. Please update before reporting a problem.

| Version | Supported |
|---|---|
| Latest stable release | ✅ |
| Current beta, if any | ✅ |
| Older versions | ❌ |

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub instead:

1. Go to the [Security tab](https://github.com/3615nulsi/ha-awtrix-studio/security) of the repository.
2. Select **Report a vulnerability**.
3. Describe the problem, how to reproduce it, and what an attacker could do with it.

You'll get an answer within a week. Once the problem is confirmed, a fix is prepared privately and released as soon as possible. You'll be credited in the advisory and the release notes, unless you'd rather not be.

## What's in scope

Examples of what we want to hear about:

- a way to run code or inject HTML in the panel, for example through an app name, a label, an entity name or an imported file;
- a way for a non-administrator to see or use the panel or its WebSocket commands;
- a way to publish on MQTT topics other than those of the clock, for example through an app name;
- a leak of the clock's web page password, in logs, diagnostics or the panel.

Out of scope:

- what an administrator can already do in Home Assistant, such as writing templates;
- problems in Home Assistant, the MQTT broker or the clock firmware themselves (please report those to their own projects).

## How the integration protects you

The safeguards already in place are listed in the [Security section of the README](README.md#-security).
