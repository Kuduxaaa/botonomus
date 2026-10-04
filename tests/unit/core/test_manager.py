import asyncio

import pytest

from botonomus import Botonomus, BrowserConfig
from botonomus.errors import (
    BrowserCleanupError,
    BrowserStartupError,
    ConfigurationError,
    ManagerClosedError,
    ProfileInUseError,
)
from botonomus.profiles import ProfileLease
from tests.unit.core.fakes import FakeBackend


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def bot(tmp_path, backend):
    return Botonomus(
        config=BrowserConfig(profile_root=tmp_path, close_timeout=0.5), backend=backend
    )


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_capacity_must_be_positive_integer(limit):
    with pytest.raises(ConfigurationError):
        Botonomus(max_instances=limit)


async def test_body_failure_releases_session(bot, backend):
    async with bot:
        with pytest.raises(RuntimeError):
            async with bot.open(profile="first"):
                raise RuntimeError("caller failed")
        assert backend.handles[0].closed
        async with asyncio.timeout(1):
            async with bot.open(profile="second"):
                assert bot.active_count == 1
    assert bot.active_count == 0


async def test_queued_cancellation_does_not_consume_capacity(bot, backend):
    async with bot:
        async with bot.open(profile="first"):
            task = asyncio.create_task(use(bot, "cancelled"))
            await asyncio.sleep(0.02)
            assert len(backend.handles) == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with asyncio.timeout(1):
            await use(bot, "third")


async def use(bot, profile):
    async with bot.open(profile=profile):
        pass


async def test_failed_launch_releases_profile_and_capacity(bot, backend):
    async with bot:
        backend.fail = True
        with pytest.raises(BrowserStartupError) as error:
            await use(bot, "retry")
        assert isinstance(error.value.__cause__, OSError)
        backend.fail = False
        async with asyncio.timeout(1):
            await use(bot, "retry")


async def test_close_wakes_queued_callers(bot, backend):
    async with bot:
        async with bot.open(profile="first"):
            waiter = asyncio.create_task(use(bot, "queued"))
            await asyncio.sleep(0.02)
            await bot.close()
            with pytest.raises(ManagerClosedError):
                await waiter
            assert backend.handles[0].closed


async def test_cancel_during_launch_keeps_ownership_until_cleanup(bot, backend, tmp_path):
    backend.gate.clear()
    async with bot:
        task = asyncio.create_task(use(bot, "slow"))
        await backend.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        backend.gate.set()
    assert backend.handles[0].closed
    assert bot.active_count == 0
    lease = ProfileLease(tmp_path, "slow")
    lease.acquire()
    lease.release()


async def test_launch_finishes_during_shutdown(bot, backend):
    backend.gate.clear()
    async with bot:
        task = asyncio.create_task(use(bot, "slow"))
        await backend.entered.wait()
        closing = asyncio.create_task(bot.close())
        await asyncio.sleep(0.02)
        backend.gate.set()
        await closing
        with pytest.raises(ManagerClosedError):
            await task
    assert all(h.closed for h in backend.handles)


async def test_session_close_idempotent_and_releases_capacity(bot, backend):
    async with bot:
        async with bot.open(profile="first") as session:
            await asyncio.gather(session.close(), session.close())
            assert backend.handles[0].closed
            assert bot.active_count == 0
            await use(bot, "second")


async def test_cancelled_close_continues_cleanup(bot, backend):
    async with bot:
        async with bot.open(profile="first") as session:
            handle = backend.handles[0]
            handle.close_gate.clear()
            closing = asyncio.create_task(session.close())
            await asyncio.sleep(0.02)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            handle.close_gate.set()
        assert handle.closed


async def test_close_failure_does_not_mask_body_error(bot, backend):
    async with bot:
        with pytest.raises(ValueError, match="original"):
            async with bot.open(profile="first"):
                backend.handles[0].close_error = True
                raise ValueError("original")
        assert bot.active_count == 1  # Quarantined until backend confirms shutdown.
    assert bot.active_count == 0
    assert backend.stopped


async def test_open_requires_entered_manager(bot):
    with pytest.raises(ManagerClosedError):
        await use(bot, "early")


async def test_multiple_sessions_overlap(tmp_path, backend):
    async with Botonomus(2, config=BrowserConfig(profile_root=tmp_path), backend=backend) as bot:
        async with bot.open(profile="a"), bot.open(profile="b"):
            assert bot.active_count == 2
            assert len(backend.handles) == 2


async def test_close_during_start_cannot_reopen_manager(tmp_path, backend):
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def slow_start():
        entered.set()
        await resume.wait()

    backend.start = slow_start
    bot = Botonomus(config=BrowserConfig(profile_root=tmp_path), backend=backend)
    entering = asyncio.create_task(bot.__aenter__())
    await entered.wait()
    closing = asyncio.create_task(bot.close())
    await asyncio.sleep(0.02)
    resume.set()
    await closing
    with pytest.raises(ManagerClosedError):
        await entering
    assert backend.stopped
    with pytest.raises(ManagerClosedError):
        await use(bot, "after-close")


async def test_uncertain_launch_keeps_profile_locked_after_failed_backend_close(tmp_path, backend):
    async def uncertain_launch(path, config):
        raise BrowserCleanupError("process exit unconfirmed")

    async def failed_close():
        raise BrowserCleanupError("still alive")

    backend.launch = uncertain_launch
    backend.close = failed_close
    bot = Botonomus(config=BrowserConfig(profile_root=tmp_path), backend=backend)
    await bot.__aenter__()
    with pytest.raises(BrowserCleanupError):
        await use(bot, "uncertain")
    contender = ProfileLease(tmp_path, "uncertain")
    with pytest.raises(ProfileInUseError):
        contender.acquire()
    with pytest.raises(BrowserCleanupError):
        await bot.close()
    try:
        with pytest.raises(ProfileInUseError):
            contender.acquire()
        assert bot.active_count == 1
    finally:
        contender.release()
        # The fake has no actual OS process; release test resources explicitly.
        for owned in tuple(bot._owned):
            owned.lease.release()


async def test_per_session_args_extend_config(bot, backend):
    async with bot:
        async with bot.open(profile="seeded", args=["--fingerprint=7"]):
            pass
        async with bot.open(profile="plain"):
            pass
    assert backend.configs[0].extra_args == ("--fingerprint=7",)
    assert backend.configs[1].extra_args == ()


async def test_per_session_args_are_validated_before_admission(bot, backend):
    async with bot:
        with pytest.raises(ConfigurationError):
            async with bot.open(profile="bad", args=["--remote-debugging-port=1"]):
                pass
        assert bot.active_count == 0
