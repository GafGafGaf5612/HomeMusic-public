"""Letters from the Hub over SMTP of any mailbox (registration codes). Gmail with an app password is free and sends
about 500 letters a day; README, «Регистрация по email»."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 30


class MailError(Exception):
    pass


def configured() -> bool:
    return bool(get_settings().smtp_host)


def _deliver(msg: EmailMessage) -> None:
    st = get_settings()
    ctx = ssl.create_default_context()
    smtp = (smtplib.SMTP_SSL(st.smtp_host, st.smtp_port, timeout=TIMEOUT_S, context=ctx) if st.smtp_port == 465
            else smtplib.SMTP(st.smtp_host, st.smtp_port, timeout=TIMEOUT_S))
    with smtp:
        if st.smtp_port != 465:
            smtp.starttls(context=ctx)
        if st.smtp_user:
            smtp.login(st.smtp_user, st.smtp_password)
        smtp.send_message(msg)


async def send(to: str, subject: str, text: str) -> None:
    st = get_settings()
    sender = st.smtp_from or st.smtp_user
    msg = EmailMessage()
    msg["From"] = formataddr(("HomeMusic", sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    msg.set_content(text)
    try:
        await asyncio.to_thread(_deliver, msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("letter to %s not sent: %s", to, e)
        raise MailError(str(e)) from e
