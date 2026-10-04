"""Interleaved A/B experiments over detection sites, with confidence intervals.

An experiment runs every arm (a browser configuration) against every site, ``runs``
times, in shuffled order within each round so arms share network conditions. Each
visit gets a fresh profile and the next proxy from an optional rotation.
"""

import asyncio
import contextlib
import itertools
import json
import random
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from ..config import BrowserConfig, parse_proxy
from ..errors import BotonomusError, ConfigurationError
from .catalogue import SITES
from .detection import (
    LIMITATION,
    VERDICTS,
    DetectionSite,
    Environment,
    RunResult,
    SessionOpener,
    _discard_profile,
    classify_error,
    collect_environment,
    visit_site,
)
from .stats import wilson

ARM_KEYS: Final = frozenset(
    {
        "browser", "executable", "persona", "extra_args", "headless", "driver",
        "locale", "timezone", "geoip", "humanize", "allow_timezone_mismatch",
    }
)  # fmt: skip
"""Keys an arm may set; each maps onto a `BrowserConfig` field."""

_ARM_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


@dataclass(frozen=True, slots=True)
class Arm:
    """One browser configuration under test.

    Attributes:
        name: 1-32 lowercase letters, digits or ``-``.
        options: `BrowserConfig` keyword arguments (``executable`` already mapped to
            ``executable_path``).
    """

    name: str
    options: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """What to run.

    Attributes:
        sites: Sites, in file order.
        arms: Arms, in file order.
        runs: Visits per (arm, site).
    """

    sites: tuple[DetectionSite, ...]
    arms: tuple[Arm, ...]
    runs: int


def load_spec(path: Path, *, runs: int | None = None) -> ExperimentSpec:
    """Read an experiment TOML file. See `parse_spec`.

    Raises:
        ConfigurationError: For invalid TOML or content.
        OSError: If the file cannot be read.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {path.name}: {exc}") from None
    return parse_spec(data, runs=runs)


def parse_spec(data: Mapping[str, Any], *, runs: int | None = None) -> ExperimentSpec:
    """Validate experiment data.

    Args:
        data: ``sites`` (list of catalogue names), ``runs`` (positive int) and
            ``arms`` (table of arm name to options).
        runs: Overrides ``data["runs"]``.

    Returns:
        The validated spec.

    Raises:
        ConfigurationError: Naming the first invalid site, arm or key.
    """
    extra = sorted(set(data) - {"sites", "runs", "arms"})
    if extra:
        raise ConfigurationError(
            f"Unknown key(s): {', '.join(extra)}; expected sites, runs and arms"
        )
    names = data.get("sites")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ConfigurationError("sites must be a non-empty list of site names")
    unknown = [name for name in names if name not in SITES]
    if unknown:
        raise ConfigurationError(
            f"Unknown site(s): {', '.join(unknown)}; known: {', '.join(SITES)}"
        )
    count = data.get("runs", 1) if runs is None else runs
    if type(count) is not int or count < 1:
        raise ConfigurationError("runs must be a positive integer")
    table = data.get("arms")
    if not isinstance(table, dict) or not table:
        raise ConfigurationError("The experiment needs at least one arm")
    arms = tuple(_arm(name, options) for name, options in table.items())
    sites = tuple(SITES[name] for name in dict.fromkeys(names))
    return ExperimentSpec(sites=sites, arms=arms, runs=count)


def _arm(name: str, raw: Any) -> Arm:
    if not isinstance(name, str) or not _ARM_NAME.fullmatch(name):
        raise ConfigurationError(f"Arm name {name!r} must be 1-32 lowercase letters, digits or '-'")
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Arm {name!r} must be a table")
    extra = sorted(set(raw) - ARM_KEYS)
    if extra:
        raise ConfigurationError(f"Arm {name!r} has unknown key(s): {', '.join(extra)}")
    options: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "executable":
            options["executable_path"] = Path(str(value))
        elif key == "extra_args":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigurationError(f"Arm {name!r}: extra_args must be a list of strings")
            options["extra_args"] = tuple(value)
        elif key == "persona":
            if not (value in ("auto", "off") or (type(value) is int and value >= 0)):
                raise ConfigurationError(f"Arm {name!r}: persona must be 'auto', 'off' or a seed")
            options["persona"] = value
        else:
            options[key] = value
    return Arm(name, options)


ManagerFactory = Callable[[BrowserConfig], AbstractAsyncContextManager[SessionOpener]]
"""Builds a manager (entered with ``async with``) for one visit's configuration."""


