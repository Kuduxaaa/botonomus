# Client: a real browser called like httpx

`botonomus.Client` is a lightweight layer over one shared Chrome. You call it like
`httpx`, and every request is loaded by a real browser tab, so cookies, scripts and
anti-bot checks behave as they would for a person. It is an addition:
`Botonomus`, `bot.open()`, profiles and personas are unchanged and remain the right tool
when you need a long-lived browser per account.

```python
import botonomus

response = await botonomus.get("https://example.com")
print(response.status, response.via, response.text[:80])
```

A module-level call opens a temporary client for one request, like `httpx.get`. Keep a
`Client` open to reuse the browser, its cookies and the HTTP fast path:

```python
from botonomus import BrowserConfig, Client

async with Client(config=BrowserConfig(headless=False), max_tabs="auto") as client:
    page = await client.get("https://example.com/products", params={"page": 2})
    login = await client.post("https://example.com/login", data={"user": "me", "pw": "..."})
    api = await client.post("https://example.com/api/cart", json={"sku": 42})
    print(api.json())
```

## Why it is lighter

| | `Botonomus.open` | `Client` |
|---|---|---|
| Browser processes | One Chrome per session | One Chrome for the client |
| Per concurrent request | Browser, GPU and network processes, renderer, profile on disk | One renderer; in-memory context, nothing on disk |
| Cookies | In the profile directory | In the context (or an identity file) |

Every request runs in an in-memory *browser context*: its own cookies, storage and
cache, isolated like a separate browser, while the browser, GPU and network processes
are shared. Chrome never shares a renderer between contexts.

Measured on the development machine (Windows 11, stock Chrome, a local page, 6 held-open
tabs, `botonomus benchmark --levels 1,3,6`): about 224 MiB of host memory per session
with `--mode sessions` against about 133 MiB per context with `--mode contexts`, and half
the startup time. The saving grows with the number of tabs because the shared processes
are paid once. Measure your own server before sizing:

```bash
botonomus benchmark --mode contexts --levels 1,5,10,20
```

## The HTTP fast path

With `mode="auto"` (the default) and the optional extra installed, a host that a tab has
loaded without a challenge is served by an HTTP client for later requests:

```bash
pip install "botonomus[http]"
```

- The fast path uses `curl_cffi`, which reproduces Chrome's TLS ClientHello and HTTP/2
  settings. It impersonates the newest Chrome target not newer than your browser (at
  most 12 majors older), sends the User-Agent, client hints and Accept-Language that
  your browser actually sent, and uses the context's cookies. Cookies the server sets
  are written back to the browser context.
- If a fast-path response looks like an anti-bot challenge (Cloudflare, DataDome,
  PerimeterX markers, or 429), the request is sent again from a tab. After two such
  fallbacks the host stays on tabs for the rest of the client's life.
- A response that came from a tab has `response.via == "browser"` and the rendered DOM
  in `response.html`; a fast-path response has `via == "http"` and `html is None`.
- Without the extra, or without a matching target, every request uses a tab.
  `mode="browser"` forces that.

Some sites check every request with JavaScript. The fast path cannot pass those checks,
so those requests fall back to tabs automatically.

## Pages: full interaction

When you need clicks, typing or scrolling, open a tab in the same context:

```python
async with client.page("https://example.com/login") as page:
    await page.locator("#user").fill("me")
    await page.get_by_role("button", name="Sign in").click()
```

With `BrowserConfig(humanize=True)` the page is a `HumanPage`. Cookies and
`localStorage` stay in the context after the tab closes.

## Identities: accounts without a profile directory

```python
async with client.identity("acct-01", proxy="http://user:pass@proxy:8080") as me:
    await me.post("https://example.com/login", data={...})
    async with me.page("https://example.com/account") as page:
        ...
```

An identity is a context whose state survives between uses: cookies, `localStorage` per
origin and its proxy are saved to `identities/acct-01/state.json` (next to the profile
root) when the block exits. They are restored into a fresh context next time.
`localStorage` is restored locally without contacting the site. The file is a few
kilobytes instead of a profile directory. Like profiles, an identity can be open in only
one place at a time, across processes.

- The file contains session cookies and proxy credentials. It is written readable by
  the owner only (mode 0600 on Linux and macOS; on Windows the identity directory's
  inherited permissions are replaced with full control for the current user) and is
  never logged. Treat it like a password.
- IndexedDB, Cache Storage, service workers and the HTTP cache are not carried. For sites
  that keep login state there, use `client.identity(name, backend="profile")`, which
  opens the full Chrome profile `name` (the same one `bot.open(profile=name)` uses) in
  its own browser, through the same API.

## Options

| Option | Default | Meaning |
|---|---|---|
| `config` | `BrowserConfig()` | Executable, persona, proxy, headless, humanize. `driver` must be `native`. |
| `proxy` | `None` | Proxy for the client's contexts (credentials allowed); `None` uses `config.proxy`. |
| `mode` | `"auto"` | `"auto"`: tab first, then the fast path per host. `"browser"`: always a tab. |
| `max_tabs` | `"auto"` | Concurrent tabs; `"auto"` sizes from free memory (about 150 MB per tab after 300 MB for the browser) and CPUs (4 per CPU). |
| `block` | `()` | Skip `image`, `font`, `media` or `stylesheet` loads. Saves bandwidth and CPU; some sites notice. |
| `fresh_context` | `False` | A new, empty context per request instead of one shared context. |
| `timeout` | `30` | Seconds per request. |
| `wait` | `"load"` | `"load"` or `"domcontentloaded"`. |
| `identity_root` | `<profile_root>/../identities` | Where identity files live. |

Per request: `params`, `headers`, `data` (form mapping, `str` or `bytes`), `json`,
`timeout`, `wait` and `settle` (extra seconds for scripts to run before the page is
read). Non-GET methods, headers and bodies are applied to the tab's navigation request
itself, as a form submission would send them.

## Response

`url` (after redirects), `status`, `headers` (case-insensitive), `content` (raw bytes),
`text`, `json()`, `html` (rendered DOM, tabs only), `cookies` (names and values for this
URL), `elapsed`, `via`, `ok` and `raise_for_status()`, which raises
`botonomus.HTTPStatusError`.

## Notes and limits

- Contexts are Chrome's off-the-record kind. Some detectors can tell incognito windows
  apart (for example a smaller storage quota). Check your target with
  `botonomus detect` and `botonomus consistency`.
- A persona applies to the whole shared browser, so every context of a client presents
  the same hardware. Use separate clients (or `Botonomus.open`) for separate personas.
- If the browser process dies, the client relaunches it on the next request. It
  recreates each context with its last known cookies and `localStorage`, and retries the
  interrupted request once.
- The shared browser's throwaway profile lives in the system temp directory
  (`botonomus-client-*`), never among your named profiles, and is deleted on close.
- Client browsers always launch with
  `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`, so a per-context proxy
  cannot leak the real address over WebRTC.
