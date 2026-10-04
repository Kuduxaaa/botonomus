"""Listing and removing profiles under a profile root.

Removal always takes the profile's lease first, so a profile that a running
session (in any process) owns is never deleted from under it.
"""

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..errors import ConfigurationError, ProfileInUseError
from .lease import PROFILE_NAME, ProfileLease


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """One profile directory.

    Attributes:
        name: Profile name (the directory name).
        path: Absolute profile directory.
        in_use: Whether another lease currently holds the profile.
        modified: Last modification time of the directory, UTC ISO 8601.
    """

    name: str
    path: Path
    in_use: bool
    modified: str


def list_profiles(root: Path) -> list[ProfileInfo]:
    """Return the profiles under ``root``, sorted by name.

    Directories whose names are not valid profile names (such as ``.locks``) are
    skipped. A missing root yields an empty list.

    Args:
        root: Profile root directory.

    Returns:
        One entry per profile directory.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        return []
    profiles = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.is_symlink() or not PROFILE_NAME.fullmatch(entry.name):
            continue
        modified = datetime.fromtimestamp(entry.stat().st_mtime, UTC).isoformat()
        profiles.append(ProfileInfo(entry.name, entry, _in_use(root, entry.name), modified))
    return profiles


def remove_profile(root: Path, name: str) -> Path:
    """Delete a profile directory after taking its lease.

    Args:
        root: Profile root directory.
        name: Profile name.

    Returns:
        The removed directory.

    Raises:
        ConfigurationError: For an invalid name, or if the profile does not exist.
        ProfileInUseError: If another lease, in any process, holds the profile.
        OSError: If the directory cannot be deleted.
    """
    lease = ProfileLease(root, name)
    if not lease.path.is_dir():
        raise ConfigurationError(f"Profile {lease.name!r} does not exist")
    lease.acquire()
    try:
        shutil.rmtree(lease.path)
    finally:
        lease.release()
    return lease.path


def _in_use(root: Path, name: str) -> bool:
    lease = ProfileLease(root, name)
    try:
        lease.acquire()
    except (ProfileInUseError, ConfigurationError):
        return True
    lease.release()
    return False
