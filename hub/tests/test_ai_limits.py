"""Daily allowance at the DB boundary: no model, network or real waiting."""

import asyncio
from datetime import date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import db
from app.ai import limits
from app.models import AssistantDailyUsage, User


@pytest.fixture
async def users(hub_env, db_file):
    db.init_engine(db_file)
    async with db.session_scope() as s:
        ordinary = User(nd_username="member", nd_password_enc="unused", is_admin=False)
        other = User(nd_username="other", nd_password_enc="unused", is_admin=False)
        admin = User(nd_username="admin", nd_password_enc="unused", is_admin=True)
        s.add_all([ordinary, other, admin])
    yield ordinary, other, admin
    await db.dispose()


async def reserve(user):
    async with db.session_scope() as s:
        await limits.reserve_message(s, user)


async def test_parallel_messages_allow_exactly_five_and_keep_users_independent(users):
    ordinary, other, admin = users
    results = await asyncio.gather(*(reserve(ordinary) for _ in range(8)), return_exceptions=True)
    assert results.count(None) == 5
    denied = [r for r in results if isinstance(r, HTTPException)]
    assert len(denied) == 3 and all(r.status_code == 429 for r in denied)
    await reserve(other)
    for _ in range(7):
        await reserve(admin)
    async with db.session_scope() as s:
        rows = {row.user_id: row.messages for row in (await s.scalars(select(AssistantDailyUsage))).all()}
        assert rows == {ordinary.id: 5, other.id: 1}
        assert admin.id not in rows


@pytest.mark.parametrize(("zone", "before", "after", "expected_day"), [
    ("Europe/Moscow", datetime(2026, 10, 29, 20, 59, 59), datetime(2026, 10, 29, 21), date(2026, 10, 30)),
    ("America/New_York", datetime(2026, 11, 1, 3, 59, 59), datetime(2026, 11, 1, 4), date(2026, 11, 1)),
], ids=["moscow-midnight", "dst-day-midnight"])
async def test_allowance_resets_at_local_midnight(users, monkeypatch, zone, before, after, expected_day):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "timezone", zone)
    monkeypatch.setattr(limits, "utcnow", lambda: before)
    for _ in range(5):
        await reserve(users[0])
    with pytest.raises(HTTPException) as exc:
        await reserve(users[0])
    assert exc.value.headers["Retry-After"] == "1"
    monkeypatch.setattr(limits, "utcnow", lambda: after)
    await reserve(users[0])
    async with db.session_scope() as s:
        row = await s.get(AssistantDailyUsage, (users[0].id, expected_day))
        assert (row.day, row.messages) == (expected_day, 1)


async def test_reservation_survives_failed_work_and_engine_restart(users, db_file):
    with pytest.raises(RuntimeError):
        async with db.session_scope() as s:
            await limits.reserve_message(s, users[0])
            raise RuntimeError("model failed after admission")
    await db.dispose()
    db.init_engine(db_file)
    async with db.session_scope() as s:
        assert (await s.scalars(select(AssistantDailyUsage))).one().messages == 1
