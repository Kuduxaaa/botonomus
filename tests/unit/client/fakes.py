"""A scripted CDP connection for client unit tests."""

from botonomus.cdp.connection import CDPSession


class FakeConnection:
    """Records every command; answers from ``replies`` (method -> result or callable)."""

    def __init__(self, replies=None):
        self.sent = []
        self.replies = {
            "Target.createBrowserContext": {"browserContextId": "ctx-1"},
            "Target.createTarget": {"targetId": "tab-1"},
            "Target.attachToTarget": {"sessionId": "s-1"},
            "Page.getFrameTree": {"frameTree": {"frame": {"id": "frame-1", "url": "about:blank"}}},
            "Storage.getCookies": {"cookies": [{"name": "a", "value": "1"}]},
        }
        self.replies.update(replies or {})
        self.handlers = []

    def session(self, session_id=None):
        return CDPSession(self, session_id)

    async def send(self, method, params=None, session_id=None, timeout=60.0):
        self.sent.append((method, params or {}, session_id))
        reply = self.replies.get(method, {})
        return reply(params or {}) if callable(reply) else reply

    def on(self, event, handler, session_id=None):
        self.handlers.append((event, handler, session_id))

    def off(self, event, handler, session_id=None):
        if (event, handler, session_id) in self.handlers:
            self.handlers.remove((event, handler, session_id))

    def handlers_live(self):
        return len(self.handlers)

    def methods(self):
        return [method for method, _, _ in self.sent]
