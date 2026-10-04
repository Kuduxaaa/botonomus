# Security policy

## Reporting a vulnerability

Report it privately through GitHub: [Report a vulnerability](https://github.com/Kuduxaaa/botonomus/security/advisories/new). Include:

- the affected version (`botonomus --version`) and platform,
- a description of the issue and its impact,
- steps or a minimal script to reproduce it.

Please do not open a public GitHub issue for a vulnerability. We aim to acknowledge reports within three working days and to agree a disclosure date with you once a fix is available.

**Never include credentials** in any report, issue, log or screenshot: no proxy usernames or passwords, cookies, session tokens, profile directories or `persona.key` files. Botonomus keeps credentials out of its own messages and logs; a place where it does not is itself a security issue worth reporting.

## Supported versions

| Version | Supported |
|---|---|
| 0.2.x | Yes |
| 0.1.x | No |

## Scope

In scope:

- The `botonomus` Python package: credential handling (proxy URLs, the loopback forwarder), profile locking and isolation, process ownership and cleanup, the CDP connection, the CLI.
- The Botonomus Chromium installer: manifest signature verification (Ed25519), archive size and SHA-256 checks, safe archive extraction, install directory handling.
- Report files written by `botonomus detect`, `probe`, `proxy-check` and `benchmark` leaking secrets.

Out of scope:

- A website detecting Botonomus as automation. That is a detection result, not a vulnerability; open a normal issue with a `botonomus detect` report instead.
- Vulnerabilities in Google Chrome, Chromium, Playwright or Patchright themselves; report those upstream.
- Local attackers who can already run code as your user. Any local process that can reach a browser's loopback debugging port can control that session while it runs, which is a documented property of CDP. Likewise, on Linux any local user can connect to the Xvfb virtual display; it is meant for single-user machines.
