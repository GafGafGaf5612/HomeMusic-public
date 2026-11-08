"""Accounts: by invitation (the admin creates a one-time code, the person registers with it) or by email (a code in
a letter, no admin involved); each is a new Navidrome user, who may sign in with a linked Google account later."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import deque
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth, mailer
from app.config import get_settings
from app.db import session_scope
from app.google_auth import GoogleTokenError, verify_id_token
from app.library.navidrome import AuthError, NavidromeAdmin, NavidromeUserExists
from app.models import EmailCode, Invite, User, utcnow
from app.security import decrypt

log = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: the code is read out and typed by hand
CODE_LEN = 8
# letters of any script, digits, "._-": Navidrome takes them, and the name is typed in the player too
USERNAME_RE = re.compile(r"^[\w.\-]{2,32}$")
MIN_PASSWORD = 6

# Wrong codes: the codes are long enough, this only keeps a guesser from trying for long
_FAIL_WINDOW_S = 600
_FAIL_LIMIT = 30
_failures: deque[float] = deque()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_RESEND_S = 60  # a new letter to the same address
EMAIL_HOURLY = 5  # letters to one address an hour
EMAIL_ATTEMPTS = 5  # wrong codes before the letter's code stops working


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def normalize_code(code: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", code.upper())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}" if len(code) == 8 else code


def invite_status(inv: Invite) -> str:
    if inv.used_at is not None:
        return "used"
    return "expired" if inv.expires_at < utcnow() else "active"


async def create_invite(s: AsyncSession, admin: User, note: str, days: int | None) -> Invite:
    days = days or get_settings().invite_days
    while True:
        code = new_code()
        if (await s.execute(select(Invite.id).where(Invite.code == code))).first() is None:
            break
    inv = Invite(code=code, note=note.strip(), created_by=admin.id, expires_at=utcnow() + timedelta(days=days))
    s.add(inv)
    await s.flush()
    return inv


def _check_rate() -> None:
    now = time.monotonic()
    while _failures and now - _failures[0] > _FAIL_WINDOW_S:
        _failures.popleft()
    if len(_failures) >= _FAIL_LIMIT:
        raise HTTPException(429, "Слишком много попыток. Подождите несколько минут")


def _fail(status: int, detail: str) -> HTTPException:
    _failures.append(time.monotonic())
    return HTTPException(status, detail)


def check_new_account(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Имя: от 2 до 32 букв или цифр, можно «.», «_», «-», без пробелов")
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Пароль — не короче {MIN_PASSWORD} символов")


async def register(s: AsyncSession, code: str, username: str, password: str,
                   google: dict[str, Any] | None = None) -> tuple[User, str]:
    """Creates the Navidrome user for an unused invite and signs in. Returns (user, session token)."""
    _check_rate()
    username = username.strip()
    check_new_account(username, password)
    norm = normalize_code(code)
    inv = (await s.execute(select(Invite).where(Invite.code == norm))).scalar_one_or_none() if norm else None
    if inv is None or invite_status(inv) != "active":
        raise _fail(400, "Код приглашения не подходит: проверьте его или попросите новый")
    await _check_name_free(s, username)
    if google is not None:
        await _check_google_free(s, google)
    # Taken first, within this transaction: a second registration with the same code waits for it and finds it used.
    res = await s.execute(update(Invite).where(Invite.id == inv.id, Invite.used_at.is_(None)).values(used_at=utcnow()))
    if res.rowcount != 1:
        raise _fail(400, "Код приглашения уже использован")
    user, token = await _create_account(s, username, password, inv.note or username)
    inv.used_by = user.id
    if google is not None:
        user.google_sub, user.google_email = str(google["sub"]), google.get("email")
    log.info("registered %s by invite %s", username, inv.id, extra={"user_id": user.id})
    return user, token


async def _check_name_free(s: AsyncSession, username: str) -> None:
    taken = await s.execute(select(User.id).where(func.lower(User.nd_username) == username.lower()))
    if taken.first() is not None:
        raise HTTPException(409, "Это имя уже занято, выберите другое")


async def _create_account(s: AsyncSession, username: str, password: str, name: str) -> tuple[User, str]:
    """The Navidrome user, then a Hub session for it."""
    try:
        async with NavidromeAdmin() as nd:
            await nd.create_user(username, password, name=name)
    except NavidromeUserExists as e:
        raise HTTPException(409, "Это имя уже занято, выберите другое") from e
    except AuthError as e:
        log.error("navidrome admin login failed: check NAVIDROME_ADMIN_USER/PASSWORD")
        raise HTTPException(502, "Сервер музыки не принял учётную запись администратора") from e
    return await auth.login(s, username, password)


# --- registration by email ------------------------------------------------------------------


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _code_hash(email: str, code: str) -> str:
    key = get_settings().hub_secret_key.encode()
    return hmac.new(key, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _check_email_signup() -> None:
    if not mailer.configured():
        raise HTTPException(404, "Регистрация по почте на этом сервере не настроена")


async def _check_email_free(s: AsyncSession, email: str) -> None:
    if (await s.execute(select(User.id).where(User.email == email))).first() is not None:
        raise HTTPException(409, "С этой почтой аккаунт уже есть — войдите в него")


async def send_email_code(email: str) -> int:
    """Sends a registration code to the address; returns the seconds before another letter may be asked for.
    The letter goes out after the commit: SMTP takes seconds, the database is not held for them."""
    _check_email_signup()
    email = normalize_email(email)
    if len(email) > 320 or not EMAIL_RE.match(email):
        raise HTTPException(400, "Проверьте адрес почты")
    settings = get_settings()
    code = f"{secrets.randbelow(10**6):06d}"
    now = utcnow()
    async with session_scope() as s:
        await _check_email_free(s, email)
        recent = list((await s.execute(select(EmailCode.created_at).where(
            EmailCode.email == email, EmailCode.created_at >= now - timedelta(hours=1)))).scalars())
        if recent and (wait := EMAIL_RESEND_S - int((now - max(recent)).total_seconds())) > 0:
            raise HTTPException(429, f"Письмо уже отправлено. Новое можно запросить через {wait} с")
        if len(recent) >= EMAIL_HOURLY:
            raise HTTPException(429, "Слишком много писем на этот адрес. Попробуйте через час")
        today = (await s.execute(select(func.count(EmailCode.id))
                                 .where(EmailCode.created_at >= now - timedelta(days=1)))).scalar_one()
        if today >= settings.email_daily_limit:
            log.warning("email daily limit %s reached", settings.email_daily_limit)
            raise HTTPException(503, "Сегодня сервер уже отправил все письма. Попробуйте завтра")
        # only the latest letter counts
        await s.execute(update(EmailCode).where(EmailCode.email == email, EmailCode.used_at.is_(None))
                        .values(used_at=now))
        row = EmailCode(email=email, code_hash=_code_hash(email, code),
                        expires_at=now + timedelta(minutes=settings.email_code_minutes))
        s.add(row)
        await s.flush()
        row_id = row.id
    text = (f"Ваш код для регистрации в HomeMusic: {code}\n\n"
            f"Введите его на экране регистрации. Код действует {settings.email_code_minutes} минут.\n"
            "Если вы не регистрировались в HomeMusic, просто не обращайте внимания на это письмо.\n")
    try:
        await mailer.send(email, f"Код HomeMusic: {code}", text)
    except mailer.MailError as e:
        async with session_scope() as s:  # not sent: asking again right away is fine
            await s.execute(delete(EmailCode).where(EmailCode.id == row_id))
        raise HTTPException(502, "Не удалось отправить письмо. Проверьте адрес или попробуйте позже") from e
    log.info("registration code sent to %s", email)
    return EMAIL_RESEND_S


async def register_by_email(s: AsyncSession, email: str, code: str, username: str, password: str
                            ) -> tuple[User, str]:
    """Creates the Navidrome user for an address confirmed by the code from its letter and signs in."""
    _check_email_signup()
    _check_rate()
    email, username = normalize_email(email), username.strip()
    check_new_account(username, password)
    row = (await s.execute(select(EmailCode).where(EmailCode.email == email, EmailCode.used_at.is_(None))
                           .order_by(EmailCode.id.desc()).limit(1))).scalar_one_or_none()
    if row is None or row.expires_at < utcnow():
        raise _fail(400, "Код устарел или уже использован: запросите новое письмо")
    if not hmac.compare_digest(row.code_hash, _code_hash(email, re.sub(r"\D", "", code))):
        left = EMAIL_ATTEMPTS - row.attempts - 1
        async with session_scope() as w:  # counted although this request's own transaction rolls back
            await w.execute(update(EmailCode).where(EmailCode.id == row.id).values(
                attempts=EmailCode.attempts + 1, used_at=utcnow() if left <= 0 else None))
        raise _fail(400, f"Неверный код: осталось попыток — {left}" if left > 0
                    else "Неверный код. Запросите новое письмо")
    await _check_email_free(s, email)
    await _check_name_free(s, username)
    # Taken first, as an invite is: a second registration with the same letter finds it used.
    res = await s.execute(update(EmailCode).where(EmailCode.id == row.id, EmailCode.used_at.is_(None))
                          .values(used_at=utcnow()))
    if res.rowcount != 1:
        raise _fail(400, "Код уже использован: запросите новое письмо")
    user, token = await _create_account(s, username, password, username)
    user.email = email
    log.info("registered %s by email", username, extra={"user_id": user.id})
    return user, token


async def purge_email_codes() -> int:
    """Codes past the daily count are of no use; returns how many went."""
    async with session_scope() as s:
        res = await s.execute(delete(EmailCode).where(EmailCode.created_at < utcnow() - timedelta(days=2)))
        return res.rowcount


async def username_for_login(s: AsyncSession, login: str) -> str:
    """Sign-in takes the name, or the email the account registered with."""
    login = login.strip()
    if "@" in login:
        name = (await s.execute(select(User.nd_username).where(User.email == normalize_email(login)))).scalar()
        if name:
            return name
    return login


async def _google_claims(id_token: str) -> dict[str, Any]:
    if not get_settings().google_client_id:
        raise HTTPException(404, "Вход через Google на этом сервере не настроен")
    try:
        return await verify_id_token(id_token)
    except GoogleTokenError as e:
        log.warning("google token rejected: %s", e)
        raise HTTPException(401, "Google не подтвердил вход, попробуйте ещё раз") from e


async def _check_google_free(s: AsyncSession, claims: dict[str, Any], user: User | None = None) -> None:
    other = (await s.execute(select(User).where(User.google_sub == str(claims["sub"])))).scalar_one_or_none()
    if other is not None and (user is None or other.id != user.id):
        raise HTTPException(409, f"Этот Google-аккаунт уже привязан к пользователю «{other.nd_username}»")


def username_from_email(email: str | None) -> str:
    local = (email or "").split("@", 1)[0].lower()
    name = re.sub(r"[^\w.\-]", "", local)[:32]
    return name if len(name) >= 2 else f"user{secrets.randbelow(10000):04d}"


async def google_login(s: AsyncSession, id_token: str, code: str | None, username: str | None
                       ) -> tuple[User, str, str]:
    """Sign-in with Google: the linked user, or a new one with an invite code. Returns (user, token, password)."""
    claims = await _google_claims(id_token)
    user = (await s.execute(select(User).where(User.google_sub == str(claims["sub"])))).scalar_one_or_none()
    if user is not None:
        password = decrypt(user.nd_password_enc)
        try:
            user, token = await auth.login(s, user.nd_username, password)
        except HTTPException as e:
            if e.status_code != 401:
                raise
            raise HTTPException(401, "Пароль аккаунта сменили в Navidrome: войдите паролем и привяжите Google снова") from e
        return user, token, password
    if not code:
        raise HTTPException(409, "Этот Google-аккаунт ещё не привязан. Введите код приглашения, "
                                 "а если аккаунт уже есть — войдите паролем и привяжите Google в настройках")
    password = secrets.token_urlsafe(12)
    user, token = await register(s, code, (username or "").strip() or username_from_email(claims.get("email")),
                                 password, google=claims)
    return user, token, password


async def link_google(s: AsyncSession, user: User, id_token: str) -> User:
    claims = await _google_claims(id_token)
    await _check_google_free(s, claims, user)
    user.google_sub, user.google_email = str(claims["sub"]), claims.get("email")
    return user
