"""Recognizing anti-bot challenge pages, which only a real browser can pass."""

import re
from collections.abc import Mapping

# Markers that only appear on interstitials (not on pages that merely mention a vendor):
# Cloudflare's challenge options object and titles, DataDome's challenge host and
# PerimeterX's captcha container.
_BODY_MARKERS = re.compile(
    r"_cf_chl_opt|<title>\s*just a moment|attention required! \| cloudflare"
    r"|captcha-delivery\.com|id=[\"']px-captcha",
    re.IGNORECASE,
)


def is_challenge(status: int, headers: Mapping[str, str], body: str) -> bool:
    """Whether a response is an anti-bot interstitial rather than the page.

    Args:
        status: HTTP status.
        headers: Response headers (case-insensitive lookups).
        body: Decoded body; only the first 64 KB are inspected.
    """
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if status == 429:
        return True
    if _BODY_MARKERS.search(body[:65536]):
        return True
    return status in (403, 503) and "x-datadome" in headers
