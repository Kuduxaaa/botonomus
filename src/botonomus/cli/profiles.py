"""``botonomus profiles``: list, remove and warm up profiles."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..config import BrowserConfig
from ..core import Botonomus
from ..errors import ProfileInUseError
from ..human import Human, HumanConfig, HumanPage
from ..profiles import DEFAULT_SITES, WarmupReport, list_profiles, remove_profile, warm_up
from .browse import DEFAULT_ROOT
from .common import (
    EXIT_FAILURE,
    EXIT_OK,
    add_browser_arguments,
    add_json_argument,
    browser_config,
    error,
    notice,
    positive_float,
    print_json,
)

DEFAULT_WARMUP_SECONDS = 300.0


def http_url(value: str) -> str:
    """``argparse`` type for ``http``/``https`` URLs with a host.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is not such a URL.
    """
    scheme, _, rest = value.partition("://")
    if scheme.lower() not in ("http", "https") or not rest or rest.startswith("/"):
        raise argparse.ArgumentTypeError(f"expected an http(s) URL, got {value!r}")
    return value


def register(subparsers: Any) -> None:
    """Add the ``profiles`` subcommand with ``list``, ``remove`` and ``warmup``."""
    parser = subparsers.add_parser("profiles", help="list, remove or warm up profiles")
    actions = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    listing = actions.add_parser("list", help="list profiles and whether they are in use")
    listing.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="profile root")
    add_json_argument(listing)
    listing.set_defaults(func=run_list)
    removing = actions.add_parser("remove", help="delete a profile that is not in use")
    removing.add_argument("name", help="profile name")
    removing.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="profile root")
    removing.set_defaults(func=run_remove)
    warming = actions.add_parser(
        "warmup",
        help="browse common sites humanly so a profile accumulates history",
        description=(
            "Visits benign, high-traffic sites, reads and scrolls, and follows a few "
            "same-site links with human-like input. Never types or submits forms. "
            "Contacts third-party websites."
        ),
    )
    warming.add_argument("name", help="profile name")
    warming.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="profile root")
    warming.add_argument(
        "--duration",
        type=positive_float,
        default=DEFAULT_WARMUP_SECONDS,
        metavar="S",
        help=f"upper bound in seconds (default: {DEFAULT_WARMUP_SECONDS:.0f})",
    )
    warming.add_argument(
        "--sites",
        nargs="+",
        type=http_url,
        metavar="URL",
        help="start URLs (default: a built-in list of news, reference and shopping sites)",
    )
    add_browser_arguments(warming)
    add_json_argument(warming)
    warming.set_defaults(func=run_warmup)


def run_list(args: argparse.Namespace) -> int:
    """Print the profiles under ``--root``."""
    profiles = list_profiles(args.root)
    if args.json:
        print_json(profiles)
        return EXIT_OK
    if not profiles:
        print(f"no profiles in {args.root}")
    for profile in profiles:
        state = "in use" if profile.in_use else "free"
        print(f"{profile.name:<40} {state:<7} {profile.modified}")
    return EXIT_OK


def run_remove(args: argparse.Namespace) -> int:
    """Delete one profile; refuse while any session holds it."""
    try:
        path = remove_profile(args.root, args.name)
    except ProfileInUseError:
        error(f"profile {args.name!r} is in use; close its session first")
        return EXIT_FAILURE
    print(f"removed {path}")
    return EXIT_OK


async def warmup(
    config: BrowserConfig, name: str, duration: float, sites: Sequence[str]
) -> WarmupReport:
    """Open profile ``name`` and run [`warm_up`][botonomus.profiles.warm_up] on it.

    Input goes through a [`Human`][botonomus.human.Human] using the ``--humanize``
    preset, or the ``default`` preset when humanize is off.

    Args:
        config: Session configuration.
        name: Profile name.
        duration: Upper bound in seconds.
        sites: Start URLs.

    Returns:
        The warm-up report.
    """
    async with Botonomus(config=config) as bot, bot.open(profile=name) as session:
        page = session.page
        if isinstance(page, HumanPage):  # share the session's pointer state
            raw, human = page.raw, page.human
        else:
            raw, human = page, Human(page, config=HumanConfig.preset("default"))
        return await warm_up(raw, sites=sites, duration=duration, human=human)


def run_warmup(args: argparse.Namespace) -> int:
    """Warm up one profile and print the report."""
    config = browser_config(args, args.root)
    sites = list(dict.fromkeys(args.sites)) if args.sites else list(DEFAULT_SITES)
    notice(f"Warming up {args.name!r} for up to {args.duration:.0f}s on {len(sites)} site(s)")
    report = asyncio.run(warmup(config, args.name, args.duration, sites))
    if args.json:
        print_json(report)
        return EXIT_OK
    print(f"sites visited   {report.sites_visited}")
    print(f"sites failed    {report.sites_failed}")
    print(f"links followed  {report.links_followed}")
    print(f"elapsed         {report.elapsed:.1f}s")
    return EXIT_OK
