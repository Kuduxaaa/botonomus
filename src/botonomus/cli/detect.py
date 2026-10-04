"""``botonomus detect``: repeated runs against public bot-detection pages."""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import BrowserConfig
from ..core import Botonomus
from ..diagnostics import SITES, DetectionReport, DetectionSite, run_detection
from .common import (
    EXIT_OK,
    add_browser_arguments,
    add_json_argument,
    browser_config,
    non_negative_float,
    notice,
    positive_int,
    print_json,
)

DEFAULT_OUTPUT = Path("artifacts/detection")


def register(subparsers: Any) -> None:
    """Add the ``detect`` subcommand."""
    parser = subparsers.add_parser(
        "detect",
        help="run public bot-detection pages and aggregate their verdicts",
        description=(
            "Visits third-party detection pages. Results describe those pages on this "
            "machine, network and date only; they are not proof of undetectability."
        ),
    )
    parser.add_argument(
        "--sites", nargs="+", choices=list(SITES), default=list(SITES), metavar="SITE",
        help=f"sites to visit (default: all): {', '.join(SITES)}",
    )  # fmt: skip
    parser.add_argument("--runs", type=positive_int, default=1, help="runs per site")
    parser.add_argument("--parallel", type=positive_int, default=2, help="concurrent sessions")
    parser.add_argument(
        "--settle", type=non_negative_float, help="override per-site settle seconds"
    )
    parser.add_argument(
        "--reuse-profiles", action="store_true", help="keep one profile per site across runs"
    )
    parser.add_argument("--no-interact", action="store_true", help="skip pointer movement")
    parser.add_argument(
        "--output", type=Path, help=f"report directory (default: {DEFAULT_OUTPUT}/<UTC time>)"
    )
    parser.add_argument("--root", type=Path, help="profile root (default: <output>/profiles)")
    add_browser_arguments(parser)
    add_json_argument(parser)
    parser.set_defaults(func=run)


async def detect(
    config: BrowserConfig,
    sites: list[DetectionSite],
    args: argparse.Namespace,
    output: Path,
) -> DetectionReport:
    """Open a manager and run the detection sites.

    Args:
        config: Session configuration.
        sites: Sites to visit.
        args: Parsed ``detect`` arguments.
        output: Report directory.
    """
    async with Botonomus(args.parallel, config=config) as bot:
        return await run_detection(
            bot,
            sites,
            runs=args.runs,
            fresh_profiles=not args.reuse_profiles,
            output=output,
            interact=not args.no_interact,
            settle=args.settle,
        )


def run(args: argparse.Namespace) -> int:
    """Run detection and print the summary."""
    output = args.output or DEFAULT_OUTPUT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    config = browser_config(args, args.root or output / "profiles")
    sites = [SITES[name] for name in dict.fromkeys(args.sites)]
    notice(f"Contacting {len(sites)} third-party site(s); writing to {output}")
    report = asyncio.run(detect(config, sites, args, output))
    if args.json:
        print_json(report.to_dict())
        return EXIT_OK
    print(format_report(report))
    print(f"\nreport: {output / 'report.json'}")
    return EXIT_OK


def format_report(report: DetectionReport) -> str:
    """Render a report as a plain-text table.

    Args:
        report: The detection report.

    Returns:
        Environment lines followed by one row per site.
    """
    env = report.environment
    build = "  [Botonomus Chromium]" if env.botonomus_build else ""
    lines = [
        f"date      {env.captured_at}",
        f"browser   {env.browser_product} ({env.executable_name or 'unknown executable'}){build}",
        f"sha256    {env.executable_sha256 or 'unknown'}",
        f"driver    {env.driver}   proxy {env.proxy_type or 'none'}   os {env.os}",
        "",
        f"{'site':<22}{'runs':>5}{'pass':>6}{'fail':>6}{'blk':>5}{'unk':>5}{'err':>5}{'pass%':>8}",
    ]
    for site in report.sites:
        rate = "-" if site.pass_rate is None else f"{site.pass_rate * 100:.0f}%"
        lines.append(
            f"{site.site:<22}{site.runs:>5}{site.passed:>6}{site.failed:>6}"
            f"{site.blocked:>5}{site.unknown:>5}{site.errors:>5}{rate:>8}"
        )
        if site.error_categories:
            categories = ", ".join(f"{k}={v}" for k, v in sorted(site.error_categories.items()))
            lines.append(f"{'':<22}errors: {categories}")
    lines.append("")
    lines.append(report.limitation)
    return "\n".join(lines)
