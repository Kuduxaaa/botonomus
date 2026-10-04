"""``botonomus experiment``: interleaved A/B runs of browser configurations."""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..diagnostics.experiment import load_spec, run_experiment
from ..network import load_proxies
from .common import (
    EXIT_OK,
    add_json_argument,
    non_negative_float,
    notice,
    positive_int,
    print_json,
)

DEFAULT_OUTPUT = Path("artifacts/experiments")


def register(subparsers: Any) -> None:
    """Add the ``experiment`` subcommand."""
    parser = subparsers.add_parser(
        "experiment",
        help="compare browser configurations on detection sites with confidence intervals",
        description=(
            "Runs every arm in ARMS.toml against its sites, interleaved, one visit at a "
            "time, with a fresh profile and the next proxy per visit. Contacts "
            "third-party sites; results describe this machine, network and date only."
        ),
    )
    parser.add_argument("spec", type=Path, metavar="ARMS.toml", help="experiment file")
    parser.add_argument("--runs", type=positive_int, help="override runs per (arm, site)")
    parser.add_argument("--proxies", type=Path, help="proxy list, one URL per line, rotated")
    parser.add_argument(
        "--output", type=Path, help=f"report directory (default: {DEFAULT_OUTPUT}/<UTC time>)"
    )
    parser.add_argument("--seed", type=int, help="schedule and interaction seed")
    parser.add_argument(
        "--settle", type=non_negative_float, help="override per-site settle seconds"
    )
    parser.add_argument("--no-interact", action="store_true", help="skip pointer movement")
    add_json_argument(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Run the experiment and print the markdown summary."""
    spec = load_spec(args.spec, runs=args.runs)
    proxies = load_proxies(args.proxies) if args.proxies else []
    output = args.output or DEFAULT_OUTPUT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    visits = spec.runs * len(spec.arms) * len(spec.sites)
    notice(f"{visits} visit(s) to third-party sites; writing to {output}")
    report = asyncio.run(
        run_experiment(
            spec,
            output=output,
            proxies=proxies,
            seed=args.seed,
            interact=not args.no_interact,
            settle=args.settle,
        )
    )
    if args.json:
        print_json(report.to_dict())
    else:
        print(report.to_markdown())
        print(f"\nreport: {output / 'report.json'}")
    return EXIT_OK
