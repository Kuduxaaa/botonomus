"""Command-line construction for a native, unobservable browser launch."""

from collections.abc import Sequence
from pathlib import Path

from ..config import BrowserConfig
from ..fingerprint import switches


def launch_arguments(
    executable: Path,
    profile_path: Path,
    port: int,
    config: BrowserConfig,
    *,
    proxy_server: str | None = None,
    platform_args: Sequence[str] = (),
) -> list[str]:
    """Build the full browser command line.

    Args:
        executable: Browser executable.
        profile_path: Dedicated ``--user-data-dir``.
        port: Explicit, nonzero loopback debugging port.
        config: Session configuration.
        proxy_server: Credential-free ``--proxy-server`` value, if a proxy is used.
            Credentials are handled by the loopback forwarder and never appear here.
        platform_args: Flags the platform needs (container flags, virtual-display
            window geometry), placed before the user's ``extra_args``.

    Returns:
        Arguments in launch order, ending with the initial ``about:blank`` page.
    """
    arguments = [
        str(executable),
        f"--user-data-dir={profile_path}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if config.headless:
        arguments.append("--headless=new")
    if config.render_when_occluded:
        arguments.append("--disable-backgrounding-occluded-windows")
    if config.locale is not None:
        arguments += [f"--lang={config.locale}", f"--accept-lang={config.locale}"]
    arguments += platform_args
    arguments += config.extra_args
    arguments += config.persona_switches
    arguments += _persona_window(config.persona_switches)
    if proxy_server is not None:
        # WebRTC UDP would otherwise bypass the proxy and reveal the real address.
        arguments += [
            f"--proxy-server={proxy_server}",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]
    arguments.append("about:blank")
    return arguments


def _persona_window(persona_switches: Sequence[str]) -> list[str]:
    """Window geometry that fits a persona screen: at the origin, filling the work area.

    ``outerWidth``/``outerHeight`` come from the real window, so a window larger than
    the persona's screen would contradict it.
    """
    values = dict(arg.split("=", 1) for arg in persona_switches if "=" in arg)
    screen = values.get(switches.SCREEN)
    if screen is None:
        return []
    width, height = (int(part) for part in screen.split("x"))
    taskbar = int(values.get(switches.TASKBAR, "0"))
    return ["--window-position=0,0", f"--window-size={width},{height - taskbar}"]
