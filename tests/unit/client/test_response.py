import pytest

from botonomus import HTTPStatusError
from botonomus.client import Headers, Response


def make(**overrides):
    values = {
        "url": "https://example.com/",
        "status": 200,
        "headers": Headers({"Content-Type": "text/html; charset=iso-8859-1"}),
        "content": "café".encode("latin-1"),
        "via": "browser",
        "elapsed": 0.5,
    }
    values.update(overrides)
    return Response(**values)


def test_text_uses_declared_charset():
    assert make().text == "café"


def test_text_falls_back_to_utf8_with_replacement():
    response = make(headers=Headers({}), content=b"ok \xff")
    assert response.text == "ok �"


def test_headers_are_case_insensitive():
    headers = Headers({"Set-Cookie": "a=1", "X-Thing": "y"})
    assert headers["set-cookie"] == "a=1"
    assert headers.get("x-THING") == "y"
    assert "content-type" not in headers
    assert dict(headers) == {"set-cookie": "a=1", "x-thing": "y"}


def test_json_parses_body():
    assert make(content=b'{"a": [1, 2]}').json() == {"a": [1, 2]}


def test_ok_and_raise_for_status():
    assert make(status=302).ok
    make(status=204).raise_for_status()
    failed = make(status=404)
    assert not failed.ok
    with pytest.raises(HTTPStatusError) as caught:
        failed.raise_for_status()
    assert caught.value.response is failed
    assert str(caught.value) == "HTTP 404"


def test_html_defaults_to_none_and_repr_is_short():
    response = make()
    assert response.html is None
    assert repr(response) == "<Response [200] via browser https://example.com/>"
