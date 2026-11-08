"""Verification of Google ID tokens (Sign in with Google in the Android app, Credential Manager).

The app gets a token for the Hub's Web client id and sends it here; the Hub checks Google's RS256 signature
against the published keys, the audience and the expiry. No client secret is involved.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import get_settings

CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
SKEW_S = 120

_keys: dict[str, rsa.RSAPublicKey] = {}
_keys_until: float = 0


class GoogleTokenError(Exception):
    pass


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _int(part: str) -> int:
    return int.from_bytes(_b64(part), "big")


async def _fetch_keys(client: httpx.AsyncClient | None = None) -> None:
    global _keys, _keys_until
    own = client is None
    if client is None:
        s = get_settings()
        proxy = s.outbound_proxy if s.outbound_proxy and httpx.URL(CERTS_URL).host in s.proxy_hosts else None
        client = httpx.AsyncClient(timeout=15, proxy=proxy)
    try:
        r = await client.get(CERTS_URL)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise GoogleTokenError(f"Google keys are unavailable: {e}") from e
    finally:
        if own:
            await client.aclose()
    keys = {}
    for k in r.json().get("keys", []):
        if k.get("kty") == "RSA" and k.get("kid"):
            keys[k["kid"]] = rsa.RSAPublicNumbers(_int(k["e"]), _int(k["n"])).public_key()
    m = re.search(r"max-age=(\d+)", r.headers.get("cache-control", ""))
    _keys, _keys_until = keys, time.time() + (int(m.group(1)) if m else 3600)


async def _key(kid: str) -> rsa.RSAPublicKey:
    if kid not in _keys or time.time() > _keys_until:
        await _fetch_keys()
    key = _keys.get(kid)
    if key is None:
        raise GoogleTokenError("unknown signing key")
    return key


async def verify_id_token(token: str) -> dict[str, Any]:
    """Claims of a valid token issued for our client id with a verified email; GoogleTokenError otherwise."""
    client_id = get_settings().google_client_id
    if not client_id:
        raise GoogleTokenError("Google sign-in is not configured")
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
        signature = _b64(sig_b64)
    except (ValueError, json.JSONDecodeError) as e:
        raise GoogleTokenError("malformed token") from e
    if header.get("alg") != "RS256":
        raise GoogleTokenError("unexpected algorithm")
    key = await _key(str(header.get("kid")))
    try:
        key.verify(signature, f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise GoogleTokenError("bad signature") from e
    now = time.time()
    if claims.get("iss") not in ISSUERS:
        raise GoogleTokenError("wrong issuer")
    aud = claims.get("aud")
    if aud != client_id and not (isinstance(aud, list) and client_id in aud):
        raise GoogleTokenError("token is for another client")
    if float(claims.get("exp", 0)) < now - SKEW_S or float(claims.get("iat", now)) > now + SKEW_S:
        raise GoogleTokenError("token expired")
    if not claims.get("sub"):
        raise GoogleTokenError("no subject")
    if claims.get("email") and claims.get("email_verified") not in (True, "true"):
        raise GoogleTokenError("email is not verified")
    return claims
