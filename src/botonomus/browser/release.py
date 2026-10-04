"""Signed release manifests for the Botonomus Chromium binary.

A release channel publishes two files side by side:

* ``manifest.json``: UTF-8 JSON describing one release and its per-platform zips.
* ``manifest.json.sig``: the base64 Ed25519 signature of the exact manifest bytes.

The signature covers the raw bytes, so the manifest is verified before it is parsed
and nothing in an unauthenticated document is ever interpreted. Example:

```json
{
  "schema_version": 1,
  "channel": "stable",
  "version": "155.0.8059.26-bn1",
  "chromium_version": "155.0.8059.26",
  "published_at": "2026-10-01T12:00:00Z",
  "artifacts": [
    {"platform": "win-x64", "url": "https://.../chromium-win-x64.zip",
     "sha256": "<64 lowercase hex>", "size": 183500800}
  ]
}
```

Parsing is strict: unknown or missing keys, wrong types, unsupported schema versions
and non-HTTPS URLs are all rejected with
[`BinaryVerificationError`][botonomus.errors.BinaryVerificationError].
"""

import base64
import binascii
import ipaddress
import json
import platform as _platform
import re
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..errors import BinaryVerificationError
from . import signing

__all__ = [
    "SCHEMA_VERSION",
    "Artifact",
    "ReleaseManifest",
    "current_platform",
    "is_valid_version",
    "parse_manifest",
    "sign_manifest",
    "validate_url",
    "verify_manifest",
]

SCHEMA_VERSION = 1
"""The only manifest ``schema_version`` this SDK understands."""

MAX_MANIFEST_BYTES = 1 << 20
"""Upper bound on a manifest download; real manifests are a few kilobytes."""

MAX_SIGNATURE_BYTES = 4096
"""Upper bound on a signature file download."""

_VERSION_RE = re.compile(r"\d{1,5}\.\d{1,5}\.\d{1,6}\.\d{1,6}-bn\d{1,4}")
_CHROMIUM_VERSION_RE = re.compile(r"\d{1,5}\.\d{1,5}\.\d{1,6}\.\d{1,6}")
_CHANNEL_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
_PLATFORM_RE = re.compile(r"[a-z]{2,10}-[a-z0-9]{2,10}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = frozenset(
    {"schema_version", "channel", "version", "chromium_version", "published_at", "artifacts"}
)
_ARTIFACT_KEYS = frozenset({"platform", "url", "sha256", "size"})


@dataclass(frozen=True, slots=True)
class Artifact:
    """One downloadable platform build listed in a manifest.

    Attributes:
        platform: Platform tag such as ``"win-x64"`` (see `current_platform`).
        url: HTTPS location of the zip archive.
        sha256: Lowercase hex SHA-256 of the zip archive.
        size: Exact size of the zip archive in bytes.
    """

    platform: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """A parsed, authenticated release manifest.

    Attributes:
        schema_version: Manifest format version; always `SCHEMA_VERSION`.
        channel: Release channel name, e.g. ``"stable"``.
        version: Botonomus build version, e.g. ``"155.0.8059.26-bn1"``.
        chromium_version: Upstream Chromium version the build is based on.
        published_at: Timezone-aware publication time.
        artifacts: Per-platform downloads; platforms are unique.
    """

    schema_version: int
    channel: str
    version: str
    chromium_version: str
    published_at: datetime
    artifacts: tuple[Artifact, ...]

    def artifact_for(self, platform: str) -> Artifact | None:
        """Return the artifact for ``platform``, or ``None`` if this release lacks one.

        Args:
            platform: A platform tag such as ``"win-x64"``.

        Returns:
            The matching artifact, if any.
        """
        for artifact in self.artifacts:
            if artifact.platform == platform:
                return artifact
        return None


def is_valid_version(version: str) -> bool:
    """Whether ``version`` is a well-formed Botonomus build version.

    Versions double as directory names, so this also guarantees they are path-safe.

    Args:
        version: Candidate version string, e.g. ``"155.0.8059.26-bn1"``.

    Returns:
        ``True`` if the string matches ``MAJOR.MINOR.BUILD.PATCH-bnN``.
    """
    return _VERSION_RE.fullmatch(version) is not None


def current_platform() -> str:
    """Return the platform tag for the running interpreter.

    Returns:
        A tag of the form ``<os>-<arch>``: ``os`` is ``win``, ``mac`` or ``linux`` and
        ``arch`` is ``x64``, ``arm64`` or ``x86``.
    """
    if sys.platform == "win32":
        os_tag = "win"
    elif sys.platform == "darwin":
        os_tag = "mac"
    else:
        os_tag = "linux"
    machine = _platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    elif machine in {"x86", "i386", "i686"}:
        arch = "x86"
    else:
        arch = machine or "unknown"
    return f"{os_tag}-{arch}"


