# Linux servers

Botonomus runs headed Chrome on Linux servers through a virtual X display. A headed browser on Xvfb renders like a desktop browser: it has real window and screen geometry and never reports itself as headless.

## Install

```bash
# Debian / Ubuntu
wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt-get install -y /tmp/chrome.deb xvfb
pip install "git+https://github.com/Kuduxaaa/botonomus"
botonomus info        # shows the Chrome that sessions will launch
```

## Virtual display

`BrowserConfig(virtual_display=...)`:

| Value | Behaviour |
|---|---|
| `None` (default) | On Linux, for headed sessions, start Xvfb when neither `DISPLAY` nor `WAYLAND_DISPLAY` is set |
| `True` | Always use Xvfb (Linux only; elsewhere a `ConfigurationError`) |
| `False` | Never; use the existing display, or run `headless=True` |

One Xvfb server (1920x1080, 24-bit) is shared by every browser of a `Botonomus` manager. Xvfb chooses a free display number itself, so several managers or processes can run at once; the server is stopped when the manager closes, even if a browser fails to close. Headless sessions never start it.

!!! warning "Shared machines"
    The display has no access control: any local user can connect to it, watch the screen and send input. Use it on machines no one else logs in to.

If `DISPLAY` points at a display that no longer exists (for example after an SSH session ended), Chrome fails to start; unset `DISPLAY` or pass `virtual_display=True`. Windows are placed at `0,0` with the screen's size.

A missing Xvfb raises `BrowserStartupError` naming the package to install.

## Containers

Botonomus adds only the flags containers need:

- `--disable-dev-shm-usage` when `/dev/shm` is under 512 MB. Docker's default of 64 MB otherwise crashes renderers. Alternatively run the container with `--shm-size=2g`.
- `--no-sandbox` only when running as root, where Chrome refuses to start otherwise. Chrome then shows an "unsupported command-line flag" bar that shrinks the viewport, which pages can notice: run as a non-root user.

## What a server cannot hide

- **No GPU, no real WebGL.** Without a GPU, recent Chrome either disables WebGL or renders it in software; both read as a server to detection services. For WebGL-sensitive sites use machines with a GPU.
- **One device fingerprint per machine.** Every Chrome on a host reports the same hardware, canvas and audio fingerprint, so sites can link sessions that share a machine. Spread a large fleet over several machines, or use Botonomus Chromium personas.
- **Linux is a minority platform.** Chrome on Linux is legitimate but rarer than Windows. Do not present Windows from Linux: fonts, GPU and platform details would disagree.

## Capacity

Each headed browser is a full Chrome. Measure the machine before choosing a pool size:

```bash
botonomus benchmark --levels 1,5,10,20
```

Then check that sessions still look right at that load:

```bash
botonomus consistency --browser chrome --persona off
```
