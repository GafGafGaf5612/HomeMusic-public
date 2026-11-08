"""Login with Navidrome credentials (FR-1), cookie sessions, per-user Navidrome clients."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.library.navidrome import AuthError, NavidromeClient
from app.models import Session, User, utcnow
from app.security import decrypt, encrypt, hash_token, new_token

COOKIE = "hm_session"
SEEN_EVERY = timedelta(minutes=5)  # how often a request refreshes User.last_seen_at (admin's user list)


async def login(s: AsyncSession, username: str, password: str) -> tuple[User, str]:
    """Check the credentials against Navidrome (ping), upsert the user, create a session. Returns (user, token)."""
    nd = NavidromeClient(username, password)
    try:
        await nd.ping()
        info = await nd.get_user()
    except AuthError as e:
        raise HTTPException(401, "Неверный логин или пароль") from e
    finally:
        await nd.aclose()

    user = (await s.execute(select(User).where(User.nd_username == username))).scalar_one_or_none()
    if user is None:
        user = User(nd_username=username, nd_password_enc=encrypt(password))
        s.add(user)
    else:
        user.nd_password_enc = encrypt(password)
    user.is_admin = bool(info.get("adminRole"))
    user.last_seen_at = utcnow()
    await s.flush()

    token, token_hash = new_token()
    s.add(Session(id=token_hash, user_id=user.id, expires_at=utcnow() + timedelta(days=get_settings().session_days)))
    await s.execute(delete(Session).where(Session.expires_at < utcnow()))
    return user, token


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=get_settings().session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


async def logout(s: AsyncSession, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        await s.execute(delete(Session).where(Session.id == hash_token(token)))
    response.delete_cookie(COOKIE, path="/")


async def current_user(request: Request, s: AsyncSession = Depends(get_session, scope="function")) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Требуется вход")
    sess = await s.get(Session, hash_token(token))
    if sess is None or sess.expires_at < utcnow():
        raise HTTPException(401, "Сессия истекла, войдите снова")
    user = await s.get(User, sess.user_id)
    if user is None:
        raise HTTPException(401, "Требуется вход")
    now = utcnow()
    if user.last_seen_at is None or now - user.last_seen_at > SEEN_EVERY:
        user.last_seen_at = now
    # Provider resolution may take minutes and itself needs DB connections. Release the
    # authentication transaction before entering the endpoint, retaining the loaded user
    # in the shared session (expire_on_commit=False) for subsequent account updates.
    await s.commit()
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Только для администратора")
    return user


def user_navidrome(user: User) -> NavidromeClient:
    """Navidrome client acting as this user (their playlists and stars)."""
    return NavidromeClient(user.nd_username, decrypt(user.nd_password_enc))
