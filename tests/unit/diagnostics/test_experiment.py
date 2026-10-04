from pathlib import Path

import pytest

from botonomus.diagnostics.experiment import Arm, load_spec, parse_spec
from botonomus.diagnostics.stats import wilson
from botonomus.errors import ConfigurationError


@pytest.mark.parametrize(
    ("k", "n", "expected"),
    [(0, 10, (0.0, 0.2775)), (10, 10, (0.7225, 1.0)), (5, 10, (0.2366, 0.7634)), (0, 0, None)],
)
def test_wilson_known_values(k, n, expected):
    assert wilson(k, n) == expected


SPEC = {
    "sites": ["fingerprint", "creepjs"],
    "runs": 3,
    "arms": {
        "chrome": {"browser": "chrome", "persona": "off"},
        "bn": {
            "executable": "C:/x/chrome.exe",
            "browser": "botonomus",
            "persona": 1234,
            "extra_args": ["--disable-features=JXLImageFormat"],
        },
    },
}


def test_parse_spec_maps_arm_options():
    spec = parse_spec(SPEC)
    assert [s.name for s in spec.sites] == ["fingerprint", "creepjs"]
    assert spec.runs == 3
    chrome, bn = spec.arms
    assert chrome == Arm("chrome", {"browser": "chrome", "persona": "off"})
    assert bn.options["executable_path"] == Path("C:/x/chrome.exe")
    assert bn.options["extra_args"] == ("--disable-features=JXLImageFormat",)
    assert bn.options["persona"] == 1234


def test_runs_override():
    assert parse_spec(SPEC, runs=7).runs == 7


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"arms": {"x": {"browsr": "chrome"}}}, "browsr"),
        ({"arms": {}}, "at least one arm"),
        ({"arms": {"Bad Name": {}}}, "Bad Name"),
        ({"runs": 0}, "runs"),
        ({"arms": {"x": {"persona": "maybe"}}}, "persona"),
        ({"arms": {"x": {"extra_args": "--a"}}}, "extra_args"),
    ],
)
def test_unknown_arm_key_rejected(patch, message):
    with pytest.raises(ConfigurationError, match=message):
        parse_spec({**SPEC, **patch})


def test_unknown_site_rejected():
    with pytest.raises(ConfigurationError, match="nosuchsite"):
        parse_spec({**SPEC, "sites": ["nosuchsite"]})


def test_load_spec_reads_toml(tmp_path):
    path = tmp_path / "arms.toml"
    path.write_text(
        'sites = ["creepjs"]\nruns = 2\n[arms.chrome]\nbrowser = "chrome"\n', encoding="utf-8"
    )
    spec = load_spec(path)
    assert spec.arms == (Arm("chrome", {"browser": "chrome"}),)


def test_load_spec_invalid_toml(tmp_path):
    path = tmp_path / "arms.toml"
    path.write_text("sites = [", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="TOML"):
        load_spec(path)


# --- runner -------------------------------------------------------------------

import json  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from botonomus.diagnostics import detection  # noqa: E402
from botonomus.diagnostics.experiment import run_experiment, schedule  # noqa: E402

from .fakes import FakeOpener, FakePage  # noqa: E402


@pytest.fixture(autouse=True)
def no_executable(monkeypatch):
    def missing(config):
        raise detection.BrowserUnavailableError("none")

    monkeypatch.setattr(detection, "resolve_executable", missing)


