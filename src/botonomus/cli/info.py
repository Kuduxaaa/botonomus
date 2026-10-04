"""``botonomus info``: versions, platform and the browsers Botonomus can find."""

import argparse
import importlib
import importlib.util
import platform
from pathlib import Path
from typing import Any

from .._version import __version__
from ..browser import (
    executable_version,
    find_botonomus_chromium,
    find_chrome,
    installer,
    is_botonomus_build,
    is_testing_build,
)
from ..config import BrowserConfig
from ..config.browser import BROWSER_CHOICES, BrowserChoice
from ..core import resolve_executable
from ..errors import BotonomusError, BrowserUnavailableError
from .common import EXIT_OK, add_json_argument, print_json


def register(subparsers: Any) -> None:
    """Add the ``info`` subcommand."""
    parser = subparsers.add_parser("info", help="show versions and discovered browsers")
    parser.add_argument("--executable", type=Path, help="report this executable instead")
    parser.add_argument(
        "--browser",
        choices=BROWSER_CHOICES,
        default="auto",
        help="browser choice used for the 'default' line (default: auto)",
    )
    add_json_argument(parser)
    parser.set_defaults(func=run)


def collect(executable: Path | None = None, browser: BrowserChoice = "auto") -> dict[str, Any]:
    """Gather environment information without starting a browser.

    Args:
        executable: Report this executable instead of discovering Chrome.
        browser: The ``BrowserConfig.browser`` choice used to resolve ``default``.

    Returns:
        ``botonomus``, ``python``, ``platform``, ``chrome`` (``None`` if not found),
        ``botonomus_chromium`` (the newest installed build for this platform, or
        ``None``), ``default`` (what a launch with ``browser`` and ``executable``
        would use, or ``None`` if nothing suitable is found), ``drivers`` (which
        optional driver packages are importable) and ``installed_binaries``
        (``None`` when the install directory cannot be read).
    """
    try:
        path: Path | None = find_chrome(executable)
    except BrowserUnavailableError:
        path = None
    chrome = None
    if path is not None:
        chrome = {
            "path": str(path),
            "version": executable_version(path),
            "testing_build": is_testing_build(path),
        }
    return {
        "botonomus": __version__,
        "python": f"{platform.python_version()} ({platform.python_implementation()})",
        "platform": platform.platform(),
        "chrome": chrome,
        "botonomus_chromium": botonomus_chromium(),
        "default": default_browser(executable, browser),
        "drivers": {
            "native": True,
            "patchright": importlib.util.find_spec("patchright") is not None,
            "playwright": importlib.util.find_spec("playwright") is not None,
        },
        "installed_binaries": installed_binaries(),
    }


def botonomus_chromium() -> dict[str, Any] | None:
    """Describe the Botonomus Chromium build ``browser="auto"`` would prefer.

    Returns:
        ``{path, version}`` for the newest installed build for this platform, or
        ``None`` if none is installed or the install directory cannot be read.
    """
    try:
        path = find_botonomus_chromium()
    except (OSError, BotonomusError):  # info must never fail on the install directory
        return None
    if path is None:
        return None
    return {"path": str(path), "version": executable_version(path)}


def default_browser(executable: Path | None, browser: BrowserChoice) -> dict[str, Any] | None:
    """Describe the executable a session launch would use.

    Args:
        executable: An explicit executable, as ``BrowserConfig.executable_path``.
        browser: The ``BrowserConfig.browser`` choice.

    Returns:
        ``{browser, path, version, botonomus_build}``, or ``None`` if the launch would
        fail because no suitable executable exists.
    """
    config = BrowserConfig(executable_path=executable, browser=browser)
    try:
        path = resolve_executable(config)
    except (OSError, BotonomusError):  # BrowserUnavailableError, BinaryNotInstalledError
        return None
    return {
        "browser": browser,
        "path": str(path),
        "version": executable_version(path),
        "botonomus_build": is_botonomus_build(path),
    }


def installed_binaries() -> list[dict[str, str]] | None:
    """List installed Botonomus Chromium builds for this platform.

    Returns:
        One ``{version, chromium_version, executable}`` entry per build, newest
        first, or ``None`` if the install directory cannot be read.
    """
    try:
        builds = installer.installed_binaries()
    except Exception:  # info must never fail on an unreadable install directory
        return None
    return [
        {
            "version": build.version,
            "chromium_version": build.chromium_version,
            "executable": str(build.executable),
        }
        for build in builds
    ]


def run(args: argparse.Namespace) -> int:
    """Print environment information."""
    data = collect(args.executable, args.browser)
    if args.json:
        print_json(data)
        return EXIT_OK
    print(f"botonomus  {data['botonomus']}")
    print(f"python     {data['python']}")
    print(f"platform   {data['platform']}")
    chrome = data["chrome"]
    if chrome is None:
        print("chrome     not found (pass --executable or install Google Chrome)")
    else:
        version = chrome["version"] or "unknown version"
        suffix = "  [testing build]" if chrome["testing_build"] else ""
        print(f"chrome     {chrome['path']} ({version}){suffix}")
    build = data["botonomus_chromium"]
    if build is None:
        print("build      no Botonomus Chromium build installed ('botonomus install')")
    else:
        print(f"build      {build['path']} ({build['version'] or 'unknown version'})")
    default = data["default"]
    if default is None:
        print(f"default    none: browser={args.browser} finds no executable")
    else:
        kind = "Botonomus Chromium" if default["botonomus_build"] else "Chrome"
        print(f"default    {default['path']} [{kind}, browser={default['browser']}]")
    drivers = ", ".join(name for name, ok in data["drivers"].items() if ok)
    print(f"drivers    {drivers}")
    binaries = data["installed_binaries"]
    if binaries is None:
        print("binaries   unreadable install directory")
    elif not binaries:
        print("binaries   none installed")
    else:
        for item in binaries:
            print(f"binaries   {item['version']}  {item['executable']}")
    return EXIT_OK
