"""Admin insights (INSIGHTS.md 7): song weights, launches and listening statistics of every user. Read-only: no
writes, no network (the embedder's facets only), every answer `Cache-Control: no-store`."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    InsightsListeningOut,
    InsightsMapOut,
    InsightsMatrixOut,
    InsightsMixOut,
    InsightsMixRow,
    InsightsOverviewOut,
    InsightsTasteOut,
    InsightsTasteTrackOut,
    InsightsTasteTracksOut,
    InsightsWaveOut,
    InsightsWavesOut,
)
from app.auth import admin_user
from app.config import get_settings
from app.db import get_session
from app.insights import analytics, launches, maps, mixes_view, taste_view, waves_view
from app.models import Mix, PlayEvent, User, WaveSession, utcnow


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/admin", tags=["admin insights"], dependencies=[Depends(_no_store)])

Model = Literal["v1", "v2"] | None
LIMIT = 200


async def _user(s: AsyncSession, user_id: int) -> User:
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    return user


def _period(start: date | None, end: date | None) -> tuple[date, date]:
    try:
        return analytics.period(start, end, get_settings().timezone, utcnow())
    except ValueError:
        raise HTTPException(422, "Неверный период") from None


async def _users(s: AsyncSession, include_excluded: bool) -> list[tuple[User, bool]]:
    excluded = set(get_settings().cfg.analytics.exclude_users)
    users = (await s.execute(select(User).order_by(User.id))).scalars()
    return [(u, u.nd_username in excluded) for u in users if include_excluded or u.nd_username not in excluded]


@router.get("/insights/overview", response_model=InsightsOverviewOut)
async def overview(start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to"),
                   include_excluded: bool = False, _: User = Depends(admin_user),
                   s: AsyncSession = Depends(get_session, scope="function")):
    start, end = _period(start, end)
    return await analytics.overview(s, await _users(s, include_excluded), start, end, get_settings().timezone,
                                    utcnow())


@router.get("/users/{user_id}/listening", response_model=InsightsListeningOut)
async def listening(user_id: int, start: date | None = Query(None, alias="from"),
                    end: date | None = Query(None, alias="to"), _: User = Depends(admin_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    user = await _user(s, user_id)
    start, end = _period(start, end)
    return await analytics.listening(s, user, start, end, get_settings().timezone, utcnow())


@router.get("/users/{user_id}/taste", response_model=InsightsTasteOut)
async def taste(user_id: int, model: Model = None, _: User = Depends(admin_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    return await taste_view.summary(s, await _user(s, user_id), model, utcnow())


@router.get("/users/{user_id}/taste/tracks", response_model=InsightsTasteTracksOut)
async def taste_tracks(user_id: int, model: Model = None,
                       filter: Literal["all", "positive", "seed", "negative", "liked", "disliked", "downloaded"] = "all",
                       q: str | None = Query(None, max_length=200),
                       sort: Literal["s", "p", "n", "last_heard", "plays", "title"] = "s",
                       limit: int = Query(50, ge=1, le=LIMIT), offset: int = Query(0, ge=0),
                       _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await taste_view.tracks(s, await _user(s, user_id), model=model, flt=filter, q=q, sort=sort, limit=limit,
                                   offset=offset, now=utcnow())


@router.get("/users/{user_id}/taste/track", response_model=InsightsTasteTrackOut)
async def taste_track(user_id: int, key: str = Query(min_length=1, max_length=128), model: Model = None,
                      _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    out = await taste_view.track(s, await _user(s, user_id), key=key, model=model, now=utcnow())
    if out is None:
        raise HTTPException(404, "Трека нет в профиле")
    return out


@router.get("/users/{user_id}/taste/map", response_model=InsightsMapOut)
async def taste_map(user_id: int, model: Model = None, basis: Literal["global", "user"] = "global",
                    mix: int | None = Query(None, ge=1), wave: str | None = Query(None, max_length=32),
                    limit: int = Query(maps.DEFAULT_LIMIT, ge=1, le=2000), _: User = Depends(admin_user),
                    s: AsyncSession = Depends(get_session, scope="function")):
    try:
        return await maps.build(s, await _user(s, user_id), model=model, basis=basis, mix_id=mix, wave_id=wave,
                                limit=limit, now=utcnow())
    except LookupError as e:
        raise HTTPException(404, "Микса нет у пользователя" if str(e) == "mix" else "Сессии волны нет у пользователя")             from None


@router.get("/taste/matrix", response_model=InsightsMatrixOut)
async def taste_matrix(by: Literal["track", "artist"] = "track", model: Model = None,
                       filter: Literal["all", "shared_liked", "conflict", "single", "disliked"] = "all",
                       q: str | None = Query(None, max_length=200),
                       sort: str = Query("sum", pattern=r"^(sum|spread|title|user:\d+)$"),
                       limit: int = Query(50, ge=1, le=LIMIT), offset: int = Query(0, ge=0),
                       include_excluded: bool = False, _: User = Depends(admin_user),
                       s: AsyncSession = Depends(get_session, scope="function")):
    users = [u for u, _excluded in await _users(s, include_excluded)]
    return await taste_view.matrix(s, users, by=by, model=model, flt=filter, q=q, sort=sort, limit=limit,
                                   offset=offset, now=utcnow())


@router.get("/users/{user_id}/mixes", response_model=list[InsightsMixRow])
async def mixes(user_id: int, limit: int = Query(30, ge=1, le=LIMIT), _: User = Depends(admin_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    user = await _user(s, user_id)
    return mixes_view.listing(await launches.load(s, user.id, utcnow()), limit)


@router.get("/mixes/{mix_id}", response_model=InsightsMixOut)
async def mix(mix_id: int, _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    row = await s.get(Mix, mix_id)
    out = await mixes_view.detail(s, await launches.load(s, row.user_id, utcnow()), mix_id) if row else None
    if out is None:
        raise HTTPException(404, "Микс не найден")
    return out


@router.get("/users/{user_id}/waves", response_model=InsightsWavesOut)
async def waves(user_id: int, start: date | None = Query(None, alias="from"),
                end: date | None = Query(None, alias="to"), origin: str | None = Query(None, max_length=32),
                seed_kind: str | None = Query(None, max_length=32), limit: int = Query(50, ge=1, le=LIMIT),
                offset: int = Query(0, ge=0), _: User = Depends(admin_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    user = await _user(s, user_id)
    if start is not None or end is not None:
        start, end = _period(start, end)
    begin, stop = analytics.bounds(start, end, get_settings().timezone)
    return waves_view.listing(await launches.load(s, user.id, utcnow()), begin=begin, stop=stop, origin=origin,
                              kind=seed_kind, limit=limit, offset=offset)


@router.get("/waves/{session_id}", response_model=InsightsWaveOut)
async def wave(session_id: str, _: User = Depends(admin_user),
               s: AsyncSession = Depends(get_session, scope="function")):
    row = await s.get(WaveSession, session_id)
    user_id = row.user_id if row else (await s.execute(select(PlayEvent.user_id).where(
        PlayEvent.context == f"wave:{session_id}").limit(1))).scalar_one_or_none()
    out = await waves_view.detail(s, await launches.load(s, user_id, utcnow()), session_id) \
        if user_id is not None else None
    if out is None:
        raise HTTPException(404, "Сессия волны не найдена")
    return out
