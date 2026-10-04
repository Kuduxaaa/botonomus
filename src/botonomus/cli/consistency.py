"""``botonomus consistency``: local persona and context consistency checks."""

import argparse
import asyncio
import tempfile
from pathlib import Path
from typing import Any

from ..diagnostics.consistency import run_consistency
from .common import (
    EXIT_FAILURE,
    EXIT_OK,
    add_browser_arguments,
    add_json_argument,
    browser_config,
    print_json,
)


def register(subparsers: Any) -> None:
    """Add the ``consistency`` subcommand."""
    parser = subparsers.add_parser(
        "consistency",
        help="check readback stability and identity agreement across contexts (no network)",
        description=(
            "Loads a loopback page that renders canvas, WebGL and audio twice and reads "
            "identity values in the page, an iframe and dedicated, shared and service "
            "workers. Exits 1 when any check fails."
        ),
    )
    add_browser_arguments(parser, humanize=False)
    add_json_argument(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Run the checks and print them."""
    with tempfile.TemporaryDirectory(
        prefix="botonomus-consistency-", ignore_cleanup_errors=True
    ) as root:
        report = asyncio.run(run_consistency(browser_config(args, Path(root))))
    if args.json:
        print_json(report.to_dict())
    else:
        print(f"browser {report.product}")
        for check in report.checks:
            print(f"  {'PASS' if check.passed else 'FAIL'}  {check.name:<16} {check.observed}")
    return EXIT_OK if report.ok else EXIT_FAILURE
