# Concurrency

A [`Botonomus`](../reference/core.md) manager admits at most `max_instances` sessions at a time. Each session is a separate browser process with its own profile.

```python
import asyncio
from botonomus import Botonomus


async def main():
    async with Botonomus(max_instances=10) as bot:

        async def work(n: int) -> str:
            async with bot.open(profile=f"worker-{n}") as session:
                await session.page.goto("https://example.com")
                return await session.page.title()

        titles = await asyncio.gather(*(work(i) for i in range(40)))
        print(titles)


asyncio.run(main())
```

Forty jobs run through ten slots. `max_instances` limits simultaneous sessions; it does not launch anything by itself.

## Admission rules

- Requests above the limit wait in `open()`. Cancelling a waiting request does not consume capacity.
- A slot is held from admission until the browser is **confirmed** stopped. A failed or cancelled launch gives its slot and profile back only after cleanup.
- If shutdown cannot be confirmed (`BrowserCleanupError`), the profile and slot stay reserved until the backend confirms it, so an uncertain process is never handed to another session.
- `bot.active_count` reports reserved slots, including launches and unconfirmed cleanup.
- `await bot.close()` (or leaving the `async with`) rejects new requests, wakes waiters with `ManagerClosedError` and closes every session. It is idempotent and shielded from cancellation.

## Rules of thumb

- **One manager, one event loop, entered once.** Entering it twice raises `ManagerClosedError`.
- **Do not nest beyond capacity.** A session opened inside another session needs a second slot. With `max_instances=1` the inner `open()` waits forever.
- **Measure before scaling.** Each browser is a full process and costs far more RAM and CPU than a tab. Real websites cost more than the local probe page.
- **Windows needs a subprocess-capable event loop.** The default loop from `asyncio.run` (proactor) works.

## Measuring capacity

```bash
botonomus benchmark --levels 1,2,5
botonomus benchmark --levels 10,20,40,80 --output artifacts/benchmark.json
```

The benchmark starts browsers sequentially and holds them open simultaneously at each level. It records startup duration, active sessions, failures, peak sampled host memory (whole-host, not browser-only) and the host-memory growth per active session (in `contexts` mode this includes the shared browser, so it falls as the level rises). `--mode contexts` measures the [Client](client.md) model instead: one browser with an in-memory context per tab. It stops admitting below 15% available RAM or after two failures, closes the level, then decides whether to continue.

The initial measured run completed 1 session in 1.125 s and 2 simultaneously active sessions in 3.031 s, including local navigation, with zero failures. Levels 10-80 have not been benchmarked, and no capacity or throughput figure is promised.

The repository's `examples/concurrent_sessions.py` runs queued work against a local page:

```bash
python examples/concurrent_sessions.py --limit 2 --jobs 4
```
