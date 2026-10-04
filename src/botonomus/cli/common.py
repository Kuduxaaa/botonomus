"""Argument helpers, output and exit codes shared by every subcommand."""

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Final

from ..config import DRIVERS, BrowserConfig, parse_proxy
from ..config.browser import BROWSER_CHOICES
from ..errors import ConfigurationError
from ..fingerprint import PersonaSpec
from ..human import HumanConfig
from ..human.config import PresetName

EXIT_OK: Final = 0
"""The command did what was asked."""
EXIT_FAILURE: Final = 1
"""The command ran but failed (browser, network, filesystem, locked profile)."""
EXIT_USAGE: Final = 2
"""The command line or a configuration value was invalid."""

HUMANIZE_CHOICES: Final = ("off", "default", "careful", "fast")
"""Values of ``--humanize``: ``off`` or a `HumanConfig.preset` name."""

_PRESETS: Final[dict[str, PresetName]] = {
    "default": "default",
    "careful": "careful",
    "fast": "fast",
}


def positive_int(value: str) -> int:
    """``argparse`` type for integers of at least 1.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is not a positive integer.
    """
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")
    return number


def positive_float(value: str) -> float:
    """``argparse`` type for finite numbers greater than 0.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is not a positive number.
    """
    try:
        number = float(value)
    except ValueError:
        number = 0.0
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError(f"expected a positive number, got {value!r}")
    return number


def non_negative_float(value: str) -> float:
    """``argparse`` type for finite numbers of at least 0.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is negative or not a number.
    """
    try:
        number = float(value)
    except ValueError:
        number = -1.0
    if not 0 <= number < float("inf"):
        raise argparse.ArgumentTypeError(f"expected a non-negative number, got {value!r}")
    return number


def proxy_url(value: str) -> str:
    """``argparse`` type that validates a proxy URL without echoing it.

    Raises:
        argparse.ArgumentTypeError: If the URL is malformed. The message never
            contains the value, which may hold credentials.
    """
    try:
        parse_proxy(value)
    except ConfigurationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def persona_spec(value: str) -> PersonaSpec:
    """``argparse`` type for ``--persona``: ``auto``, ``off`` or a 64-bit integer seed.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is none of those.
    """
    if value == "auto":
        return "auto"
    if value == "off":
        return "off"
    try:
        seed = int(value)
    except ValueError:
        seed = -1
    if not 0 <= seed < 2**64:
        raise argparse.ArgumentTypeError(
            f"expected 'auto', 'off' or an integer seed in [0, 2**64), got {value!r}"
        )
    return seed


def add_browser_arguments(
    parser: argparse.ArgumentParser, *, driver: bool = True, humanize: bool = True
) -> None:
    """Add the shared browser options.

    Always adds ``--executable``, ``--proxy``, ``--locale``, ``--headless``,
    ``--browser``, ``--persona``, ``--geoip``, ``--timezone`` and
    ``--allow-timezone-mismatch``.

    Args:
        parser: The subcommand parser.
        driver: Whether to offer ``--driver``.
        humanize: Whether to offer ``--humanize``; pointless for commands that never
            act on a page through ``session.page``.
    """
    group = parser.add_argument_group("browser")
    group.add_argument(
        "--executable",
        type=Path,
        help="browser executable (default: chosen by --browser)",
    )
    group.add_argument(
        "--proxy", type=proxy_url, metavar="URL", help="scheme://[user:pass@]host:port"
    )
    group.add_argument("--locale", help="BCP 47 locale such as en-US")
    group.add_argument("--headless", action="store_true", help="run without a window")
    if driver:
        group.add_argument("--driver", choices=DRIVERS, default="native")
    group.add_argument(
        "--browser",
        choices=BROWSER_CHOICES,
        default="auto",
        help="auto prefers an installed Botonomus Chromium build, else Chrome (default)",
    )
    group.add_argument(
        "--persona",
        type=persona_spec,
        default="auto",
        metavar="{auto,off,SEED}",
        help="fingerprint persona; a seed requires Botonomus Chromium (default: auto)",
    )
    group.add_argument(
        "--geoip",
        action="store_true",
        help="align locale and time zone with the proxy's exit (requires --proxy)",
    )
    group.add_argument("--timezone", metavar="ZONE", help="IANA time zone such as Europe/Berlin")
    group.add_argument(
        "--allow-timezone-mismatch",
        action="store_true",
        help="launch Chrome even if it cannot present the required time zone",
    )
    if humanize:
        group.add_argument(
            "--humanize",
            choices=HUMANIZE_CHOICES,
            default="off",
            help="human-like input preset for page actions (default: off)",
        )


def add_json_argument(parser: argparse.ArgumentParser) -> None:
    """Add ``--json`` for machine-readable output on stdout."""
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")


def browser_config(args: argparse.Namespace, profile_root: Path) -> BrowserConfig:
    """Build a `BrowserConfig` from parsed browser arguments.

    Args:
        args: Namespace from a parser that used `add_browser_arguments`.
        profile_root: Directory for the command's profiles.

    Options a parser did not offer take their `BrowserConfig` defaults.

    Raises:
        ConfigurationError: If any value or combination is invalid (for example
            ``--geoip`` without ``--proxy``).
    """
    return BrowserConfig(
        profile_root=profile_root,
        executable_path=args.executable,
        proxy=args.proxy,
        locale=args.locale,
        headless=args.headless,
        driver=getattr(args, "driver", "native"),
        browser=getattr(args, "browser", "auto"),
        persona=getattr(args, "persona", "auto"),
        geoip=getattr(args, "geoip", False),
        timezone=getattr(args, "timezone", None),
        allow_timezone_mismatch=getattr(args, "allow_timezone_mismatch", False),
        humanize=humanize_setting(getattr(args, "humanize", "off")),
    )


def humanize_setting(name: str) -> bool | HumanConfig:
    """Map a ``--humanize`` value to `BrowserConfig.humanize`.

    Args:
        name: ``off`` or a `HumanConfig.preset` name.

    Returns:
        ``False`` for ``off``, else the preset's `HumanConfig`.

    Raises:
        ConfigurationError: For an unknown name.
    """
    if name == "off":
        return False
    preset = _PRESETS.get(name)
    if preset is not None:
        return HumanConfig.preset(preset)
    raise ConfigurationError(f"humanize must be one of {', '.join(HUMANIZE_CHOICES)}")


def plain(value: Any) -> Any:
    """Convert dataclasses, paths and containers into JSON-compatible data."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def print_json(value: Any) -> None:
    """Print ``value`` as indented JSON on stdout."""
    print(json.dumps(plain(value), indent=2, default=str))


def write_json(path: Path, value: Any) -> None:
    """Write ``value`` as indented UTF-8 JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(plain(value), indent=2, default=str, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def error(message: str) -> None:
    """Print ``botonomus: error: message`` on stderr."""
    print(f"botonomus: error: {message}", file=sys.stderr)


def notice(message: str) -> None:
    """Print an informational line on stderr, keeping stdout for results."""
    print(message, file=sys.stderr)
