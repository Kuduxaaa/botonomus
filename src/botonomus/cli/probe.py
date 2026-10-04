"""``botonomus probe``: capture the local probe snapshot from an automated session.

Optionally compares it with a baseline: a normally launched browser (``--normal``)
or a snapshot exported by hand (``--baseline FILE``, collected with ``--serve``).
Everything stays on loopback.
"""

import argparse
import asyncio
import json
import queue
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..core import resolve_executable
from ..diagnostics import ProbeServer, automated_snapshot, compare_snapshots, normal_snapshot
from .browse import wait_for_enter
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
    write_json,
)


def register(subparsers: Any) -> None:
    """Add the ``probe`` subcommand."""
    parser = subparsers.add_parser("probe", help="capture the local probe snapshot")
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument(
        "--normal", action="store_true", help="also capture a normally launched browser"
    )
    baseline.add_argument("--baseline", type=Path, help="compare with a saved snapshot JSON")
    baseline.add_argument(
        "--serve",
        action="store_true",
        help="only serve the probe for a manual baseline; open it yourself and download JSON",
    )
    parser.add_argument("--output", type=Path, help="directory for snapshot JSON files")
    parser.add_argument("--timeout", type=positive_float, default=30.0, help="seconds per snapshot")
    add_browser_arguments(parser, humanize=False)
    add_json_argument(parser)
    parser.set_defaults(func=run)


async def probe(args: argparse.Namespace) -> dict[str, Any]:
    """Capture snapshots and compare them as requested.

    Returns:
        ``{"automated": ..., "baseline": ... | None, "baseline_mode": str | None,
        "comparison": ... | None}``.
    """
    baseline: dict[str, Any] | None = None
    mode = None
    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        mode = "user-supplied snapshot"
    with (
        tempfile.TemporaryDirectory(
            prefix="botonomus-probe-", ignore_cleanup_errors=True
        ) as directory,
        ProbeServer() as s,
    ):
        root = Path(directory)
        config = browser_config(args, root / "profiles")
        if args.normal:
            # The baseline uses the same executable the automated session launches.
            executable = await asyncio.to_thread(resolve_executable, config)
            baseline = await normal_snapshot(executable, root / "normal", s, timeout=args.timeout)
            mode = "normal launch; no automation or debugging connection"
        automated = await automated_snapshot(config, s, timeout=args.timeout)
    comparison = compare_snapshots(baseline, automated) if baseline is not None else None
    return {
        "automated": automated,
        "baseline": baseline,
        "baseline_mode": mode,
        "comparison": comparison,
    }


async def serve(args: argparse.Namespace) -> None:
    """Serve the probe until Enter, printing a launch command for a manual baseline."""
    profile = await asyncio.to_thread(Path(".botonomus/manual-diagnostic").resolve)
    executable = await asyncio.to_thread(resolve_executable, browser_config(args, profile))
    with ProbeServer() as server:
        command = [str(executable), f"--user-data-dir={profile}", "--no-first-run", server.url]
        notice("Run this in another terminal, then download the snapshot from the page:")
        print(subprocess.list2cmdline(command))
        notice("Press Enter here to stop the server.")
        await wait_for_enter()


def run(args: argparse.Namespace) -> int:
    """Run the probe and report it."""
    if args.serve:
        try:
            asyncio.run(serve(args))
        except KeyboardInterrupt:
            pass
        return EXIT_OK
    try:
        result = asyncio.run(probe(args))
    except queue.Empty:
        error("the probe page did not report a snapshot in time")
        return EXIT_FAILURE
    if args.output is not None:
        write_json(args.output / "automated.json", result["automated"])
        if result["baseline"] is not None:
            write_json(args.output / "baseline.json", result["baseline"])
            write_json(args.output / "comparison.json", result["comparison"])
    if args.json:
        print_json(result)
        return EXIT_OK
    automated = result["automated"]
    observations = automated["observations"]
    print(f"browser      {automated['browser_version']}")
    print(f"captured_at  {automated['captured_at']}")
    print(f"observations {len(observations)}")
    for key in sorted(observations):
        print(f"  {key}: {json.dumps(observations[key])[:100]}")
    comparison = result["comparison"]
    if comparison is not None:
        print(f"baseline     {result['baseline_mode']}")
        print(f"comparison   {comparison['status']}")
        for key in comparison["differences"]:
            print(f"  differs: {key}")
    return EXIT_OK
