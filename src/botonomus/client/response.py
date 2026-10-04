"""Responses returned by `Client`, shaped like those of ``requests`` and ``httpx``."""

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ..errors import HTTPStatusError

Via = Literal["browser", "http"]
CHARSET = re.compile(r"charset=[\"']?([\w.:-]+)", re.IGNORECASE)


class Headers(Mapping[str, str]):
    """Read-only, case-insensitive response headers; keys are stored in lowercase."""

    def __init__(self, items: Mapping[str, str] | None = None) -> None:
        self._items = {str(key).lower(): str(value) for key, value in (items or {}).items()}

    def __getitem__(self, key: str) -> str:
        return self._items[key.lower()]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"Headers({self._items!r})"


@dataclass(frozen=True, eq=False)
class Response:
    """One completed request.

    Attributes:
        url: Final URL after redirects.
        status: HTTP status code.
        headers: Response headers.
        content: The raw response body.
        via: ``"browser"`` (a real tab loaded it) or ``"http"`` (the fast path).
        elapsed: Seconds from sending to having the body.
        method: Request method.
        html: For browser responses, the rendered DOM after scripts ran; else ``None``.
        cookies: Cookie names and values the context holds for this URL afterwards.
    """

    url: str
    status: int
    headers: Headers
    content: bytes
    via: Via
    elapsed: float
    method: str = "GET"
    html: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)

    @property
    def encoding(self) -> str:
        """The charset from ``Content-Type``, or ``utf-8``."""
        match = CHARSET.search(self.headers.get("content-type", ""))
        return match.group(1) if match else "utf-8"

    @property
    def text(self) -> str:
        """The body decoded with `encoding`; undecodable bytes become U+FFFD."""
        try:
            return self.content.decode(self.encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        """Whether the status is below 400."""
        return self.status < 400

    def json(self) -> Any:
        """The body parsed as JSON."""
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        """Raise `HTTPStatusError` for a 4xx or 5xx status."""
        if self.status >= 400:
            raise HTTPStatusError(self)

    def __repr__(self) -> str:
        return f"<Response [{self.status}] via {self.via} {self.url}>"
