"""Opt-in: run detection pages from Python, including a site of your own.

For the built-in catalogue alone, the CLI is simpler::

    botonomus detect --runs 3 --driver patchright

This contacts third-party websites. Results describe those pages on this machine,
network and date only; they are not proof of undetectability.
"""

import asyncio
from pathlib import Path

from botonomus import Botonomus, BrowserConfig
from botonomus.diagnostics import SITES, DetectionSite, run_detection

# An extractor reports only what the page itself states; anything else is "unknown".
MY_SITE = DetectionSite(
    "my-check",
    "https://deviceandbrowserinfo.com/are_you_a_bot",
    ready="document.body.innerText.includes('You are')",
    extractor="""() => {
      const text = document.body.innerText;
      if (text.includes('You are human')) return {verdict: 'pass', details: {}};
      if (text.includes('You are a bot')) return {verdict: 'fail', details: {}};
      return {verdict: 'unknown', details: {}};
    }""",
)


async def main() -> None:
    output = Path("artifacts/detection/example")
    config = BrowserConfig(profile_root=output / "profiles")
    async with Botonomus(max_instances=2, config=config) as bot:
        report = await run_detection(bot, [SITES["sannysoft"], MY_SITE], runs=2, output=output)
    for site in report.sites:
        print(f"{site.site}: {site.passed}/{site.runs} pass, {site.errors} errors")
    print(f"Full report: {output / 'report.json'}")


if __name__ == "__main__":
    asyncio.run(main())
