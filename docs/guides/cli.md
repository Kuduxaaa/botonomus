# Command line

Installing the package adds the `botonomus` command. `python -m botonomus.cli` is equivalent.

```bash
botonomus --version
botonomus COMMAND --help
```

| Command | Purpose |
|---|---|
| `botonomus info [--executable PATH] [--browser {auto,botonomus,chrome}] [--json]` | Botonomus and Python versions, platform, discovered Chrome (path, version, testing-build warning), the newest installed Botonomus Chromium build, the executable a launch would use (`default`, and whether it is a Botonomus build), available drivers, installed builds |
| `botonomus install [--version V] [--manifest-url URL] [--json]` | Download, verify and install Botonomus Chromium; progress on stderr, then the installed version and executable |
| `botonomus uninstall VERSION` | Remove an installed build |
| `botonomus binaries [--json]` | List installed Botonomus Chromium builds, newest first |
| `botonomus open [--profile P] [--url U] [--root DIR]` | Visible persistent session until Enter or Ctrl+C |
| `botonomus probe [--normal \| --baseline FILE \| --serve] [--output DIR] [--timeout S] [--json]` | Local probe snapshot, optionally compared with an ordinary launch of the same executable |
| `botonomus detect [--sites ...] [--runs N] [--parallel N] [--settle S] [--reuse-profiles] [--no-interact] [--output DIR] [--root DIR] [--json]` | Public detection pages, aggregated report |
| `botonomus proxy-check FILE [--parallel N] [--timeout S] [--output FILE] [--json]` | Exit IP and location per proxy |
| `botonomus profiles list [--root DIR] [--json]` | List profiles and whether each is in use |
| `botonomus profiles remove NAME [--root DIR]` | Delete a profile; refuses one that is in use |
| `botonomus profiles warmup NAME [--duration S] [--sites URL ...] [--root DIR] [--json]` | Browse common sites humanly so the profile accumulates history (see [Profiles](profiles.md)) |
| `botonomus benchmark [--levels 1,2,5,10] [--root DIR] [--output FILE] [--json]` | Held-open concurrency on a local page |

## Browser options

`open`, `probe`, `detect`, `profiles warmup` and `benchmark` share these options:

| Option | Meaning |
|---|---|
| `--executable PATH` | Browser executable (default: chosen by `--browser`) |
| `--browser {auto,botonomus,chrome}` | `auto` (default) prefers an installed Botonomus Chromium build and falls back to Chrome; `botonomus` requires a build; `chrome` uses Google Chrome |
| `--persona {auto,off,SEED}` | Fingerprint persona: `auto` (default) derives one per profile on Botonomus Chromium, `off` disables it, an integer seed in `[0, 2**64)` requires Botonomus Chromium |
| `--proxy URL` | `scheme://[user:pass@]host:port`; validated without echoing it |
| `--geoip` | Align locale and time zone with the proxy's exit; requires `--proxy` |
| `--timezone ZONE` | IANA time zone such as `Europe/Berlin`; overrides the exit's zone |
| `--allow-timezone-mismatch` | Launch Chrome even when it cannot present the required time zone |
| `--locale TAG` | BCP 47 locale such as `en-US` |
| `--headless` | Run without a window |
| `--driver {native,patchright,playwright}` | Page driver (default `native`) |
| `--humanize {off,default,careful,fast}` | Human-like input for `session.page` with that `HumanConfig` preset (default `off`). Offered by `open`, `detect` and `profiles warmup`; `probe` and `benchmark` never act on the page |

The options map one to one onto [`BrowserConfig`](../reference/config.md) fields. An invalid value or combination, such as `--geoip` without `--proxy`, exits with code 2; `--persona 7` with stock Chrome fails at launch with `PersonaUnsupportedError`.

## Installing Botonomus Chromium

```bash
botonomus install                      # newest build of the stable channel
botonomus install --version 155.0.8059.26-bn1
botonomus binaries                     # what is installed
botonomus info                         # which executable 'auto' launches
botonomus uninstall 155.0.8059.26-bn1
```

`install` verifies the Ed25519-signed manifest and the archive's size and SHA-256 before anything is unpacked, and draws a progress bar (percent and MB) on stderr. `--manifest-url` (or `BOTONOMUS_MANIFEST_URL`) selects another channel; installs go to `BOTONOMUS_HOME`. A download or verification failure exits with code 1 and a message that never contains the URL, which may carry an access token. In 1.0.0 the release host and signing key are placeholders, so `install` cannot complete until the first Botonomus Chromium release is published; see [Personas](personas.md).

## Defaults worth knowing

- `open` and `profiles` use the profile root `.botonomus/profiles`, the same default as `BrowserConfig`. `open` uses the profile `default` unless `--profile` is given.
- `detect` writes to `artifacts/detection/<UTC time>/` and keeps its profiles in `<output>/profiles` unless `--root` is given.
- `benchmark` uses `.botonomus/benchmark` as its profile root and the levels `1,2,5,10`.
- `profiles warmup` runs for at most 300 seconds on the built-in site list unless `--duration` or `--sites` is given, with the `default` input preset unless `--humanize` names another. It contacts third-party websites.
- `info`, `probe --normal` and `detect` reports resolve the executable exactly as a launch does, so with `--browser auto` they report the installed Botonomus build when there is one.
- Results go to stdout (`--json` for machine-readable output); progress and notices go to stderr.
- `proxy-check` and `detect` reports never contain proxy credentials.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime failure (browser, network, download or verification, filesystem, locked profile, interrupted) |
| 2 | Usage or configuration error |

## Examples

```bash
# A visible session that stays open, with an explicit executable
botonomus open --profile demo --url https://example.com --executable "C:\Program Files\Google\Chrome\Application\chrome.exe"

# Compare with an ordinary launch
botonomus probe --normal --output artifacts/comparison

# Three runs per detection page through a proxy
botonomus detect --runs 3 --proxy socks5://user:pass@proxy.example:1080

# Botonomus Chromium with a fixed persona, aligned with the proxy's exit
botonomus open --browser botonomus --persona 7 --proxy socks5://user:pass@proxy.example:1080 --geoip

# Warm up a profile for five minutes with careful human input
botonomus profiles warmup shopper --duration 300 --humanize careful

# Check a proxy list and save the results
botonomus proxy-check proxies.txt --output artifacts/proxies.json

# Measure concurrency after checking free memory
botonomus benchmark --levels 10,20,40 --output artifacts/benchmark.json
```
