"""The ``botonomus`` command-line interface.

Subcommands: ``info``, ``install``, ``uninstall``, ``binaries``, ``open``, ``probe``,
``detect``, ``proxy-check``, ``profiles list|remove|warmup`` and ``benchmark``.
Built on `argparse` only.
"""

from .main import build_parser, main

__all__ = ["build_parser", "main"]
