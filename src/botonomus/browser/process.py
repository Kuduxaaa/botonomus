"""Process ownership: inspect and terminate only processes this backend created.

All functions here are blocking and are called through ``asyncio.to_thread``.
psutil guards destructive operations against PID reuse, so a recycled PID never
leads to terminating an unrelated process.
"""

import socket

import psutil

from ..errors import BrowserCleanupError


def available_port() -> int:
    """Return a currently free TCP port on the loopback interface.

    The port is explicit and nonzero on purpose: Chromium treats
    ``--remote-debugging-port=0`` as automation and reports ``navigator.webdriver``.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def owns_listener(process: psutil.Process, port: int) -> bool:
    """Whether ``process`` itself listens on loopback ``port``.

    Used to confirm the DevTools endpoint belongs to the browser we launched, not to
    another local process that raced for the port.
    """
    try:
        return any(
            connection.status == psutil.CONN_LISTEN
            and connection.laddr.port == port
            and connection.laddr.ip in {"127.0.0.1", "::1"}
            for connection in process.net_connections(kind="tcp")
        )
    except psutil.NoSuchProcess:
        return False


def descendants(process: psutil.Process) -> list[psutil.Process]:
    """All child processes of ``process``, or an empty list if it has exited."""
    try:
        return process.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def stop_owned(processes: list[psutil.Process], timeout: float) -> None:
    """Terminate, then kill, the given owned processes.

    Args:
        processes: Process identities captured when they were created.
        timeout: Total seconds to wait: half after terminate, half after kill.

    Raises:
        BrowserCleanupError: If any process is still alive after both phases.
    """
    remaining = []
    for process in processes:
        try:
            if process.is_running():
                process.terminate()
                remaining.append(process)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(remaining, timeout=timeout / 2)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=timeout / 2)
    if alive:
        raise BrowserCleanupError("Owned browser processes did not exit")
