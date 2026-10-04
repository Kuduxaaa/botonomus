# Profiles

A profile is a Chrome user-data directory under `BrowserConfig.profile_root` (default `.botonomus/profiles`). It keeps cookies, local storage, IndexedDB, history and Chrome's own settings between runs, so a site sees a returning browser.

```python
async with bot.open(profile="acct-01") as session:
    ...  # log in once; later runs with "acct-01" reuse the cookies
```

## Names

- 1-64 ASCII letters, digits, `_` or `-`, starting with a letter or digit.
- Normalized to lowercase: `Acct-01` and `acct-01` are the same profile.
- Windows device names (`con`, `nul`, `com1`, ...) are refused.

Invalid names raise `ConfigurationError`.

## Exclusive ownership

Opening a profile takes a cross-process file lock in `profile_root/.locks`. A second session for the same profile, in the same or another Python process, raises `ProfileInUseError` instead of corrupting the directory. Symlinked or junctioned profile directories are refused, so a lease cannot be redirected elsewhere.

## Listing and removing

```python
from pathlib import Path
from botonomus.profiles import list_profiles, remove_profile

root = Path(".botonomus/profiles")
for info in list_profiles(root):
    print(info.name, info.in_use, info.modified)

remove_profile(root, "acct-01")  # raises ProfileInUseError while a session owns it
```

From the command line:

```bash
botonomus profiles list
botonomus profiles list --root D:\bots\profiles --json
botonomus profiles remove acct-01
```

## Warm-up

A fresh profile with no history is itself a signal. [`warm_up`](../reference/profiles.md) browses a curated list of benign, high-traffic sites (news, encyclopedia, weather, shopping, entertainment) with human input: it dwells, scrolls and follows a few ordinary same-site links. It never types, never submits forms and never follows login, account, cart, checkout, consent or download links. It is opt-in and never runs automatically.

```python
from botonomus.profiles import warm_up

async with bot.open(profile="acct-01") as session:
    report = await warm_up(session.page, duration=180, rng_seed=7)
    print(report.sites_visited, report.sites_failed, report.links_followed)
```

`page` can be a native page, a Playwright page or a `HumanPage`'s `.raw`. Pass `sites=[...]` to use your own list and `human=` to reuse an existing `Human`. A failing site is logged by failure category and skipped.

From the command line, `botonomus profiles warmup acct-01 --duration 180` does the same and prints the report; `--sites URL ...` replaces the site list and `--humanize careful` picks the input preset (see [Command line](cli.md)).

## Persona seed key

With `persona="auto"` on Botonomus Chromium, each profile's persona seed is derived from its name and a random per-installation key stored in `profile_root/.botonomus/persona.key`. The same profile keeps the same identity across restarts; the same name on another installation gets an unrelated identity. Deleting the key changes the identity of every profile under that root. See [Personas](personas.md).

## Safety

- Use a dedicated profile root. Never point Botonomus at your everyday Chrome profile.
- Profiles contain sensitive browsing state (session cookies, tokens). Keep them out of version control (`.botonomus/` is in `.gitignore`) and out of bug reports.
