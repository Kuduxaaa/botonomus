"""``botonomus open``: a visible persistent session that stays open until Enter."""

import argparse
import asyncio
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..config import BrowserConfig
from ..core import Botonomus
from .common import EXIT_OK, add_browser_arguments, browser_config, notice

DEFAULT_ROOT = Path(".botonomus/profiles")


def register(subparsers: Any) -> None:
    """Add the ``open`` subcommand."""
    parser = subparsers.add_parser("open", help="open a visible browser for a profile")
    parser.add_argument("--profile", default="default", help="profile name (default: default)")
    parser.add_argument("--url", default="about:blank", help="page to open first")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="profile root directory")
    add_browser_arguments(parser)
    parser.set_defaults(func=run)


async def wait_for_enter() -> None:
    """Return when a line (or end of file) arrives on stdin.

    Reading happens on a daemon thread so Ctrl+C can end the program without waiting
    for a blocked ``input()`` call.
    """
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def finish() -> None:
        if not done.done():
            done.set_result(None)

    def read() -> None:
        with suppress(Exception):
            sys.stdin.readline()
        with suppress(RuntimeError):  # loop already closed after Ctrl+C
            loop.call_soon_threadsafe(finish)

    threading.Thread(target=read, name="botonomus-stdin", daemon=True).start()
    await done


async def browse(config: BrowserConfig, profile: str, url: str) -> None:
    """Open ``profile``, navigate to ``url`` and wait for Enter.

    Args:
        config: Session configuration.
        profile: Profile name.
        url: First page.
    """
    async with Botonomus(config=config) as bot, bot.open(profile=profile) as session:
        if url != "about:blank":
            await session.page.goto(url)
        notice(f"Opened profile {session.profile!r}. Press Enter or Ctrl+C to close.")
        await wait_for_enter()


def run(args: argparse.Namespace) -> int:
    """Open the browser; Ctrl+C is a normal way to close it."""
    config = browser_config(args, args.root)
    try:
        asyncio.run(browse(config, args.profile, args.url))
    except KeyboardInterrupt:
        notice("Closed.")
    return EXIT_OK
