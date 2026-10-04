"""``botonomus trace`` and ``botonomus trace-diff``: fingerprinting API reads of a page."""

import argparse
import asyncio
import dataclasses
import tempfile
from pathlib import Path
from typing import Any

from ..core import Botonomus
from ..diagnostics.apitrace import (
    Trace,
    TraceServer,
    diff_traces,
    format_diff,
    trace_flags,
    trace_page,
)
from ..errors import ConfigurationError
from .common import (
    EXIT_OK,
    add_browser_arguments,
    add_json_argument,
    browser_config,
    notice,
    positive_float,
    print_json,
)


def register(subparsers: Any) -> None:
    """Add ``trace`` and ``trace-diff``."""
    parser = subparsers.add_parser(
        "trace",
        help="record which fingerprinting APIs a page reads (diagnostic; visible to the page)",
        description=(
            "Hooks fingerprinting APIs in the page's main world, its same-process frames "
            "and out-of-process iframes (not workers), visits URL and writes every "
            "distinct read with its value. The hooks are visible to the page: compare "
            "two browsers traced the same way, never use this for real sessions."
        ),
    )
    parser.add_argument("url", help="page to trace")
    parser.add_argument("--output", type=Path, required=True, help="trace JSON file to write")
    parser.add_argument(
        "--settle", type=positive_float, default=15.0, help="seconds to wait after load"
    )
    parser.add_argument(
        "--root", type=Path, help="profile root to keep (default: a temporary directory)"
    )
    # The tracer drives CDP directly, so only the native driver is offered.
    add_browser_arguments(parser, driver=False, humanize=False)
    parser.set_defaults(func=run_trace)

    diff = subparsers.add_parser("trace-diff", help="compare two trace files")
    diff.add_argument("a", type=Path, help="first trace (e.g. stock Chrome)")
    diff.add_argument("b", type=Path, help="second trace")
    add_json_argument(diff)
    diff.set_defaults(func=run_diff)


async def _trace(args: argparse.Namespace) -> Trace:
    # Chrome may hold profile files briefly after exit on Windows.
    with tempfile.TemporaryDirectory(
        prefix="botonomus-trace-", ignore_cleanup_errors=True
    ) as scratch:
        config = browser_config(args, args.root or Path(scratch))
        config = dataclasses.replace(config, extra_args=trace_flags(config.extra_args))
        with TraceServer() as server:
            async with Botonomus(1, config=config) as bot, bot.open(profile="trace") as session:
                return await trace_page(session.page, args.url, server, settle=args.settle)


def run_trace(args: argparse.Namespace) -> int:
    """Trace one page and write the JSON file."""
    notice(f"Tracing {args.url}; the tracer is visible to the page (diagnostic only)")
    trace = asyncio.run(_trace(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(trace.to_json(), encoding="utf-8")
    if not trace.records:
        notice("warning: no fingerprinting reads were recorded; the page may not have run")
    print(f"{len(trace.records)} record(s) from {trace.product} -> {args.output}")
    return EXIT_OK


def _load(path: Path) -> Trace:
    try:
        return Trace.from_json(path.read_text(encoding="utf-8"))
    except (ValueError, KeyError, TypeError, AttributeError):  # incl. JSON/Unicode errors
        raise ConfigurationError(f"{path.name} is not a trace file") from None


def run_diff(args: argparse.Namespace) -> int:
    """Print the differences between two traces."""
    a, b = _load(args.a), _load(args.b)
    diff = diff_traces(a, b)
    if args.json:
        print_json(diff)
    else:
        print(f"a: {a.product} ({len(a.records)} records)")
        print(f"b: {b.product} ({len(b.records)} records)")
        print(format_diff(diff, args.a.stem, args.b.stem))
    return EXIT_OK
