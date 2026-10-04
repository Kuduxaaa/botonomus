# Proxies and geo consistency

## Configuring a proxy

```python
from botonomus import BrowserConfig

BrowserConfig(proxy="http://proxy.example:8080")
BrowserConfig(proxy="https://user:pass@proxy.example:8443")  # TLS to the proxy
BrowserConfig(proxy="socks5://user:pass@proxy.example:1080")
```

The URL is `scheme://[user:pass@]host:port` with scheme `http`, `https` or `socks5`. Percent-encoded credentials are decoded. A path, query or fragment, or a password without a username, raises `ConfigurationError`, and the message never echoes the URL.

`parse_proxy` gives you the parsed form; credentials stay out of `repr`:

```pycon
>>> from botonomus import parse_proxy
>>> spec = parse_proxy("http://user:secret@proxy.example:8080")
>>> spec
ProxySpec(scheme='http', host='proxy.example', port=8080)
>>> spec.server, spec.has_credentials
('http://proxy.example:8080', True)
```

## How traffic flows

- **No credentials:** the proxy goes straight to Chrome's `--proxy-server`.
- **With credentials:** Chrome cannot take credentials on the command line, and answering proxy-auth prompts over CDP is observable. Botonomus instead starts a loopback SOCKS5 [`ProxyForwarder`](../reference/network.md) per browser. Chrome connects to it without authentication, and each connection is tunnelled through the upstream proxy with HTTP `CONNECT` or SOCKS5 username/password authentication. Hostnames resolve at the proxy. The forwarder is a byte tunnel: TLS is negotiated end to end by Chrome's own network stack, so the TLS fingerprint is Chrome's.
- Credentials never appear on the command line, in `repr()`, in error messages or in logs.

### WebRTC

Any proxy also sets the profile preference `webrtc.ip_handling_policy` to `disable_non_proxied_udp` before launch (Chrome reads the policy only from this preference; the `--force-webrtc-ip-handling-policy` switch, also passed, works only in headless shells). WebRTC UDP would otherwise bypass the proxy and reveal the machine's real address. Botonomus blocks non-proxied UDP; it does not substitute a fake address, so pages see no ICE candidates.

## Checking proxies

Before using a list, check that each proxy works and where it exits:

```bash
botonomus proxy-check proxies.txt --parallel 16 --timeout 20 --output checks.json
```

The file has one URL per line; blank lines and lines starting with `#` are ignored. Each result reports `host:port` (never credentials), whether the lookup succeeded, an error category (`invalid`, `unreachable`, `timeout`, `tls`, `upstream`) and the exit: IP, country, region, city, time zone, ISP, and, from ip-api.com, whether the address is a data-centre range or a known proxy/VPN/Tor exit.

From Python:

```python
from botonomus.network import check_proxies, load_proxies

results = await check_proxies(load_proxies("proxies.txt"), parallel=16)
for check in results:
    print(check.address, check.ok, check.error, check.exit and check.exit.country_code)
```

## Geo consistency

A German exit with an `en-US` browser in a US time zone is a contradiction detectors look for. With `geoip=True`, Botonomus looks up the proxy's exit before each launch and aligns the browser with it:

```python
from botonomus import Botonomus, BrowserConfig

config = BrowserConfig(proxy="socks5://user:pass@proxy.example:1080", geoip=True)
async with Botonomus(config=config) as bot, bot.open(profile="de-01") as session:
    print(session.exit.ip, session.exit.country_code, session.exit.timezone)
```

What happens:

1. The lookup runs **through the proxy**, so the service sees the exit address. Providers are tried in order: ip-api.com (plain HTTP, rich data, about 45 requests a minute per exit), then ipinfo.io over TLS negotiated inside the tunnel.
2. Results are cached per proxy (including credentials, since providers often pin a sticky exit to the username) for one hour, and concurrent sessions on the same proxy share one lookup. Failures are not cached.
3. **Locale:** unless you set `locale`, the exit country's most common locale is applied with `--lang` and `--accept-lang` (for example `DE` becomes `de-DE`).
4. **Time zone:** unless you set `timezone`, the exit's IANA zone is used.
    - On Botonomus Chromium it is presented with the `--bn-timezone` switch.
    - Stock Chrome always uses the host's time zone (on Windows it ignores the `TZ` variable, and CDP time zone emulation is avoided as an inconsistency risk). If the host's current UTC offset differs from the exit's, `open()` raises `GeoMismatchError`. Offsets are compared, so `Europe/Berlin` and `Europe/Paris` agree.
5. If every provider fails, `open()` raises `GeoLookupError` with each failure chained.

To accept a time-zone mismatch on Chrome knowingly:

```python
BrowserConfig(proxy="...", geoip=True, allow_timezone_mismatch=True)
```

Setting `timezone="Europe/Berlin"` without `geoip` applies the same rules: Botonomus Chromium presents it, Chrome requires the host to match.

### Custom lookup providers

`exit_info` accepts any object implementing the [`GeoProvider`](../reference/network.md) protocol (`name`, `host`, `port`, `tls_context`, `request`, `parse`):

```python
from botonomus import parse_proxy
from botonomus.network import IpInfoProvider, exit_info

spec = parse_proxy("http://user:pass@proxy.example:8080")
info = await exit_info(spec, providers=[IpInfoProvider()])
print(info.ip, info.city, info.locale())
```

The manager's own lookups use the default providers.

## IP reputation

Geo consistency removes a contradiction; it does not make an address trustworthy. Scores such as reCAPTCHA v3 depend heavily on IP reputation and profile history. Residential or mobile exits and [warmed-up profiles](profiles.md#warm-up) matter more than any browser setting.
