"""Yandex Music: the user's collection — «Мне нравится», playlists (private ones too), liked albums and artists,
dislikes.

Three ways in:
- sign-in by code (`device_start` → the user enters the code on ya.ru/device → `device_poll` gives a token). Yandex
  has no public API for music: the sign-in is made as the official Yandex Music app (its public client id, as
  `yandex-music` by MarshalX documents it). No Plus subscription is needed: only lists are read, never audio;
- a token pasted by hand (the same token, got elsewhere);
- an open profile without signing in (`music.yandex.ru/users/<login>/…`): likes of tracks, albums and artists and
  public playlists; no private playlists, no dislikes.

The `yandex-music` client is synchronous (its async one needs aiohttp): `fetch_snapshot` runs in a worker thread.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.catalog.yandex import yandex_client, yandex_track_row
from app.imports.sources.base import AuthPending, SnapCollection, SnapItem, Snapshot, SourceError
from app.net import make_client

OAUTH = "https://oauth.yandex.ru"
# the Yandex Music app's own OAuth client (public: shipped in the app, documented by MarshalX/yandex-music)
CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
CLIENT_SECRET = "53bc75238f0c4d08a118e51fe9203300"
LIKES_KIND = 3  # the playlist kind of «Мне нравится»
TRACKS_CHUNK = 200
MAX_DISLIKES = 2000
_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HOST_RE = re.compile(r"^(?:www\.|m\.)?music\.yandex\.[a-z]{2,3}$")


# --- sign-in by code -------------------------------------------------------------------------------


async def device_start(device_id: str) -> dict[str, Any]:
    """Asks Yandex for a sign-in code: {device_code, user_code, verification_url, interval, expires_in}."""
    try:
        async with make_client(http2=False) as c:
            r = await c.post(f"{OAUTH}/device/code",
                             data={"client_id": CLIENT_ID, "device_id": device_id, "device_name": "HomeMusic"})
    except Exception as e:
        raise SourceError(f"Яндекс не ответил: {e}") from e
    if r.status_code != 200 or "device_code" not in r.text:
        raise SourceError(f"Яндекс не выдал код входа (HTTP {r.status_code})")
    return r.json()


async def device_poll(device_code: str) -> str:
    """The token once the user has confirmed the code; AuthPending until then."""
    try:
        async with make_client(http2=False) as c:
            r = await c.post(f"{OAUTH}/token", data={"grant_type": "device_code", "code": device_code,
                                                     "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        body = r.json()
    except Exception as e:
        raise AuthPending(f"yandex token poll failed: {e!r}") from e  # network trouble: ask again later
    if token := body.get("access_token"):
        return token
    err = body.get("error")
    if err in ("authorization_pending", "slow_down"):
        raise AuthPending(err)
    if err == "expired_token":
        raise SourceError("Код входа устарел — получите новый")
    if err == "access_denied":
        raise SourceError("Вход отклонён")
    raise SourceError(f"Яндекс не пустил: {body.get('error_description') or err or r.status_code}")


# --- profile links ---------------------------------------------------------------------------------


def profile_login(text: str) -> str:
    """`music.yandex.ru/users/<login>/playlists/3` (any page of the profile) or a bare login → the login."""
    raw = (text or "").strip()
    if _LOGIN_RE.match(raw) and "yandex." not in raw.lower():
        return raw
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    parts = [x for x in p.path.split("/") if x]
    if _HOST_RE.match((p.hostname or "").lower()) and len(parts) >= 2 and parts[0] == "users":
        if _LOGIN_RE.match(parts[1]):
            return parts[1]
    raise SourceError("Нужна ссылка на профиль: music.yandex.ru/users/<логин>/playlists/3")


# --- the collection --------------------------------------------------------------------------------


def track_raw(t: Any, *, liked_at: str | None = None) -> dict[str, Any] | None:
    """yandex_music.Track → raw import item (None for podcasts and rows without a title)."""
    row = yandex_track_row(t)
    if row is None:
        return None
    albums = getattr(t, "albums", None) or []
    raw: dict[str, Any] = {
        "artist": row["artist"],
        "artists": row["artists"],
        "title": row["title"],
        "album": albums[0].title if albums and getattr(albums[0], "title", None) else None,
        "duration_s": row["duration_s"],
    }
    if cover := getattr(t, "cover_uri", None):
        raw["cover_url"] = "https://" + cover.replace("%%", "200x200")
    if liked_at:
        raw["liked_at"] = liked_at
    if getattr(t, "available", True) is False:
        raw["unavailable"] = True  # gone from Yandex: its names are still good for the search
    return {k: v for k, v in raw.items() if v is not None}


def _track_items(client: Any, shorts: list[Any], *, liked: bool = False) -> list[SnapItem]:
    """TrackShort rows → items; rows that came without the full track are fetched in chunks."""
    missing = [s.track_id for s in shorts if getattr(s, "track", None) is None]
    full: dict[str, Any] = {}
    for i in range(0, len(missing), TRACKS_CHUNK):
        for t in client.tracks(missing[i:i + TRACKS_CHUNK]) or []:
            full[str(t.id)] = t
    items: list[SnapItem] = []
    seen: set[str] = set()
    for s in shorts:
        tid = str(s.id).split(":")[0]
        t = getattr(s, "track", None) or full.get(tid)
        if tid in seen or t is None:
            continue
        ts = str(s.timestamp)[:19] if liked and getattr(s, "timestamp", None) else None
        if raw := track_raw(t, liked_at=ts):
            seen.add(tid)
            items.append(SnapItem(external_id=tid, raw=raw))
    return items


def _cover(obj: Any) -> str | None:
    uri = getattr(obj, "cover_uri", None) or getattr(getattr(obj, "cover", None), "uri", None)
    return "https://" + uri.replace("%%", "200x200") if uri else None


def _names(artists: Any) -> str:
    return ", ".join(a.name for a in artists or [] if getattr(a, "name", None))


def fetch_snapshot(client: Any, owner: str | int | None, *, signed_in: bool) -> Snapshot:
    """Everything of `owner` (None: the signed-in user) that the client may read, in Yandex's order."""
    from yandex_music.exceptions import NotFoundError, UnauthorizedError, YandexMusicError

    account: str | None = str(owner) if owner is not None else None
    try:
        if signed_in:
            acc = client.me.account if client.me else None
            account = (acc.login or acc.display_name) if acc else account
        snap = Snapshot(account=account)
        likes = client.users_likes_tracks(owner)
        # newest first, as in the app
        snap.collections.append(SnapCollection(
            kind="likes", external_id="likes", title="Мне нравится",
            items=_track_items(client, list(likes.tracks or []) if likes else [], liked=True)))
        for pl in client.users_playlists_list(owner) or []:
            if pl.kind == LIKES_KIND or not pl.track_count:
                continue
            full = client.users_playlists(pl.kind, pl.owner.uid if pl.owner else owner)
            items = _track_items(client, list(full.tracks or [])) if full else []
            if items:
                snap.collections.append(SnapCollection(kind="playlist", external_id=f"{pl.uid or owner}:{pl.kind}",
                                                       title=pl.title or "Плейлист", items=items,
                                                       cover_url=_cover(pl)))
        albums = [lk.album for lk in client.users_likes_albums(owner) or [] if getattr(lk, "album", None)]
        if albums:
            snap.collections.append(SnapCollection(kind="albums", external_id="albums", title="Альбомы", items=[
                SnapItem(external_id=str(a.id), raw={k: v for k, v in {
                    "artist": _names(a.artists), "title": a.title, "year": a.year, "track_count": a.track_count,
                    "cover_url": _cover(a)}.items() if v is not None})
                for a in albums if a.title]))
        artists = [lk.artist for lk in client.users_likes_artists(owner) or [] if getattr(lk, "artist", None)]
        if artists:
            snap.collections.append(SnapCollection(kind="artists", external_id="artists", title="Исполнители", items=[
                SnapItem(external_id=str(a.id), raw={k: v for k, v in {"name": a.name, "cover_url": _cover(a)}.items()
                                                     if v is not None})
                for a in artists if a.name]))
        if signed_in:
            dis = client.users_dislikes_tracks(owner)
            shorts = list(dis.tracks or [])[:MAX_DISLIKES] if dis else []
            if shorts:
                snap.collections.append(SnapCollection(kind="dislikes", external_id="dislikes", title="Не нравится",
                                                       items=_track_items(client, shorts)))
        return snap
    except UnauthorizedError as e:
        if signed_in:
            raise SourceError("Яндекс не принял вход — войдите заново") from e
        raise SourceError("Медиатека закрыта: откройте её в настройках приватности Яндекс Музыки "
                          "или войдите через Яндекс") from e
    except NotFoundError as e:
        raise SourceError("Профиль не найден в Яндекс Музыке") from e
    except YandexMusicError as e:
        raise SourceError(f"Яндекс Музыка не ответила: {e}") from e


def fetch(token: str | None, login: str | None) -> Snapshot:
    """Sync (run in a thread): signed in with `token`, or the open profile `login`."""
    from yandex_music.exceptions import UnauthorizedError, YandexMusicError

    if token:
        try:
            client = yandex_client(token).init()
        except UnauthorizedError as e:
            raise SourceError("Токен не подошёл или устарел — войдите заново") from e
        except YandexMusicError as e:
            raise SourceError(f"Яндекс Музыка не ответила: {e}") from e
        return fetch_snapshot(client, None, signed_in=True)
    if not login:
        raise SourceError("Нет ни входа, ни профиля")
    return fetch_snapshot(yandex_client(), login, signed_in=False)
