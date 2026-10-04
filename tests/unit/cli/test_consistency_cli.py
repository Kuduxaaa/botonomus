from botonomus.cli import consistency as command
from botonomus.cli.main import main
from botonomus.diagnostics.consistency import Check, ConsistencyReport


def fake(checks):
    async def run_consistency(config):
        return ConsistencyReport("Chrome/155", checks)

    return run_consistency


def test_consistency_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(command, "run_consistency", fake([Check("screen", True, {})]))
    assert main(["consistency", "--browser", "chrome"]) == 0
    assert "PASS  screen" in capsys.readouterr().out
    monkeypatch.setattr(command, "run_consistency", fake([Check("voices", False, {"count": 0})]))
    assert main(["consistency", "--browser", "chrome"]) == 1
    assert "FAIL  voices" in capsys.readouterr().out
