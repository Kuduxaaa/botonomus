"""Local observations are evidence, never a guarantee of undetectability."""

from typing import Any


def _validate(snapshot: dict[str, Any]) -> None:
    if (
        snapshot.get("schema_version") != 1
        or not isinstance(snapshot.get("browser_version"), str)
        or not snapshot["browser_version"]
        or not isinstance(snapshot.get("captured_at"), str)
        or not isinstance(snapshot.get("observations"), dict)
    ):
        raise ValueError(
            "Expected a version-1 browser snapshot with version, time and observations"
        )


def compare_snapshots(manual: dict[str, Any] | None, automated: dict[str, Any]) -> dict[str, Any]:
    """Compare identical probes from the same browser version and environment."""
    _validate(automated)
    report: dict[str, Any] = {
        "status": "missing_baseline",
        "differences": {},
        "limitation": "Local observations do not establish third-party detection outcomes.",
    }
    if manual is None:
        return report
    _validate(manual)
    if manual["browser_version"] != automated["browser_version"]:
        report["status"] = "incomparable"
        return report
    left, right = manual["observations"], automated["observations"]
    differences = {
        key: {
            "manual": left.get(key, {"missing": True}),
            "automated": right.get(key, {"missing": True}),
        }
        for key in sorted(left.keys() | right.keys())
        if key not in left or key not in right or left[key] != right[key]
    }
    report.update(
        status="differences" if differences else "matching_observations", differences=differences
    )
    return report
