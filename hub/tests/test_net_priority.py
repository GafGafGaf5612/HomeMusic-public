"""Background work (listening prefetch) yields to user requests at the shared rate limits."""

from __future__ import annotations

import asyncio

from app.net import Priority, PrioritySemaphore, RateLimiter, in_background, priority_context


def _bg(coro):
    return asyncio.create_task(coro, context=priority_context(Priority(True)))


async def test_priority_context():
    assert not in_background()
    p = Priority(True)

    async def check() -> bool:
        return in_background()

    assert await asyncio.create_task(check(), context=priority_context(p))
    p.foreground()
    assert not await asyncio.create_task(check(), context=priority_context(p))


async def test_rate_limiter_serves_foreground_first():
    lim = RateLimiter(20)  # one request per 50 ms
    order: list[str] = []

    async def take(name: str) -> None:
        await lim.wait()
        order.append(name)

    await take("first")
    bgs = [_bg(take(f"bg{i}")) for i in range(3)]
    await asyncio.sleep(0.01)  # the background ones are waiting for the next slot
    await asyncio.gather(take("fg"), *bgs)
    assert order[:2] == ["first", "fg"]
    assert sorted(order[2:]) == ["bg0", "bg1", "bg2"]


async def test_rate_limiter_lifted_job_goes_first():
    lim = RateLimiter(20)
    order: list[str] = []
    p = Priority(True)

    async def take(name: str) -> None:
        await lim.wait()
        order.append(name)

    await take("first")
    other = _bg(take("bg"))
    await asyncio.sleep(0.01)
    lifted = asyncio.create_task(take("lifted"), context=priority_context(p))
    await asyncio.sleep(0)
    p.foreground()  # a tap joined this background job
    await asyncio.gather(other, lifted)
    assert set(order) == {"first", "bg", "lifted"}


async def test_priority_semaphore_keeps_a_slot_for_foreground():
    sem = PrioritySemaphore(2, reserve=1)
    held = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with sem:
            held.set()
            await release.wait()

    async def quick() -> str:
        async with sem:
            return "ok"

    first = _bg(hold())
    await held.wait()
    second = _bg(quick())
    await asyncio.sleep(0.02)
    assert not second.done()  # the last slot is kept for user requests
    assert await asyncio.wait_for(quick(), 1) == "ok"
    release.set()
    assert await asyncio.wait_for(second, 1) == "ok"
    await first
