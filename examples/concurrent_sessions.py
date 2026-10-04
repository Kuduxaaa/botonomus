"""Run independent browser sessions, with queued work above the configured limit."""

import argparse
import asyncio
from pathlib import Path

from botonomus import Botonomus, BrowserConfig
from botonomus.diagnostics import ProbeServer


async def main(args, url):
    async with Botonomus(args.limit, config=BrowserConfig(executable_path=args.executable)) as bot:

        async def work(index):
            async with bot.open(profile=f"worker-{index:03d}") as session:
                await session.page.goto(url)
                print(f"Session {index}: {await session.page.title()}")

        await asyncio.gather(*(work(index) for index in range(args.jobs)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    with ProbeServer() as server:
        asyncio.run(main(args, server.url))
