"""Secrets at rest (Fernet) and session tokens."""

from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().hub_secret_key
    if not key:
        raise RuntimeError("HUB_SECRET_KEY is not set")
    return Fernet(key.encode())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("cannot decrypt stored secret (HUB_SECRET_KEY changed?)") from e


def new_token() -> tuple[str, str]:
    """Returns (token for the cookie, sha256 hex stored in the DB)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()
