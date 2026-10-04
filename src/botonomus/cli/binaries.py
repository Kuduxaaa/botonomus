"""``botonomus install``, ``uninstall`` and ``binaries``: manage Botonomus Chromium builds.

Error messages never contain a release or download URL, which may carry access
tokens.
"""

import argparse
import asyncio
import sys
from typing import Any, TextIO

from ..browser import InstalledBinary, installer
from ..errors import BinaryDownloadError, BinaryNotInstalledError, BinaryVerificationError
from .common import EXIT_FAILURE, EXIT_OK, add_json_argument, error, notice, print_json

_BAR_WIDTH = 30
_MB = 1_000_000


def register(subparsers: Any) -> None:
    """Add the ``install``, ``uninstall`` and ``binaries`` subcommands."""
    installing = subparsers.add_parser(
        "install",
        help="download, verify and install Botonomus Chromium",
        description=(
            "Fetches the signed release manifest, verifies its Ed25519 signature and the "
            "archive's size and SHA-256, then unpacks the build for this platform."
        ),
    )
    installing.add_argument("--version", help="a specific build version (default: newest)")
    installing.add_argument(
        "--manifest-url",
        metavar="URL",
        help="channel manifest URL (default: BOTONOMUS_MANIFEST_URL or the stable channel)",
    )
    add_json_argument(installing)
    installing.set_defaults(func=run_install)

    removing = subparsers.add_parser("uninstall", help="remove an installed build")
    removing.add_argument("version", help="build version, as listed by 'botonomus binaries'")
    removing.set_defaults(func=run_uninstall)

    listing = subparsers.add_parser("binaries", help="list installed Botonomus Chromium builds")
    add_json_argument(listing)
    listing.set_defaults(func=run_binaries)


class ProgressBar:
    """A one-line text progress bar for [`botonomus.browser.install`][botonomus.browser.install].

    Redraws only when the whole percentage changes, and ends the line on completion.

    Args:
        stream: Where to draw; standard error by default, keeping stdout for results.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._shown: int | None = None
        self._open = False

    def __call__(self, received: int, total: int) -> None:
        """Draw ``received`` of ``total`` bytes; the install progress callback."""
        percent = min(100, received * 100 // total) if total > 0 else 100
        if percent == self._shown:
            return
        self._shown = percent
        filled = _BAR_WIDTH * percent // 100
        bar = "#" * filled + "-" * (_BAR_WIDTH - filled)
        self._stream.write(
            f"\rdownloading [{bar}] {percent:>3}%  {received / _MB:.1f}/{total / _MB:.1f} MB"
        )
        self._open = True
        if percent == 100:
            self.finish()
        else:
            self._stream.flush()

    def finish(self) -> None:
        """End the bar's line, if one is drawn."""
        if self._open:
            self._stream.write("\n")
            self._stream.flush()
            self._open = False


def describe(binary: InstalledBinary) -> dict[str, Any]:
    """JSON-compatible description of an installed build.

    Returns:
        ``version``, ``chromium_version``, ``channel``, ``platform``, ``sha256``,
        ``installed_at`` (ISO 8601), ``directory`` and ``executable``.
    """
    return {
        "version": binary.version,
        "chromium_version": binary.chromium_version,
        "channel": binary.channel,
        "platform": binary.platform,
        "sha256": binary.sha256,
        "installed_at": binary.installed_at.isoformat(),
        "directory": str(binary.directory),
        "executable": str(binary.executable),
    }


def run_install(args: argparse.Namespace) -> int:
    """Install a build, drawing download progress on stderr."""
    bar = ProgressBar()
    try:
        binary = _install(args, bar)
    except BinaryDownloadError as exc:
        # Messages never include the URL; the cause (socket or HTTP detail) is omitted.
        error(f"{exc}; check the network connection and the manifest URL, then retry")
        return EXIT_FAILURE
    except BinaryVerificationError as exc:
        error(f"{exc}; nothing was installed")
        return EXIT_FAILURE
    except BinaryNotInstalledError as exc:
        error(str(exc))
        return EXIT_FAILURE
    if args.json:
        print_json(describe(binary))
        return EXIT_OK
    print(f"installed  {binary.version} (Chromium {binary.chromium_version})")
    print(f"executable {binary.executable}")
    return EXIT_OK


def _install(args: argparse.Namespace, bar: ProgressBar) -> InstalledBinary:
    try:
        return asyncio.run(
            installer.install(version=args.version, manifest_url=args.manifest_url, progress=bar)
        )
    finally:
        bar.finish()  # an error message must not continue the bar's line


def run_uninstall(args: argparse.Namespace) -> int:
    """Remove one installed build."""
    if not installer.uninstall(args.version):
        error(f"version {args.version} is not installed")
        return EXIT_FAILURE
    print(f"removed {args.version}")
    return EXIT_OK


def run_binaries(args: argparse.Namespace) -> int:
    """List installed builds, newest first."""
    builds = installer.installed_binaries()
    if args.json:
        print_json([describe(build) for build in builds])
        return EXIT_OK
    if not builds:
        notice("no Botonomus Chromium builds installed; run 'botonomus install'")
        return EXIT_OK
    for build in builds:
        print(f"{build.version:<24} {build.platform:<10} {build.executable}")
    return EXIT_OK
