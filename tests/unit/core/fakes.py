"""Event-controlled browser boundary for deterministic lifecycle races."""

import asyncio


class FakeHandle:
    def __init__(self):
        self.page = object()
        self.context = object()
        self.closed = False
        self.close_error = False
        self.close_gate = asyncio.Event()
        self.close_gate.set()

    async def close(self):
        await self.close_gate.wait()
        if self.close_error:
            raise OSError("close failed")
        self.closed = True


class FakeBackend:
    def __init__(self):
        self.handles = []
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.fail = False
        self.stopped = False
        self.configs = []

    async def start(self):
        pass

    async def launch(self, profile_path, config):
        self.configs.append(config)
        self.entered.set()
        await self.gate.wait()
        if self.fail:
            raise OSError("launch failed")
        handle = FakeHandle()
        self.handles.append(handle)
        return handle

    async def close(self):
        for handle in self.handles:
            handle.closed = True
        self.stopped = True
