"""Persistent identities without a profile directory: storage state in a small file.

An identity's cookies, ``localStorage`` and proxy are saved to
``<root>/<name>/state.json`` and restored into a fresh in-memory context next time.
The file holds session cookies (and proxy credentials): it is written readable by the
owner only and never logged.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from ..errors import ConfigurationError
from ..profiles import ProfileLease

_VERSION = 1


@dataclass
class StorageState:
    """What an identity carries between uses.

    Attributes:
        cookies: CDP ``Network.Cookie`` objects.
        local_storage: ``localStorage`` items per origin.
        proxy: The identity's proxy URL, or ``None`` for the client's.
    """

    cookies: list[dict[str, Any]] = field(default_factory=list)
    local_storage: dict[str, dict[str, str]] = field(default_factory=dict)
    proxy: str | None = None


class IdentityStore:
    """Exclusive access to one identity's state file, across processes.

    Use as a context manager; the lock is held until exit.

    Args:
        root: Directory holding one subdirectory per identity.
        name: Identity name, with the same rules as profile names.

    Raises:
        ConfigurationError: For an invalid name.
        ProfileInUseError: On entry, if the identity is in use anywhere.
    """

    def __init__(self, root: Path, name: str) -> None:
        self._lease = ProfileLease(Path(root), name)
        self.name = self._lease.name

    def __enter__(self) -> Self:
        self._lease.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._lease.release()

    @property
    def _path(self) -> Path:
        return self._lease.path / "state.json"

    def load(self) -> StorageState:
        """The saved state, or an empty one for a new identity.

        Raises:
            ConfigurationError: If the file exists but cannot be read.
        """
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return StorageState()
        except (OSError, ValueError) as exc:
            raise ConfigurationError(f"Identity {self.name!r} has an unreadable state") from exc
        if not isinstance(raw, dict) or raw.get("version") != _VERSION:
            raise ConfigurationError(f"Identity {self.name!r} has an unsupported state")
        return StorageState(
            cookies=list(raw.get("cookies", [])),
            local_storage=dict(raw.get("local_storage", {})),
            proxy=raw.get("proxy"),
        )

    def save(self, state: StorageState) -> None:
        """Write ``state`` atomically, readable by the owner only."""
        data = json.dumps({"version": _VERSION, **asdict(state)}, ensure_ascii=False)
        temporary = self._path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(temporary, 0o600)  # O_CREAT's mode does not apply to a leftover file
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._path)
