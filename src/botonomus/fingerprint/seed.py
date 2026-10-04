"""Per-profile persona seeds keyed by a per-installation secret."""

import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Final

from ..errors import ConfigurationError
from ..profiles.lease import PROFILE_NAME

KEY_DIRECTORY: Final = ".botonomus"
"""Directory under the profile root that holds the persona key."""

KEY_FILE: Final = "persona.key"
"""Name of the persona key file inside `KEY_DIRECTORY`."""

_KEY_SIZE: Final = 32
_PERSON: Final = b"bn-profile-seed"
# A creator on a filesystem without hard links writes the key in place; give it
# this long to finish before a short file is treated as corrupt.
_PARTIAL_WAIT: Final = 1.0


def persona_seed(profile_root: Path, profile: str) -> int:
    """Return the stable 64-bit persona seed of a profile.

    The seed is a keyed BLAKE2b of the normalized (lowercase) profile name, keyed
    with 32 random bytes stored in ``profile_root/.botonomus/persona.key``. The
    same profile therefore keeps one identity across restarts, while the same
    profile name on another installation gets an unrelated identity.

    The key is created on first use and is never logged. Deleting it changes the
    identity of every profile under ``profile_root``. Blocking file I/O; it is a
    single small read after the first call.

    Args:
        profile_root: Profile root directory (the one passed to the manager).
        profile: Profile name, validated like [`ProfileLease`][botonomus.profiles.ProfileLease].

    Returns:
        The seed, in ``[0, 2**64)``.

    Raises:
        ConfigurationError: If the profile name is invalid, or the key file exists
            but is not exactly 32 bytes.
        OSError: If the key cannot be read or created.
    """
    if not isinstance(profile, str) or not PROFILE_NAME.fullmatch(profile):
        raise ConfigurationError("Profile name must contain 1–64 ASCII letters, digits, - or _")
    key = _load_or_create_key(Path(profile_root).expanduser().resolve() / KEY_DIRECTORY / KEY_FILE)
    digest = hashlib.blake2b(
        profile.lower().encode("ascii"), digest_size=8, key=key, person=_PERSON
    ).digest()
    return int.from_bytes(digest, "big")


def _load_or_create_key(path: Path) -> bytes:
    key = _read_key(path)
    if key is not None:
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    _publish_key(path, secrets.token_bytes(_KEY_SIZE))
    key = _read_key(path)
    if key is None:
        raise ConfigurationError("Persona key disappeared while it was being created")
    return key


def _read_key(path: Path) -> bytes | None:
    deadline = time.monotonic() + _PARTIAL_WAIT
    while True:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(data) == _KEY_SIZE:
            return data
        if len(data) > _KEY_SIZE or time.monotonic() >= deadline:
            raise ConfigurationError(
                f"Persona key {path} is corrupt; deleting it changes every profile's identity"
            )
        time.sleep(0.01)


def _publish_key(path: Path, key: bytes) -> None:
    """Make ``key`` the content of ``path`` unless another creator got there first."""
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    _write_exclusive(temporary, key)
    try:
        # A hard link publishes a complete file atomically and fails if one exists,
        # so concurrent creators agree on a single key and readers never see a
        # partial write.
        os.link(temporary, path)
    except FileExistsError:
        pass
    except OSError:
        try:
            _write_exclusive(path, key)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