def validate_url(url: str, *, allow_insecure_loopback: bool = False) -> str:
    """Ensure ``url`` is an HTTPS URL with a host.

    Args:
        url: The URL to check.
        allow_insecure_loopback: Also accept ``http://`` when the host is a loopback
            address or ``localhost``. For tests against a local server only.

    Returns:
        ``url`` unchanged.

    Raises:
        BinaryVerificationError: If the URL is not acceptable. The message never
            includes the URL, which may carry access tokens.
    """
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        raise BinaryVerificationError("Release URL has no host")
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and allow_insecure_loopback and _is_loopback(parsed.hostname):
        return url
    raise BinaryVerificationError("Release URLs must use https")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def verify_manifest(
    data: bytes,
    signature: bytes,
    public_key: bytes,
    *,
    allow_insecure_loopback: bool = False,
) -> ReleaseManifest:
    """Authenticate manifest bytes against a base64 signature, then parse them.

    Args:
        data: The exact bytes of ``manifest.json``.
        signature: The contents of ``manifest.json.sig``: base64 of the 64-byte
            Ed25519 signature, surrounding whitespace allowed.
        public_key: The 32-byte release public key.
        allow_insecure_loopback: Passed to `parse_manifest`.

    Returns:
        The parsed manifest.

    Raises:
        BinaryVerificationError: If the signature is malformed or does not verify, or
            the manifest fails strict parsing.
    """
    try:
        raw_signature = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BinaryVerificationError("Release manifest signature is not valid base64") from exc
    if not signing.verify(public_key, data, raw_signature):
        raise BinaryVerificationError("Release manifest signature verification failed")
    return parse_manifest(data, allow_insecure_loopback=allow_insecure_loopback)


def sign_manifest(data: bytes, seed: bytes) -> bytes:
    """Produce the ``manifest.json.sig`` contents for ``data``.

    Release tooling and tests only; see
    [`botonomus.browser.signing.sign`][botonomus.browser.signing.sign] for why
    this must not handle a production key on a shared machine.

    Args:
        data: The exact manifest bytes that will be published.
        seed: The 32-byte Ed25519 secret key.

    Returns:
        ASCII base64 of the signature followed by a newline.
    """
    return base64.b64encode(signing.sign(seed, data)) + b"\n"


def parse_manifest(data: bytes, *, allow_insecure_loopback: bool = False) -> ReleaseManifest:
    """Strictly parse manifest bytes. Call only on bytes whose signature was verified.

    Args:
        data: UTF-8 JSON manifest bytes.
        allow_insecure_loopback: Accept ``http://`` artifact URLs on loopback hosts.

    Returns:
        The parsed manifest.

    Raises:
        BinaryVerificationError: On invalid JSON, an unknown ``schema_version``,
            missing or unknown keys, wrong types or values, duplicate platforms, or
            non-HTTPS URLs.
    """
    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise BinaryVerificationError("Release manifest is not valid JSON") from exc
    if not isinstance(document, dict):
        raise BinaryVerificationError("Release manifest must be a JSON object")
    schema_version = document.get("schema_version")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise BinaryVerificationError("Unsupported release manifest schema_version")
    _check_keys(document, _MANIFEST_KEYS, "manifest")

    channel = _string(document, "channel", _CHANNEL_RE)
    version = _string(document, "version", _VERSION_RE)
    chromium_version = _string(document, "chromium_version", _CHROMIUM_VERSION_RE)
    if not version.startswith(chromium_version + "-"):
        raise BinaryVerificationError("Manifest version does not match chromium_version")
    published_at = _timestamp(document["published_at"])

    raw_artifacts = document["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise BinaryVerificationError("Manifest artifacts must be a non-empty list")
    artifacts: list[Artifact] = []
    for raw in raw_artifacts:
        artifact = _artifact(raw, allow_insecure_loopback)
        if any(existing.platform == artifact.platform for existing in artifacts):
            raise BinaryVerificationError("Manifest lists a platform more than once")
        artifacts.append(artifact)

    return ReleaseManifest(
        schema_version=schema_version,
        channel=channel,
        version=version,
        chromium_version=chromium_version,
        published_at=published_at,
        artifacts=tuple(artifacts),
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _check_keys(obj: dict[str, Any], expected: frozenset[str], what: str) -> None:
    keys = set(obj)
    if keys - expected:
        raise BinaryVerificationError(f"Unknown keys in release {what}")
    if expected - keys:
        raise BinaryVerificationError(f"Missing keys in release {what}")


def _string(obj: dict[str, Any], key: str, pattern: re.Pattern[str]) -> str:
    value = obj[key]
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise BinaryVerificationError(f"Invalid {key} in release manifest")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise BinaryVerificationError("Invalid published_at in release manifest")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BinaryVerificationError("Invalid published_at in release manifest") from exc
    if parsed.tzinfo is None:
        raise BinaryVerificationError("published_at must include a timezone")
    return parsed


def _artifact(raw: object, allow_insecure_loopback: bool) -> Artifact:
    if not isinstance(raw, dict):
        raise BinaryVerificationError("Manifest artifact must be a JSON object")
    _check_keys(raw, _ARTIFACT_KEYS, "artifact")
    platform = _string(raw, "platform", _PLATFORM_RE)
    url = raw["url"]
    if not isinstance(url, str):
        raise BinaryVerificationError("Invalid url in release manifest")
    validate_url(url, allow_insecure_loopback=allow_insecure_loopback)
    sha256 = _string(raw, "sha256", _SHA256_RE)
    size = raw["size"]
    if type(size) is not int or size <= 0:
        raise BinaryVerificationError("Invalid size in release manifest")
    return Artifact(platform=platform, url=url, sha256=sha256, size=size)
