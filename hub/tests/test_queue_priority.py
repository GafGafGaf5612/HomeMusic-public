"""Job priority: the user's work goes before background work (imports), which never takes the last worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import dispose, init_engine, session_scope
from app.jobs import queue
from app.models import Job


@pytest.fixture
async def db(db_file: Path, monkeypatch: pytest.MonkeyPatch):
    from app import config

    monkeypatch.setenv("CONFIG_FILE", str(db_file.parent / "none.yaml"))
    config.get_settings.cache_clear()
    config.get_settings().cfg.jobs.workers = 3
    init_engine(db_file)
    yield
    await dispose()
    config.get_settings.cache_clear()


async def _add(kind: str, priority: int = 0) -> int:
    async with session_scope() as s:
        return (await queue.enqueue(s, kind, {}, priority=priority)).id


async def _claimed() -> str | None:
    job = await queue._claim("test")
    return job.kind if job else None


async def test_user_work_goes_before_older_background_work(db):
    await _add("import_resolve", queue.PRIORITY_IMPORT)
    await _add("download_item", queue.PRIORITY_IMPORT)
    await _add("download_item")  # a tap, queued last
    assert [await _claimed() for _ in range(3)] == ["download_item", "import_resolve", "download_item"]


async def test_background_work_leaves_a_worker_for_the_user(db):
    for _ in range(3):
        await _add("download_item", queue.PRIORITY_IMPORT)
    assert queue.background_slots() == 2
    assert [await _claimed() for _ in range(3)] == ["download_item", "download_item", None]
    # the next idle worker would sleep until something it may take is due, not spin on the held-back job
    assert await queue._next_wakeup_in() == 30.0
    await _add("listen")
    assert await _claimed() == "listen"


async def test_background_job_runs_with_background_network_priority(db):
    from app.net import in_background

    seen: list[tuple[int, bool]] = []

    @queue.handler("test_probe")
    async def probe(job: Job) -> None:
        seen.append((job.priority, in_background()))

    try:
        await _add("test_probe", queue.PRIORITY_IMPORT)
        await _add("test_probe")
        for _ in range(2):
            job = await queue._claim("test")
            assert job is not None
            await queue.run_job(job)
    finally:
        queue._handlers.pop("test_probe", None)
    assert seen == [(0, False), (queue.PRIORITY_IMPORT, True)]
    assert not in_background()  # the worker itself is back to the foreground
