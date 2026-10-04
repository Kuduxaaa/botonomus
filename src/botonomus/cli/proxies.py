"""``botonomus proxy-check``: reachability, exit IP and location for a proxy list.

Results identify proxies by ``host:port`` and scheme only; credentials never reach
output, files or error messages.
"""

import argparse
import asyncio
import importlib
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import parse_proxy
from ..errors import ConfigurationError
from ..network import exit_info, load_proxies
from .common import (
    EXIT_OK,
    add_json_argument,
    plain,
    positive_float,
    positive_int,
    print_json,
    write_json,
)

_SECRET_KEYS = frozenset({"username", "password", "url", "proxy_url"})


def register(subparsers: Any) -> None:
    """Add the ``proxy-check`` subcommand."""
    parser = subparsers.add_parser("proxy-check", help="check every proxy in a file")
    parser.add_argument("file", type=Path, help="one proxy URL per line; # starts a comment")
    parser.add_argument("--parallel", type=positive_int, default=16, help="concurrent checks")
    parser.add_argument("--timeout", type=positive_float, default=20.0, help="seconds per proxy")
    parser.add_argument("--output", type=Path, help="also write results to this JSON file")
    add_json_argument(parser)
    parser.set_defaults(func=run)


async def check_one(proxy: str, limiter: asyncio.Semaphore, timeout: float) -> dict[str, Any]:
    """Look up one proxy's exit through the proxy itself.

    Args:
        proxy: Proxy URL (validated).
        limiter: Bounds concurrent checks.
        timeout: Seconds allowed.

    Returns:
        ``proxy`` (``host:port``), ``scheme`` and ``ok``, plus the exit fields when it
        worked or ``error`` (exception class name) when it did not.
    """
    spec = parse_proxy(proxy)
    base: dict[str, Any] = {"proxy": spec.address, "scheme": spec.scheme}
    async with limiter:
        try:
            info = await exit_info(spec, timeout)
        except Exception as exc:  # one bad proxy is a result, not a failure of the command
            return {**base, "ok": False, "error": type(exc).__name__}
    return {**base, "ok": True, **asdict(info)}


async def check_all(proxies: list[str], parallel: int, timeout: float) -> list[dict[str, Any]]:
    """Check every proxy, preferring ``botonomus.network.check_proxies`` if present.

    Args:
        proxies: Proxy URLs.
        parallel: Maximum concurrent checks.
        timeout: Seconds per proxy.

    Returns:
        One redacted result dict per proxy, in input order.
    """
    shared = getattr(importlib.import_module("botonomus.network"), "check_proxies", None)
    if callable(shared):
        try:
            results = await shared(proxies, parallel=parallel, timeout=timeout)
            return [
                redact(flatten(url, plain(item)))
                for url, item in zip(proxies, results, strict=False)
            ]
        except TypeError:
            pass  # a different signature: fall back to the local implementation
    limiter = asyncio.Semaphore(parallel)
    return list(await asyncio.gather(*(check_one(p, limiter, timeout) for p in proxies)))


def flatten(url: str, result: Any) -> Any:
    """Convert a `ProxyCheck` dict into the ``check_one`` shape the output expects.

    Other shapes pass through unchanged.

    Args:
        url: The proxy URL the result belongs to, for its scheme.
        result: One plain (JSON-compatible) shared-checker result.
    """
    if not isinstance(result, dict) or "address" not in result:
        return result
    flat: dict[str, Any] = {"proxy": result["address"]}
    try:
        flat["scheme"] = parse_proxy(url).scheme
    except ConfigurationError:
        pass
    flat["ok"] = result.get("ok", False)
    if flat["ok"]:
        flat.update(result.get("exit") or {})
    else:
        flat["error"] = result.get("error")
    return flat


def redact(value: Any) -> Any:
    """Remove credentials from a result.

    Drops credential-bearing keys and replaces any string that parses as a proxy URL
    with its ``host:port``.
    """
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if k not in _SECRET_KEYS}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and "://" in value:
        try:
            return parse_proxy(value).address
        except ConfigurationError:
            return value
    return value


def run(args: argparse.Namespace) -> int:
    """Check the proxies and print the results."""
    proxies = load_proxies(args.file)
    results = asyncio.run(check_all(proxies, args.parallel, args.timeout))
    if args.output is not None:
        write_json(args.output, results)
    if args.json:
        print_json(results)
        return EXIT_OK
    working = [r for r in results if r.get("ok")]
    print(f"{len(working)}/{len(results)} working")
    for result in results:
        name = f"{result.get('scheme', '?')}://{result.get('proxy', '?')}"
        if result.get("ok"):
            print(
                f"{name:<32} {result.get('ip', ''):<16} {result.get('country_code', ''):<3}"
                f"{result.get('timezone', ''):<22}{result.get('latency', 0.0):>6.2f}s "
                f"hosting={result.get('hosting')!s:<5} proxy={result.get('flagged_proxy')!s:<5} "
                f"{result.get('isp', '')}"
            )
        else:
            print(f"{name:<32} FAILED {result.get('error', 'unknown')}")
    if working:
        print("timezones:", dict(Counter(str(r.get("timezone")) for r in working)))
    return EXIT_OK
