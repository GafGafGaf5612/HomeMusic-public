"""Admin: users with their AI spending and library numbers, invite codes."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import accounts
from app.api.schemas import (
    AdminDownloadOut,
    AdminUserDetailOut,
    AdminUserOut,
    AdminUsersOut,
    AiMonthOut,
    AiUsageOut,
    InviteIn,
    InviteOut,
)
from app.auth import admin_user, user_navidrome
from app.db import get_session
from app.library.navidrome import NavidromeAdmin
from app.models import AiUsage, DownloadItem, Invite, Track, Upload, User, utcnow

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

ACTIVE = ("queued", "searching", "downloading", "tagging", "importing")
ND_TIMEOUT_S = 8


def _nd_time(value: Any) -> datetime | None:
    """Navidrome's RFC 3339 time ("2026-10-19T22:39:45.908050132Z") as naive UTC, like the Hub's own."""
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        head, _, tail = text.partition(".")
        if tail:  # Python takes at most 6 fraction digits
            digits = "".join(ch for ch in tail if ch.isdigit())
            text = f"{head}.{digits[:6]}{tail[len(digits):]}"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=None) - (dt.utcoffset() or timedelta(0))


async def _nd_users() -> list[dict]:
    try:
        async with NavidromeAdmin() as nd:
            return await asyncio.wait_for(nd.list_users(), timeout=ND_TIMEOUT_S)
    except Exception as e:
        log.warning("navidrome user list failed: %s", e)
        return []


async def _nd_counts(user: User) -> tuple[int | None, int | None]:
    """(♥ songs, playlists) of the user, from Navidrome as that user."""
    nd = user_navidrome(user)
    try:
        starred, playlists = await asyncio.wait_for(asyncio.gather(nd.get_starred2(), nd.get_playlists()),
                                                    timeout=ND_TIMEOUT_S)
        return len(starred.get("song") or []), len(playlists)
    except Exception as e:
        log.warning("navidrome counts for %s failed: %s", user.nd_username, e)
        return None, None
    finally:
        await nd.aclose()


async def _stats(s: AsyncSession, user_ids: list[int]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = defaultdict(dict)
    if not user_ids:
        return out
    month_ago = utcnow() - timedelta(days=30)
    rows = await s.execute(
        select(AiUsage.user_id, func.sum(AiUsage.cost_usd),
               func.sum(case((AiUsage.created_at >= month_ago, AiUsage.cost_usd), else_=0)),
               func.count(AiUsage.id), func.max(AiUsage.created_at))
        .where(AiUsage.user_id.in_(user_ids)).group_by(AiUsage.user_id))
    for uid, total, last30, n, last in rows:
        out[uid].update(ai_cost_usd=round(total or 0, 6), ai_cost_30d_usd=round(last30 or 0, 6),
                        ai_requests=n, ai_last_at=last)
    rows = await s.execute(select(DownloadItem.user_id, DownloadItem.status, func.count(DownloadItem.id))
                           .where(DownloadItem.user_id.in_(user_ids))
                           .group_by(DownloadItem.user_id, DownloadItem.status))
    for uid, status, n in rows:
        key = ("downloads_done" if status == "done" else "downloads_failed" if status in ("failed", "needs_review")
               else "downloads_active" if status in ACTIVE else None)
        if key:
            out[uid][key] = out[uid].get(key, 0) + n
    rows = await s.execute(select(Track.added_by, func.count(Track.id))
                           .where(Track.added_by.in_(user_ids)).group_by(Track.added_by))
    for uid, n in rows:
        out[uid]["tracks_added"] = n
    rows = await s.execute(select(Upload.user_id, func.count(Upload.id))
                           .where(Upload.user_id.in_(user_ids), Upload.status == "imported").group_by(Upload.user_id))
    for uid, n in rows:
        out[uid]["uploads"] = n
    rows = await s.execute(select(Invite.used_by, Invite.note).where(Invite.used_by.in_(user_ids)))
    for uid, note in rows:
        out[uid]["invite_note"] = note or None
    return out


def _user_out(user: User, stats: dict[str, Any], nd: dict | None, counts: tuple[int | None, int | None]) -> dict:
    return dict(
        id=user.id, username=user.nd_username, name=(nd or {}).get("name") or "", is_admin=user.is_admin,
        in_hub=True, created_at=user.created_at, last_seen_at=user.last_seen_at,
        player_seen_at=_nd_time((nd or {}).get("lastAccessAt")), google_email=user.google_email, email=user.email,
        starred_songs=counts[0], playlists=counts[1], **stats,
    )


@router.get("/users", response_model=AdminUsersOut)
async def users(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    hub_users = list((await s.execute(select(User).order_by(User.id))).scalars())
    nd_users, counts = await asyncio.gather(_nd_users(), asyncio.gather(*(_nd_counts(u) for u in hub_users)))
    nd_by_name = {str(u.get("userName", "")).lower(): u for u in nd_users}
    stats = await _stats(s, [u.id for u in hub_users])
    out = [AdminUserOut(**_user_out(u, stats.get(u.id, {}), nd_by_name.pop(u.nd_username.lower(), None), c))
           for u, c in zip(hub_users, counts, strict=True)]
    # Navidrome users who never opened the Hub: the admin sees them too (the player works without the Hub)
    out += [AdminUserOut(id=None, username=u.get("userName", ""), name=u.get("name") or "",
                         is_admin=bool(u.get("isAdmin")), in_hub=False, created_at=_nd_time(u.get("createdAt")),
                         player_seen_at=_nd_time(u.get("lastAccessAt")), starred_songs=None, playlists=None)
            for u in nd_by_name.values()]
    month_ago = utcnow() - timedelta(days=30)
    total, last30 = (await s.execute(
        select(func.sum(AiUsage.cost_usd), func.sum(case((AiUsage.created_at >= month_ago, AiUsage.cost_usd), else_=0)))
    )).one()
    tracks = (await s.execute(select(func.count(Track.id)))).scalar_one()
    return AdminUsersOut(users=out, ai_cost_usd=round(total or 0, 6), ai_cost_30d_usd=round(last30 or 0, 6),
                         library_tracks=tracks)


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
async def user_detail(user_id: int, _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Нет такого пользователя")
    nd_users, counts = await asyncio.gather(_nd_users(), _nd_counts(user))
    nd = next((u for u in nd_users if str(u.get("userName", "")).lower() == user.nd_username.lower()), None)
    stats = (await _stats(s, [user.id])).get(user.id, {})
    month = func.strftime("%Y-%m", AiUsage.created_at)
    months = [AiMonthOut(month=m, cost_usd=round(c or 0, 6), requests=n) for m, c, n in await s.execute(
        select(month, func.sum(AiUsage.cost_usd), func.count(AiUsage.id))
        .where(AiUsage.user_id == user.id).group_by(month).order_by(month.desc()).limit(12))]
    recent = [AiUsageOut(kind=r.kind, tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=round(r.cost_usd, 6),
                         created_at=r.created_at)
              for r in (await s.execute(select(AiUsage).where(AiUsage.user_id == user.id)
                                        .order_by(AiUsage.id.desc()).limit(20))).scalars()]
    downloads = []
    for d in (await s.execute(select(DownloadItem).where(DownloadItem.user_id == user.id)
                              .order_by(DownloadItem.id.desc()).limit(15))).scalars():
        q = (d.query_json or {}).get("query") or {}
        downloads.append(AdminDownloadOut(artist=q.get("artist") or "", title=q.get("title") or "", status=d.status,
                                          created_at=d.created_at))
    return AdminUserDetailOut(**_user_out(user, stats, nd, counts), ai_months=months, ai_recent=recent,
                              recent_downloads=downloads)


async def _invite_out(s: AsyncSession, inv: Invite) -> InviteOut:
    used_by = (await s.get(User, inv.used_by)).nd_username if inv.used_by else None
    return InviteOut(id=inv.id, code=accounts.format_code(inv.code), note=inv.note, status=accounts.invite_status(inv),
                     created_at=inv.created_at, expires_at=inv.expires_at, used_at=inv.used_at, used_by=used_by)


@router.get("/invites", response_model=list[InviteOut])
async def invites(_: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    # used and expired codes stay in the list for a month, then only the user list remembers the invite
    since = utcnow() - timedelta(days=30)
    rows = (await s.execute(select(Invite).where((Invite.used_at.is_(None) & (Invite.expires_at >= since))
                                                 | (Invite.used_at >= since))
                            .order_by(Invite.id.desc()))).scalars()
    return [await _invite_out(s, inv) for inv in rows]


@router.post("/invites", response_model=InviteOut)
async def create_invite(body: InviteIn, admin: User = Depends(admin_user),
                        s: AsyncSession = Depends(get_session, scope="function")):
    return await _invite_out(s, await accounts.create_invite(s, admin, body.note, body.days))


@router.delete("/invites/{invite_id}")
async def delete_invite(invite_id: int, _: User = Depends(admin_user), s: AsyncSession = Depends(get_session, scope="function")):
    inv = await s.get(Invite, invite_id)
    if inv is None:
        raise HTTPException(404, "Нет такого приглашения")
    if inv.used_at is not None:
        raise HTTPException(409, "Приглашение уже использовано")
    await s.execute(delete(Invite).where(Invite.id == invite_id))
    return {"ok": True}
