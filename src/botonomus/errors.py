"""Public exception hierarchy.

Every error Botonomus raises derives from `BotonomusError`. Low-level causes
(OS errors, protocol errors, driver exceptions) are preserved as ``__cause__``.
Messages never contain credentials, cookies, page contents or navigation URLs.
"""


class BotonomusError(Exception):
    """Base class for all Botonomus errors."""


class ConfigurationError(BotonomusError, ValueError):
    """A configuration value, argument or profile name is invalid."""


class BrowserUnavailableError(BotonomusError):
    """The requested browser executable cannot be found."""


class ProfileInUseError(BotonomusError):
    """Another session, in this or another process, owns the profile."""


class ManagerClosedError(BotonomusError):
    """The manager is not open for new sessions."""


class BrowserStartupError(BotonomusError):
    """The browser process, driver, context or page could not start."""


class BrowserCleanupError(BotonomusError):
    """Owned browser resources could not be confirmed stopped.

    The affected profile and capacity stay reserved until the backend confirms
    shutdown, so an uncertain process is never handed to another session.
    """


class BinaryNotInstalledError(BrowserUnavailableError):
    """The requested Botonomus Chromium build is not installed locally.

    Run ``botonomus install`` (or [`botonomus.browser.install`][botonomus.browser.install]) first.
    """


class BinaryVerificationError(BotonomusError):
    """A downloaded release manifest or artifact failed authentication or integrity checks.

    Raised for a bad manifest signature, a malformed or unsupported manifest, a size or
    SHA-256 mismatch, or an unsafe archive entry. Nothing from the failed download is
    installed.
    """


class BinaryDownloadError(BotonomusError):
    """A release manifest, signature or artifact could not be fetched.

    The message never contains the request URL, which may carry access tokens; the
    underlying network error is preserved as ``__cause__``.
    """


class PersonaUnsupportedError(ConfigurationError):
    """A persona was requested for a browser that cannot apply one.

    Personas are applied by Botonomus Chromium at the C++ level. Stock Google Chrome
    has no such switches, and Botonomus never falls back to JavaScript spoofing,
    which detectors find easily.
    """


class GeoLookupError(BotonomusError):
    """The proxy's exit location could not be determined.

    Raised when ``geoip=True`` and every lookup provider failed. Provider failures
    are preserved as ``__cause__``.
    """


class GeoMismatchError(BotonomusError):
    """The browser's time zone cannot be made to agree with the proxy exit.

    Stock Google Chrome uses the host time zone. When it differs from the exit's,
    sites see a contradiction. Use Botonomus Chromium, change the host time zone,
    or pass ``allow_timezone_mismatch=True`` to accept the risk.
    """
