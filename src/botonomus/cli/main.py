"""The ``botonomus`` command: parser construction and error-to-exit-code mapping.

Exit codes: 0 success, 1 runtime failure, 2 usage or configuration error.
"""

import argparse
import sys
from collections.abc import Sequence

from .._version import __version__
from ..errors import BotonomusError, ConfigurationError
from . import (
    benchmark,
    binaries,
    browse,
    consistency,
    detect,
    experiment,
    info,
    probe,
    profiles,
    proxies,
    trace,
)
from .common import EXIT_FAILURE, EXIT_USAGE, error

_COMMANDS = (
    info,
    binaries,
    browse,
    probe,
    detect,
    experiment,
    trace,
    consistency,
    proxies,
    profiles,
    benchmark,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level parser with every subcommand registered.

    Returns:
        The parser. Each subcommand sets ``func``, which takes the parsed namespace
        and returns an exit code.
    """
    parser = argparse.ArgumentParser(
        prog="botonomus",
        description="Stealth browser automation on real Chrome: diagnostics and tools.",
    )
    parser.add_argument("--version", action="version", version=f"botonomus {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    for command in _COMMANDS:
        command.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments without the program name; ``None`` uses ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    for stream in (sys.stdout, sys.stderr):
        # Page text and ISP names are arbitrary Unicode; a cp1252 console must not crash.
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits for --help, --version and usage errors
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if getattr(args, "func", None) is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        code: int = args.func(args)
    except ConfigurationError as exc:
        error(str(exc))
        return EXIT_USAGE
    except BotonomusError as exc:
        error(_describe(exc))
        return EXIT_FAILURE
    except (OSError, ValueError, TimeoutError) as exc:
        error(_describe(exc))
        return EXIT_FAILURE
    except KeyboardInterrupt:
        error("interrupted")
        return EXIT_FAILURE
    return code


def _describe(exc: BaseException) -> str:
    # Botonomus messages never contain credentials; for other errors the class name
    # and message are shown, and the cause chain adds the underlying reason.
    text = str(exc) or type(exc).__name__
    cause = exc.__cause__
    if cause is not None and str(cause):
        text += f" ({type(cause).__name__}: {cause})"
    return text


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
