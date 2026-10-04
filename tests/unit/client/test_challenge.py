import pytest

from botonomus.client import Headers
from botonomus.client.challenge import is_challenge


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (403, {"cf-mitigated": "challenge"}, ""),
        (503, {}, "<title>Just a moment...</title>"),
        (403, {}, "<script>window._cf_chl_opt={}</script>"),
        (403, {"server": "cloudflare"}, "Attention Required! | Cloudflare"),
        (403, {}, '<script src="https://ct.captcha-delivery.com/c.js"></script>'),
        (403, {"x-datadome": "protected"}, ""),
        (403, {}, '<div id="px-captcha"></div>'),
        (200, {}, "<script>window._cf_chl_opt={cvId:'3'}</script>"),
        (429, {}, "<html>slow down</html>"),
    ],
)
def test_challenges_are_recognized(status, headers, body):
    assert is_challenge(status, Headers(headers), body)


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (200, {"server": "cloudflare"}, "<html><title>Shop</title></html>"),
        (404, {}, "Not found"),
        (403, {}, "Forbidden"),
        (200, {}, "Learn how Cloudflare Turnstile and DataDome work"),
    ],
)
def test_ordinary_pages_are_not_challenges(status, headers, body):
    assert not is_challenge(status, Headers(headers), body)
