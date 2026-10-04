from botonomus.cli import experiment as command
from botonomus.cli.main import main


def test_experiment_cli_passes_spec_and_proxies(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "arms.toml"
    spec.write_text(
        'sites = ["creepjs"]\nruns = 1\n[arms.a]\nbrowser = "chrome"\n', encoding="utf-8"
    )
    proxies = tmp_path / "p.txt"
    proxies.write_text("http://u:pw@h.example:1\n", encoding="utf-8")
    seen = {}

    async def fake_run(spec, **kw):
        seen.update(kw, runs=spec.runs)

        class Report:
            def to_markdown(self):
                return "| a | table |"

            def to_dict(self):
                return {"ok": True}

        return Report()

    monkeypatch.setattr(command, "run_experiment", fake_run)
    argv = ["experiment", str(spec), "--runs", "4", "--proxies", str(proxies),
            "--output", str(tmp_path / "out"), "--seed", "9", "--settle", "2"]  # fmt: skip
    assert main(argv) == 0
    assert seen["runs"] == 4 and seen["proxies"] == ["http://u:pw@h.example:1"]
    assert seen["seed"] == 9 and seen["settle"] == 2.0
    out = capsys.readouterr().out
    assert "| a | table |" in out and "pw" not in out


def test_experiment_cli_bad_spec_is_usage_error(tmp_path):
    spec = tmp_path / "arms.toml"
    spec.write_text('sites = ["nope"]\n[arms.a]\n', encoding="utf-8")
    assert main(["experiment", str(spec)]) == 2
