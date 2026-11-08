"""Reserve one message before any assistant work, across sessions and restarts."""

from datetime import UTC, datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AssistantDailyUsage, User, utcnow

DAILY_MESSAGES = 5
MAX_IMAGES = 15
MAX_MESSAGE_CHARS = 5000


def validate_message_text(text: str) -> None:
    if len(text) > MAX_MESSAGE_CHARS:
        raise HTTPException(400, f"В одном сообщении не больше {MAX_MESSAGE_CHARS} символов")


async def reserve_message(s: AsyncSession, user: User) -> None:
    """Atomically consume and commit an accepted request, including ones whose later AI work fails.

    Call after input/ownership validation and before adding tasks or invoking the model.
    Committing here releases SQLite's write lock before slow model/network work starts.
    """
    if user.is_admin:
        return
    now = utcnow().replace(tzinfo=UTC)
    zone = ZoneInfo(get_settings().timezone)
    day = now.astimezone(zone).date()
    usage = AssistantDailyUsage
    stmt = insert(usage).values(user_id=user.id, day=day, messages=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[usage.user_id, usage.day],
        set_={"messages": usage.messages + 1},
        where=usage.messages < DAILY_MESSAGES,
    ).returning(usage.messages)
    if (await s.execute(stmt)).scalar_one_or_none() is None:
        reset = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
        raise HTTPException(
            429,
            f"Дневной лимит — {DAILY_MESSAGES} сообщений ассистенту. Попробуйте завтра: лимит обновится в 00:00 ({zone.key}).",
            headers={"Retry-After": str(max(1, ceil((reset - now).total_seconds())))},
        )
    await s.commit()
