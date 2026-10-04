"""Profile preferences Botonomus sets before a launch.

Chrome reads its WebRTC IP handling policy only from the profile preference
``webrtc.ip_handling_policy``; ``--force-webrtc-ip-handling-policy`` is honoured by
content_shell and headless shells, not by Chrome. Without the preference, WebRTC's UDP
traffic bypasses a proxy and reveals the real address.
"""

import json
import os
from pathlib import Path
from typing import Any

WEBRTC_POLICY = "disable_non_proxied_udp"


def ensure_webrtc_policy(profile_path: Path) -> None:
    """Set ``webrtc.ip_handling_policy`` in the profile's ``Default/Preferences``.

    Other preferences are kept. An unreadable file is replaced by one holding only this
    preference (Chrome would otherwise reset it to defaults anyway).
    """
    path = Path(profile_path) / "Default" / "Preferences"
    data: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass
    webrtc = data.get("webrtc")
    if not isinstance(webrtc, dict):
        webrtc = {}
    if webrtc.get("ip_handling_policy") == WEBRTC_POLICY and path.exists():
        return
    webrtc["ip_handling_policy"] = WEBRTC_POLICY
    data["webrtc"] = webrtc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".botonomus-tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temporary, path)