@dataclass(frozen=True, slots=True)
class Visit:
    """One scheduled visit.

    Attributes:
        arm: Arm name.
        site: Site name.
        run: 1-based round number.
    """

    arm: str
    site: str
    run: int


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    """One visit's outcome.

    Attributes:
        arm: Arm name.
        proxy: ``host:port`` of the proxy used, or ``None``. Never credentials.
        result: The visit's recorded result.
    """

    arm: str
    proxy: str | None
    result: RunResult


@dataclass(frozen=True, slots=True)
class ArmSiteSummary:
    """Outcomes of one arm on one site.

    ``pass_rate`` and ``interval`` (Wilson 95 %) are over decided runs
    (``passed + failed``) only, and ``None`` when no run was decided; blocked,
    unknown and errored runs carry no evidence either way.

    Attributes:
        arm: Arm name.
        site: Site name.
        runs: Visits made.
        passed: Visits the page passed.
        failed: Visits the page flagged.
        unknown: Error-free visits without a decided verdict.
        blocked: Visits the page refused before deciding.
        errors: Visits that raised.
        pass_rate: ``passed / (passed + failed)``.
        interval: Wilson 95 % interval for ``pass_rate``.
    """

    arm: str
    site: str
    runs: int
    passed: int
    failed: int
    unknown: int
    blocked: int
    errors: int
    pass_rate: float | None
    interval: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    """A complete experiment.

    Attributes:
        arms: Arm name to its options, JSON-compatible.
        sites: Site names.
        summaries: One per (arm, site), arm-major in spec order.
        runs: Every visit in execution order.
        environments: Arm name to the environment of its browser.
        limitation: How (not) to read the results.
    """

    arms: dict[str, dict[str, Any]]
    sites: list[str]
    summaries: list[ArmSiteSummary]
    runs: list[ExperimentRun]
    environments: dict[str, Environment]
    limitation: str = LIMITATION

    def to_dict(self) -> dict[str, Any]:
        """The report as JSON-compatible data."""
        return asdict(self)

    def to_json(self) -> str:
        """The report as indented JSON text."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str)

    def to_markdown(self) -> str:
        """One table per site: arm, runs, pass, fail, blocked, unknown, errors, rate, CI."""
        lines: list[str] = []
        for site in self.sites:
            lines += [
                f"## {site}",
                "",
                "| arm | runs | pass | fail | blocked | unknown | errors | pass rate | 95% CI |",
                "|---|---|---|---|---|---|---|---|---|",
            ]
            for s in self.summaries:
                if s.site != site:
                    continue
                rate = "-" if s.pass_rate is None else f"{s.pass_rate:.0%}"
                ci = "-" if s.interval is None else f"{s.interval[0]:.0%}-{s.interval[1]:.0%}"
                lines.append(
                    f"| {s.arm} | {s.runs} | {s.passed} | {s.failed} | {s.blocked} "
                    f"| {s.unknown} | {s.errors} | {rate} | {ci} |"
                )
            lines.append("")
        lines.append(self.limitation)
        return "\n".join(lines)


def schedule(spec: ExperimentSpec, seed: int | None) -> list[Visit]:
    """Order visits round by round, shuffling within each round.

    Args:
        spec: The experiment.
        seed: Shuffle seed; ``None`` for a random order.

    Returns:
        ``runs * len(arms) * len(sites)`` visits; round ``r`` holds every
        (arm, site) pair for run ``r``, so arms interleave in time.
    """
    rng = random.Random(seed)
    order: list[Visit] = []
    for run in range(1, spec.runs + 1):
        round_ = [Visit(arm.name, site.name, run) for arm in spec.arms for site in spec.sites]
        rng.shuffle(round_)
        order += round_
    return order


def _default_factory(config: BrowserConfig) -> AbstractAsyncContextManager[SessionOpener]:
    from ..core import Botonomus

    return Botonomus(1, config=config)


async def run_experiment(
    spec: ExperimentSpec,
    *,
    output: Path,
    proxies: Sequence[str] = (),
    seed: int | None = None,
    interact: bool = True,
    settle: float | None = None,
    run_timeout: float = 180.0,
    factory: ManagerFactory | None = None,
) -> ExperimentReport:
    """Run every scheduled visit one at a time and summarise per (arm, site).

    Args:
        spec: The experiment.
        output: Directory for profiles, screenshots, text, ``report.json`` and
            ``report.md``.
        proxies: Proxy URLs used round-robin, one per visit; empty for none.
        seed: Seed for the schedule and interaction paths.
        interact: Move and scroll after loading.
        settle: Overrides every site's ``settle`` seconds.
        run_timeout: Seconds per visit.
        factory: Builds a manager for one configuration; defaults to a one-slot
            [`Botonomus`][botonomus.Botonomus].

    Returns:
        The report, also written to ``output``.

    Raises:
        ConfigurationError: If an arm's options do not form a valid `BrowserConfig`
            (checked for every arm before the first visit).
    """
    make = factory or _default_factory
    profiles = output / "profiles"
    first_proxy = proxies[0] if proxies else None
    for arm in spec.arms:  # validate everything before launching anything
        try:
            BrowserConfig(profile_root=profiles, proxy=first_proxy, **arm.options)
        except ConfigurationError as exc:
            raise ConfigurationError(f"Arm {arm.name!r}: {exc}") from None
    for sub in ("screenshots", "text"):
        (output / sub).mkdir(parents=True, exist_ok=True)
    arms = {arm.name: arm for arm in spec.arms}
    sites = {site.name: site for site in spec.sites}
    rotation = itertools.cycle(proxies) if proxies else None
    rng = random.Random(seed)
    products: dict[str, list[str]] = {name: [] for name in arms}
    configs: dict[str, BrowserConfig] = {}
    runs: list[ExperimentRun] = []
    completed = False
    try:
        for index, visit in enumerate(schedule(spec, seed), start=1):
            proxy = next(rotation) if rotation is not None else None
            config = BrowserConfig(profile_root=profiles, proxy=proxy, **arms[visit.arm].options)
            configs.setdefault(visit.arm, config)
            profile = f"{visit.arm}-{index:05d}"
            result: RunResult | None = None
            try:
                async with make(config) as bot:
                    result = await visit_site(
                        bot, sites[visit.site], run=visit.run, profile=profile, fresh=True,
                        output=output, interact=interact, settle=settle,
                        run_timeout=run_timeout, text_limit=500, rng=rng,
                        product=products[visit.arm],
                        stem=f"{visit.arm}-{visit.site}-{visit.run:02d}",
                    )  # fmt: skip
            except Exception as exc:  # starting or closing the browser failed: this visit's result
                if result is None:
                    result = RunResult(site=visit.site, run=visit.run, profile=profile)
                    result.error = classify_error(exc)
                    result.error_type = type(exc).__name__
            address = parse_proxy(proxy).address if proxy else None
            runs.append(ExperimentRun(visit.arm, address, result))
            with contextlib.suppress(OSError, BotonomusError):  # files may still be locked
                await asyncio.to_thread(_discard_profile, profiles, profile)
        completed = True
    finally:
        # Written even when a run is interrupted, so finished visits are never lost.
        environments = (
            {
                name: await collect_environment(config, (products[name] or ["unknown"])[0])
                for name, config in configs.items()
            }
            if completed
            else {}
        )
        report = ExperimentReport(
            arms={name: _plain_options(arm.options) for name, arm in arms.items()},
            sites=list(sites),
            summaries=_summarise(spec, runs),
            runs=runs,
            environments=environments,
        )
        (output / "report.json").write_text(report.to_json(), encoding="utf-8")
        (output / "report.md").write_text(report.to_markdown(), encoding="utf-8")
    return report


def _summarise(spec: ExperimentSpec, runs: Sequence[ExperimentRun]) -> list[ArmSiteSummary]:
    summaries = []
    for arm in spec.arms:
        for site in spec.sites:
            mine = [r.result for r in runs if r.arm == arm.name and r.result.site == site.name]
            clean = [r for r in mine if r.error is None]
            count = {v: sum(1 for r in clean if r.verdict == v) for v in VERDICTS}
            decided = count["pass"] + count["fail"]
            summaries.append(
                ArmSiteSummary(
                    arm=arm.name,
                    site=site.name,
                    runs=len(mine),
                    passed=count["pass"],
                    failed=count["fail"],
                    unknown=count["unknown"],
                    blocked=count["blocked"],
                    errors=len(mine) - len(clean),
                    pass_rate=round(count["pass"] / decided, 4) if decided else None,
                    interval=wilson(count["pass"], decided),
                )
            )
    return summaries


def _plain_options(options: Mapping[str, Any]) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, value in options.items():
        if isinstance(value, Path):
            plain[key] = str(value)
        elif isinstance(value, tuple):
            plain[key] = list(value)
        else:
            plain[key] = value
    return plain
