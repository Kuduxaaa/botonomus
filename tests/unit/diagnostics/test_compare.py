import pytest

from botonomus.diagnostics import compare_snapshots


def snapshot(**observations):
    return {
        "schema_version": 1,
        "browser_version": "123.0",
        "captured_at": "2026-10-03T12:00:00Z",
        "observations": observations,
    }


def test_missing_baseline_cannot_pass():
    assert compare_snapshots(None, snapshot())["status"] == "missing_baseline"


def test_version_mismatch_is_incomparable():
    automated = snapshot()
    automated["browser_version"] = "124.0"
    assert compare_snapshots(snapshot(), automated)["status"] == "incomparable"


def test_changed_automation_property_is_reported():
    report = compare_snapshots(snapshot(webdriver=False), snapshot(webdriver=True))
    assert report["status"] == "differences"
    assert report["differences"] == {"webdriver": {"manual": False, "automated": True}}


def test_matching_observations_ignore_capture_time():
    automated = snapshot(webdriver=False)
    automated["captured_at"] = "2026-10-03T12:10:00Z"
    assert (
        compare_snapshots(snapshot(webdriver=False), automated)["status"] == "matching_observations"
    )


@pytest.mark.parametrize("bad", [{}, {"schema_version": 99}, {**snapshot(), "observations": []}])
def test_malformed_input_rejected(bad):
    with pytest.raises(ValueError):
        compare_snapshots(snapshot(), bad)


def test_missing_field_differs_from_null():
    assert compare_snapshots(snapshot(), snapshot(webdriver=None))["status"] == "differences"
