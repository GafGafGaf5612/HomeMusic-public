"""Minimal MusicBrainz client (MBIDs): recording and artist search. 1 request/s, mandatory User-Agent."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

import httpx

from app.catalog.cache import cached
from app.catalog.models import CatalogArtist, CatalogTrack, make_ref
from app.config import CatalogCfg
from app.net import RateLimiter, make_client, request_with_backoff

BASE_URL = "https://musicbrainz.org/ws/2"
TTL_S = 86400
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


class MusicBrainzError(Exception):
    """MusicBrainz returned an unexpected response."""


def _esc(s: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", s.strip())


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:  # "1988" / "1988-01" are too coarse for a date
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _key(prefix: str, *parts: Any) -> str:
    raw = "|".join(" ".join(str(p).split()).casefold() for p in parts)
    if len(raw) > 200:
        raw = hashlib.sha1(raw.encode()).hexdigest()
    return f"{prefix}:{raw}"


def _credit_names(credits: list[dict[str, Any]]) -> list[str]:
    return [c.get("name") or (c.get("artist") or {}).get("name") or "" for c in credits if c]


def map_recording(r: dict[str, Any]) -> CatalogTrack:
    credits = r.get("artist-credit") or []
    names = [n for n in _credit_names(credits) if n]
    first_artist = (credits[0].get("artist") or {}) if credits else {}
    releases = r.get("releases") or []
    rel = releases[0] if releases else {}
    rel_names = [n for n in _credit_names(rel.get("artist-credit") or []) if n]
    track_no = disc_no = None
    media = rel.get("media") or []
    if media:
        disc_no = media[0].get("position")
        tr = (media[0].get("track") or [{}])[0]
        num = str(tr.get("number") or "")
        track_no = int(num) if num.isdigit() else None
    length = r.get("length")
    isrcs = r.get("isrcs") or []
    return CatalogTrack(
        ref=make_ref("mb", r["id"]),
        source="mb",
        id=r["id"],
        title=r.get("title") or "",
        artist=names[0] if names else "",
        artists=names,
        artist_ref=make_ref("mb", first_artist["id"]) if first_artist.get("id") else None,
        album_ref=make_ref("mb", rel["id"]) if rel.get("id") else None,
        album_title=rel.get("title"),
        album_artist=rel_names[0] if rel_names else None,
        duration_s=round(length / 1000) if isinstance(length, int | float) else None,
        track_no=track_no,
        disc_no=disc_no,
        isrc=isrcs[0] if isrcs else None,
        release_date=_parse_date(r.get("first-release-date")) or _parse_date(rel.get("date")),
    )


def map_mb_artist(a: dict[str, Any]) -> CatalogArtist:
    return CatalogArtist(ref=make_ref("mb", a["id"]), source="mb", id=a["id"], name=a.get("name") or "")


class MusicBrainzClient:
    """Async MusicBrainz search client; results cached for one day."""

    def __init__(self, cfg: CatalogCfg, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._own_client = client is None
        self._client = client or make_client(
            headers={"User-Agent": cfg.musicbrainz_user_agent, "Accept": "application/json"}
        )
        if client is not None:
            self._client.headers["User-Agent"] = cfg.musicbrainz_user_agent
        self._limiter = RateLimiter(1.0)

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def _search(self, entity: str, query: str, limit: int) -> dict[str, Any]:
        resp = await request_with_backoff(
            self._client,
            "GET",
            f"{BASE_URL}/{entity}",
            limiter=self._limiter,
            params={"query": query, "fmt": "json", "limit": limit},
        )
        if resp.status_code != 200:
            raise MusicBrainzError(f"MB {entity} search -> HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as e:
            raise MusicBrainzError(f"MB {entity} search: invalid JSON") from e

    async def search_recordings(self, artist: str, title: str, limit: int = 5) -> list[CatalogTrack]:
        """Recordings matching title + artist (best score first)."""

        async def produce() -> list[dict[str, Any]]:
            query = f'recording:"{_esc(title)}" AND artist:"{_esc(artist)}"'
            data = await self._search("recording", query, limit)
            return [map_recording(r).model_dump(mode="json") for r in data.get("recordings") or []]

        rows = await cached(_key("mb:recordings", limit, artist, title), TTL_S, produce)
        return [CatalogTrack.model_validate(x) for x in rows]

    async def search_artists(self, q: str, limit: int = 5) -> list[CatalogArtist]:
        async def produce() -> list[dict[str, Any]]:
            data = await self._search("artist", _esc(q), limit)
            return [map_mb_artist(a).model_dump(mode="json") for a in data.get("artists") or []]

        rows = await cached(_key("mb:artists", limit, q), TTL_S, produce)
        return [CatalogArtist.model_validate(x) for x in rows]
