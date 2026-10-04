import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from botonomus.diagnostics import ProbeServer


def test_probe_serves_and_receives_snapshot():
    with ProbeServer() as server:
        with urlopen(server.url, timeout=2) as response:
            assert b"collectSnapshot" in response.read()
        snapshot = {
            "schema_version": 1,
            "browser_version": "1",
            "captured_at": "now",
            "observations": {"webdriver": False},
        }
        request = Request(
            server.url + "/snapshot",
            data=json.dumps(snapshot).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 204
        assert server.receive(timeout=1) == snapshot


def test_probe_rejects_malformed_snapshot():
    with ProbeServer() as server:
        request = Request(server.url + "/snapshot", data=b"not-json")
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == 400
