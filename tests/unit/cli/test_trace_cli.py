from botonomus.cli.main import main
from botonomus.diagnostics.apitrace import Trace, TraceRecord


def test_trace_diff_cli(tmp_path, capsys):
    a = Trace("u", "Chrome/154", [TraceRecord("Navigator.userAgent", "page", "o", "[]", "A")])
    b = Trace("u", "Chrome/155", [TraceRecord("Navigator.userAgent", "page", "o", "[]", "B")])
    (tmp_path / "chrome.json").write_text(a.to_json(), encoding="utf-8")
    (tmp_path / "bn.json").write_text(b.to_json(), encoding="utf-8")
    assert main(["trace-diff", str(tmp_path / "chrome.json"), str(tmp_path / "bn.json")]) == 0
    out = capsys.readouterr().out
    assert "Navigator.userAgent" in out and '"A"' in out and '"B"' in out


def test_trace_requires_output(capsys):
    assert main(["trace", "https://example.com"]) == 2


def test_trace_diff_malformed_file_is_usage_error(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"nope": 1}', encoding="utf-8")
    assert main(["trace-diff", str(bad), str(bad)]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_trace_offers_no_driver_option(capsys):
    assert (
        main(["trace", "https://example.com", "--output", "x.json", "--driver", "playwright"]) == 2
    )


def test_trace_warns_on_empty_trace(tmp_path, monkeypatch, capsys):
    from botonomus.cli import trace as command

    async def fake(args):
        return Trace("u", "Chrome/1", [])

    monkeypatch.setattr(command, "_trace", fake)
    assert main(["trace", "https://example.com", "--output", str(tmp_path / "t.json")]) == 0
    assert "no fingerprinting reads" in capsys.readouterr().err.lower()


def test_trace_diff_binary_or_bad_record_is_usage_error(tmp_path):
    binary = tmp_path / "bin.json"
    binary.write_bytes(b"\xff\xfe\x00\x81")
    bad = tmp_path / "bad.json"
    bad.write_text('{"url": "u", "product": "p", "records": [{"api": 1}]}', encoding="utf-8")
    assert main(["trace-diff", str(binary), str(binary)]) == 2
    assert main(["trace-diff", str(bad), str(bad)]) == 2
