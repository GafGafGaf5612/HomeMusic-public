from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import accounts, auth, mailer
from app.api.schemas import (
    AuthOptionsOut,
    EmailCodeIn,
    EmailCodeOut,
    EmailRegisterIn,
    GoogleIn,
    GoogleLinkIn,
    GoogleLoginOut,
    LoginIn,
    MeOut,
    RegisterIn,
)
from app.config import get_settings
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


def me_out(user: User) -> MeOut:
    settings = get_settings()
    return MeOut(id=user.id, username=user.nd_username, is_admin=user.is_admin,
                 public_music_url=settings.public_music_url, public_hub_url=settings.public_hub_url,
                 google_email=user.google_email, email=user.email)


@router.get("/options", response_model=AuthOptionsOut)
async def options():
    settings = get_settings()
    return AuthOptionsOut(invites=True, email_signup=mailer.configured(),
                          google_client_id=settings.google_client_id or None, music_url=settings.public_music_url)


@router.post("/login", response_model=MeOut)
async def login(body: LoginIn, request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")):
    user, token = await auth.login(s, await accounts.username_for_login(s, body.username), body.password)
    auth.set_session_cookie(request, response, token)
    return me_out(user)


@router.post("/register", response_model=MeOut)
async def register(body: RegisterIn, request: Request, response: Response,
                   s: AsyncSession = Depends(get_session, scope="function")):
    user, token = await accounts.register(s, body.code, body.username, body.password)
    auth.set_session_cookie(request, response, token)
    return me_out(user)


@router.post("/email/code", response_model=EmailCodeOut)
async def email_code(body: EmailCodeIn):
    return EmailCodeOut(resend_in=await accounts.send_email_code(body.email))


@router.post("/email/register", response_model=MeOut)
async def email_register(body: EmailRegisterIn, request: Request, response: Response,
                         s: AsyncSession = Depends(get_session, scope="function")):
    user, token = await accounts.register_by_email(s, body.email, body.code, body.username, body.password)
    auth.set_session_cookie(request, response, token)
    return me_out(user)


@router.post("/google", response_model=GoogleLoginOut)
async def google(body: GoogleIn, request: Request, response: Response,
                 s: AsyncSession = Depends(get_session, scope="function")):
    user, token, password = await accounts.google_login(s, body.id_token, body.code, body.username)
    auth.set_session_cookie(request, response, token)
    return GoogleLoginOut(**me_out(user).model_dump(), password=password)


@router.post("/google/link", response_model=MeOut)
async def google_link(body: GoogleLinkIn, user: User = Depends(auth.current_user),
                      s: AsyncSession = Depends(get_session, scope="function")):
    return me_out(await accounts.link_google(s, user, body.id_token))


@router.delete("/google/link", response_model=MeOut)
async def google_unlink(user: User = Depends(auth.current_user), s: AsyncSession = Depends(get_session, scope="function")):
    user.google_sub = user.google_email = None
    return me_out(user)


@router.post("/logout")
async def logout(request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")):
    await auth.logout(s, request, response)
    return {"ok": True}


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(auth.current_user)):
    return me_out(user)
