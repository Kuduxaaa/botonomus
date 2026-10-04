"""``botonomus benchmark``: how many held-open sessions this machine sustains."""

import argparse
import asyncio
from pathlib import Path
from typing import Any

from ..diagnostics import ProbeServer, parse_levels, run_benchmark
from .common import (
    EXIT_FAILURE,
    EXIT_OK,
    add_browser_arguments,
    add_json_argument,
    browser_config,
    print_json,
    write_json,
)


def _levels(value: str) -> list[int]:
    try:
        return parse_levels(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected comma-separated positive integers, got {value!r}"
        ) from None


def register(subparsers: Any) -> None:
    """Add the ``benchmark`` subcommand."""
    parser = subparsers.add_parser(
        "benchmark",
        help="measure held-open concurrency on a local page",
        description=(
            "Opens up to LEVEL sessions at once for each level. Stops early when free "
            "memory drops under 15% or launches fail twice. Large levels are expensive."
        ),
    )
    parser.add_argument(
        "--levels", type=_levels, default=[1, 2, 5, 10], help="e.g. 1,2,5,10 (default)"
    )
    parser.add_argument(
        "--root", type=Path, default=Path(".botonomus/benchmark"), help="profile root"
    )
    parser.add_argument("--output", type=Path, help="also write results to this JSON file")
    add_browser_arguments(parser, humanize=False)
    add_json_argument(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Run the levels and print one line per level."""
    config = browser_config(args, args.root)
    with ProbeServer() as server:
        reports = asyncio.run(run_benchmark(args.levels, config, server.url))
    if args.output is not None:
        write_json(args.output, reports)
    if args.json:
        print_json(reports)
    else:
        for report in reports:
            peak = report["peak_host_used_bytes"] / 2**30
            flag = "  stopped early" if report["stopped_early"] else ""
            print(
                f"level {report['requested']:>4}: active {report['active']:>4}, "
                f"failures {report['failures']}, startup {report.get('startup_seconds', 0):.1f}s, "
                f"peak host memory {peak:.1f} GiB{flag}"
            )
    any_active = any(report["active"] for report in reports)
    return EXIT_OK if any_active else EXIT_FAILURE
