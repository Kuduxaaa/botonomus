import pytest

from botonomus import BrowserConfig
from botonomus.config import parse_proxy
from botonomus.errors import ConfigurationError


@pytest.mark.parametrize("locale", ["en-US", "de", "zh-Hans-CN"])
def test_valid_locale(locale):
    assert BrowserConfig(locale=locale).locale == locale


@pytest.mark.parametrize("locale", ["", "en_US", "e", "en-US; rm", 5])
def test_invalid_locale(locale):
    with pytest.raises(ConfigurationError):
        BrowserConfig(locale=locale)


def test_proxy_credentials_are_parsed_and_hidden():
    config = BrowserConfig(proxy="http://user%40x:p%3Ass@proxy.example:8080")
    spec = config.proxy_spec
    assert spec is not None
    assert (spec.username, spec.password) == ("user@x", "p:ss")
    assert spec.server == "http://proxy.example:8080"
    assert "p:ss" not in repr(config) and "p%3Ass" not in repr(config)
    assert "p:ss" not in repr(spec)


def test_ipv6_proxy_server_is_bracketed():
    assert parse_proxy("socks5://[::1]:1080").server == "socks5://[::1]:1080"


@pytest.mark.parametrize(
    "proxy",
    ["proxy:8080", "ftp://h:21", "http://h", "http://h:99999", "http://h:1/path", "http://:x@h:1"],
)
def test_invalid_proxy(proxy):
    with pytest.raises(ConfigurationError):
        BrowserConfig(proxy=proxy)


def test_extra_args_accept_flags():
    config = BrowserConfig(extra_args=["--fingerprint=42", "--window-size=1280,800"])
    assert config.extra_args == ("--fingerprint=42", "--window-size=1280,800")


@pytest.mark.parametrize(
    "args",
    [
        "--flag",
        ("plain",),
        ("--user-data-dir=x",),
        ("--Remote-Debugging-Port=1",),
        ("--enable-automation",),
        ("--proxy-server=x",),
        ("--headless",),
        ("--lang=de",),
    ],
)
def test_extra_args_reject_owned_or_malformed(args):
    with pytest.raises(ConfigurationError):
        BrowserConfig(extra_args=args)


def test_render_when_occluded_must_be_boolean():
    assert BrowserConfig().render_when_occluded is True
    with pytest.raises(ConfigurationError):
        BrowserConfig(render_when_occluded="yes")
