"""Browser executables, processes, installed builds and the Chrome backend."""

from .arguments import launch_arguments
from .backend import Backend, BrowserHandle
from .chrome import ChromeBackend, ChromeHandle
from .discovery import (
    BUILD_MARKER,
    find_botonomus_chromium,
    find_chrome,
    is_botonomus_build,
    is_testing_build,
)
from .display import VirtualDisplay, container_flags, needs_virtual_display
from .installer import InstalledBinary, find_installed, install, installed_binaries, uninstall
from .version import executable_sha256, executable_version

__all__ = [
    "BUILD_MARKER",
    "Backend",
    "BrowserHandle",
    "ChromeBackend",
    "ChromeHandle",
    "InstalledBinary",
    "VirtualDisplay",
    "container_flags",
    "executable_sha256",
    "executable_version",
    "find_botonomus_chromium",
    "find_chrome",
    "find_installed",
    "install",
    "installed_binaries",
    "is_botonomus_build",
    "is_testing_build",
    "launch_arguments",
    "needs_virtual_display",
    "uninstall",
]