def test_schedule_interleaves_within_rounds():
    spec = parse_spec(
        {
            "sites": ["creepjs"],
            "runs": 3,
            "arms": {"a": {"browser": "chrome"}, "b": {"browser": "chrome"}},
        }
    )
    order = schedule(spec, seed=1)
    assert len(order) == 6
    for i in range(0, 6, 2):
        assert {v.arm for v in order[i : i + 2]} == {"a", "b"}
        assert {v.run for v in order[i : i + 2]} == {i // 2 + 1}


def make_factory(verdicts, configs):
    """verdicts: arm name -> verdict; records every config the runner builds."""

    def factory(config):
        configs.append(config)
        arm = "a" if config.locale == "en-US" else "b"
        opener = FakeOpener(
            config.profile_root,
            lambda profile: FakePage({"verdict": verdicts[arm], "details": {}}),
        )

        @asynccontextmanager
        async def manager():
            yield opener

        return manager()

    return factory


async def test_experiment_rotates_proxies_and_summarises(tmp_path):
    spec = parse_spec(
        {
            "sites": ["creepjs"],
            "runs": 3,
            "arms": {"a": {"locale": "en-US"}, "b": {"locale": "de-DE"}},
        }
    )
    configs = []
    report = await run_experiment(
        spec,
        output=tmp_path,
        proxies=["http://u:secret@p1.example:8080", "socks5://p2.example:1080"],
        seed=3,
        interact=False,
        settle=0,
        factory=make_factory({"a": "pass", "b": "blocked"}, configs),
    )
    assert [c.proxy for c in configs][:2] == [
        "http://u:secret@p1.example:8080",
        "socks5://p2.example:1080",
    ]
    a, b = sorted(report.summaries, key=lambda s: s.arm)
    assert (a.passed, a.pass_rate, a.interval) == (3, 1.0, (0.4385, 1.0))
    assert (b.blocked, b.pass_rate, b.interval) == (3, None, None)
    text = (tmp_path / "report.json").read_text(encoding="utf-8")
    assert "secret" not in text and "p1.example:8080" in text
    assert json.loads(text)["summaries"]
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| a |" in md and "| b |" in md


async def test_experiment_without_proxies(tmp_path):
    configs = []
    spec = parse_spec({"sites": ["creepjs"], "runs": 1, "arms": {"a": {"locale": "en-US"}}})
    report = await run_experiment(
        spec,
        output=tmp_path,
        interact=False,
        settle=0,
        factory=make_factory({"a": "fail"}, configs),
    )
    assert configs[0].proxy is None
    assert report.runs[0].proxy is None and report.summaries[0].failed == 1


async def test_invalid_arm_config_fails_before_any_visit(tmp_path):
    configs = []
    spec = parse_spec({"sites": ["creepjs"], "runs": 1, "arms": {"a": {"geoip": True}}})
    with pytest.raises(ConfigurationError):
        await run_experiment(spec, output=tmp_path, factory=make_factory({"a": "pass"}, configs))
    assert configs == []


async def test_startup_failure_is_recorded_not_raised(tmp_path):
    from botonomus.errors import BrowserStartupError

    configs = []
    good = make_factory({"a": "pass", "b": "pass"}, configs)

    def factory(config):
        if config.locale == "de-DE":

            @asynccontextmanager
            async def broken():
                raise BrowserStartupError("no browser")
                yield

            return broken()
        return good(config)

    spec = parse_spec(
        {
            "sites": ["creepjs"],
            "runs": 2,
            "arms": {"a": {"locale": "en-US"}, "b": {"locale": "de-DE"}},
        }
    )
    report = await run_experiment(spec, output=tmp_path, interact=False, settle=0, factory=factory)
    a, b = sorted(report.summaries, key=lambda s: s.arm)
    assert (a.passed, b.errors) == (2, 2)
    assert {r.result.error for r in report.runs if r.arm == "b"} == {"startup"}


async def test_profile_cleanup_failure_is_ignored(tmp_path, monkeypatch):
    from botonomus.diagnostics import experiment

    def locked(root, profile):
        raise PermissionError("still in use")

    monkeypatch.setattr(experiment, "_discard_profile", locked)
    spec = parse_spec({"sites": ["creepjs"], "runs": 2, "arms": {"a": {"locale": "en-US"}}})
    report = await run_experiment(
        spec, output=tmp_path, interact=False, settle=0, factory=make_factory({"a": "pass"}, [])
    )
    assert report.summaries[0].passed == 2


async def test_report_is_written_when_interrupted(tmp_path):
    import asyncio

    calls = []
    good = make_factory({"a": "pass"}, [])

    def factory(config):
        calls.append(config)
        if len(calls) == 2:
            raise asyncio.CancelledError
        return good(config)

    spec = parse_spec({"sites": ["creepjs"], "runs": 3, "arms": {"a": {"locale": "en-US"}}})
    with pytest.raises(asyncio.CancelledError):
        await run_experiment(spec, output=tmp_path, interact=False, settle=0, factory=factory)
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert len(data["runs"]) == 1 and data["summaries"][0]["passed"] == 1


def test_unknown_top_level_key_rejected():
    with pytest.raises(ConfigurationError, match="run"):
        parse_spec({**SPEC, "run": 10})


async def test_arm_config_error_names_the_arm(tmp_path):
    spec = parse_spec({"sites": ["creepjs"], "runs": 1, "arms": {"loud": {"headless": "yes"}}})
    with pytest.raises(ConfigurationError, match="'loud'"):
        await run_experiment(spec, output=tmp_path, factory=make_factory({}, []))
