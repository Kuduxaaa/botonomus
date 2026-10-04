import base64
import json
import re

import pytest

from botonomus.browser import release, signing
from botonomus.errors import BinaryVerificationError

SEED = bytes(range(32))
PUBLIC = signing.public_key_from_seed(SEED)


def manifest_dict(**overrides):
    document = {
        "schema_version": 1,
        "channel": "stable",
        "version": "155.0.8059.26-bn1",
        "chromium_version": "155.0.8059.26",
        "published_at": "2026-10-01T12:00:00Z",
        "artifacts": [
            {
                "platform": "win-x64",
                "url": "https://releases.example/chromium-win-x64.zip",
                "sha256": "ab" * 32,
                "size": 1234,
            }
        ],
    }
    document.update(overrides)
    return document


def encode(document):
    return json.dumps(document).encode()


def artifact(**overrides):
    item = manifest_dict()["artifacts"][0]
    item.update(overrides)
    return item


def test_verified_manifest_round_trip():
    data = encode(manifest_dict())
    manifest = release.verify_manifest(data, release.sign_manifest(data, SEED), PUBLIC)
    assert manifest.version == "155.0.8059.26-bn1"
    assert manifest.chromium_version == "155.0.8059.26"
    assert manifest.channel == "stable"
    assert manifest.published_at.tzinfo is not None
    found = manifest.artifact_for("win-x64")
    assert found is not None and found.size == 1234 and found.sha256 == "ab" * 32
    assert manifest.artifact_for("linux-x64") is None


def test_tampered_manifest_is_rejected():
    data = encode(manifest_dict())
    signature = release.sign_manifest(data, SEED)
    tampered = data.replace(b"1234", b"1235")
    with pytest.raises(BinaryVerificationError, match="signature"):
        release.verify_manifest(tampered, signature, PUBLIC)


def test_tampered_signature_is_rejected():
    data = encode(manifest_dict())
    raw = bytearray(signing.sign(SEED, data))
    raw[10] ^= 0x40
    with pytest.raises(BinaryVerificationError, match="signature"):
        release.verify_manifest(data, base64.b64encode(bytes(raw)), PUBLIC)


@pytest.mark.parametrize("signature", [b"", b"not base64!!", b"AAAA", b"\xff\xfe"])
def test_malformed_signature_is_rejected(signature):
    with pytest.raises(BinaryVerificationError):
        release.verify_manifest(encode(manifest_dict()), signature, PUBLIC)


def test_wrong_key_is_rejected():
    data = encode(manifest_dict())
    other_public = signing.public_key_from_seed(bytes(32))
    with pytest.raises(BinaryVerificationError):
        release.verify_manifest(data, release.sign_manifest(data, SEED), other_public)


@pytest.mark.parametrize("schema", [0, 2, "1", True, 1.0, None])
def test_unknown_schema_version_is_rejected(schema):
    with pytest.raises(BinaryVerificationError, match="schema_version"):
        release.parse_manifest(encode(manifest_dict(schema_version=schema)))


@pytest.mark.parametrize(
    "document",
    [
        manifest_dict(extra=1),
        {k: v for k, v in manifest_dict().items() if k != "channel"},
        manifest_dict(version="155.0.8059.26"),
        manifest_dict(version="../../evil-bn1"),
        manifest_dict(version="155.0.8059.27-bn1"),
        manifest_dict(chromium_version=155),
        manifest_dict(channel="Stable Channel"),
        manifest_dict(published_at="2026-10-01T12:00:00"),
        manifest_dict(published_at="yesterday"),
        manifest_dict(published_at=0),
        manifest_dict(artifacts=[]),
        manifest_dict(artifacts={}),
        manifest_dict(artifacts=["x"]),
        manifest_dict(artifacts=[artifact(), artifact()]),
        manifest_dict(artifacts=[artifact(extra=True)]),
        manifest_dict(artifacts=[{"platform": "win-x64"}]),
        manifest_dict(artifacts=[artifact(url="http://releases.example/a.zip")]),
        manifest_dict(artifacts=[artifact(url="ftp://releases.example/a.zip")]),
        manifest_dict(artifacts=[artifact(url="https:///a.zip")]),
        manifest_dict(artifacts=[artifact(url=7)]),
        manifest_dict(artifacts=[artifact(sha256="AB" * 32)]),
        manifest_dict(artifacts=[artifact(sha256="ab" * 31)]),
        manifest_dict(artifacts=[artifact(size=0)]),
        manifest_dict(artifacts=[artifact(size=True)]),
        manifest_dict(artifacts=[artifact(size="1234")]),
        manifest_dict(artifacts=[artifact(platform="Windows")]),
        [],
    ],
)
def test_strict_parsing_rejects_invalid_documents(document):
    with pytest.raises(BinaryVerificationError):
        release.parse_manifest(encode(document))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff\xfe",
        b"{not json",
        b'{"schema_version": 1, "schema_version": 1}',
    ],
)
def test_invalid_json_is_rejected(data):
    with pytest.raises(BinaryVerificationError):
        release.parse_manifest(data)


def test_http_only_allowed_for_loopback_when_requested():
    loopback = encode(manifest_dict(artifacts=[artifact(url="http://127.0.0.1:8000/a.zip")]))
    with pytest.raises(BinaryVerificationError, match="https"):
        release.parse_manifest(loopback)
    parsed = release.parse_manifest(loopback, allow_insecure_loopback=True)
    assert parsed.artifacts[0].url.startswith("http://127.0.0.1")
    remote = encode(manifest_dict(artifacts=[artifact(url="http://example.com/a.zip")]))
    with pytest.raises(BinaryVerificationError):
        release.parse_manifest(remote, allow_insecure_loopback=True)


@pytest.mark.parametrize(
    ("url", "loopback_ok"),
    [
        ("https://x.example/m.json", True),
        ("http://localhost:1/m.json", True),
        ("http://[::1]:1/m.json", True),
        ("http://10.0.0.1/m.json", False),
        ("http://127.example/m.json", False),
    ],
)
def test_validate_url(url, loopback_ok):
    if loopback_ok:
        assert release.validate_url(url, allow_insecure_loopback=True) == url
    else:
        with pytest.raises(BinaryVerificationError):
            release.validate_url(url, allow_insecure_loopback=True)


def test_url_errors_do_not_echo_the_url():
    with pytest.raises(BinaryVerificationError) as info:
        release.validate_url("http://example.com/m.json?token=SECRET")
    assert "SECRET" not in str(info.value)
    assert "example.com" not in str(info.value)


def test_current_platform_tag():
    assert re.fullmatch(r"(win|mac|linux)-[a-z0-9_]+", release.current_platform())


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ("155.0.8059.26-bn1", True),
        ("1.2.3.4-bn12", True),
        ("155.0.8059.26", False),
        ("155.0.8059.26-bn", False),
        ("../155.0.8059.26-bn1", False),
        ("155.0.8059.26-bn1/..", False),
    ],
)
def test_is_valid_version(version, ok):
    assert release.is_valid_version(version) is ok
