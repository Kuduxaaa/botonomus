"""Dedicated profile directories with nonblocking, cross-process ownership."""

import re
from pathlib import Path
from typing import Final

from filelock import FileLock, Timeout

from ..errors import ConfigurationError, ProfileInUseError

PROFILE_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
"""Accepted profile names: 1-64 ASCII letters, digits, ``_`` or ``-``."""

_RESERVED: Final = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


class ProfileLease:
    """Exclusive ownership of one profile directory under ``root``.

    The lock lives in ``root/.locks`` and works across processes, so two managers can
    never drive the same profile. Redirected (symlinked or junctioned) directories are
    refused so a lease cannot be pointed at an unrelated location.

    Args:
        root: Profile root directory.
        name: Profile name; normalized to lowercase. Windows device names are refused.

    Attributes:
        name: Normalized profile name.
        root: Resolved profile root.
        path: The profile directory, ``root / name``.

    Raises:
        ConfigurationError: For an invalid or reserved name.
    """

    def __init__(self, root: Path, name: str) -> None:
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise ConfigurationError("Profile name must contain 1–64 ASCII letters, digits, - or _")
        self.name = name.lower()
        if self.name in _RESERVED:
            raise ConfigurationError("Reserved profile name")
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / self.name
        self._lock: FileLock | None = None

    @property
    def held(self) -> bool:
        """Whether this lease currently owns the profile."""
        return self._lock is not None

    def acquire(self) -> Path:
        """Take ownership without waiting and create the profile directory.

        Idempotent while held.

        Returns:
            The profile directory.

        Raises:
            ProfileInUseError: If another lease, in any process, holds the profile.
            ConfigurationError: If directories are redirected or cannot be created.
        """
        if self._lock is not None:
            return self.path
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            lock_root = self.root / ".locks"
            if lock_root.resolve() != lock_root:
                raise ConfigurationError("Lock directory must not be redirected")
            lock_root.mkdir(exist_ok=True)
            if self.path.resolve() != self.path:
                raise ConfigurationError("Profile directory must not be redirected")
            lock_path = lock_root / f"{self.name}.lock"
            if lock_path.is_symlink():
                raise ConfigurationError("Lock file must not be a symbolic link")
            lock = FileLock(lock_path, thread_local=False)
            try:
                lock.acquire(timeout=0)
            except Timeout as exc:
                raise ProfileInUseError(f"Profile {self.name!r} is already in use") from exc
            try:
                self.path.mkdir(exist_ok=True)
                if self.path.resolve() != self.path:
                    raise ConfigurationError("Profile directory must not be redirected")
            except BaseException:
                lock.release()
                raise
            self._lock = lock
            return self.path
        except OSError as exc:
            raise ConfigurationError("Cannot create or lock profile directory") from exc

    def release(self) -> None:
        """Give up ownership. Idempotent."""
        if self._lock is not None:
            self._lock.release()
            self._lock = None
