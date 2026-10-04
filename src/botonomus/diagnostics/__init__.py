"""Local realism diagnostics, detection-site runs and load measurement.

Observations here are evidence about one machine, browser and date, never a
guarantee that any website will treat a session as human.
"""

from .benchmark import measure_level, parse_levels, run_benchmark, should_stop
from .catalogue import CATALOGUE, SITES
from .compare import compare_snapshots
from .detection import (
    DetectionReport,
    DetectionSite,
    Environment,
    RunResult,
    SiteSummary,
    aggregate,
    classify_error,
    normalise_verdict,
    run_detection,
)
from .experiment import ExperimentReport, ExperimentSpec, load_spec, run_experiment
from .probe import ProbeServer
from .snapshots import automated_snapshot, normal_snapshot

__all__ = [
    "CATALOGUE",
    "SITES",
    "DetectionReport",
    "DetectionSite",
    "Environment",
    "ExperimentReport",
    "ExperimentSpec",
    "ProbeServer",
    "RunResult",
    "SiteSummary",
    "aggregate",
    "automated_snapshot",
    "classify_error",
    "compare_snapshots",
    "load_spec",
    "measure_level",
    "normal_snapshot",
    "normalise_verdict",
    "parse_levels",
    "run_benchmark",
    "run_detection",
    "run_experiment",
    "should_stop",
]
