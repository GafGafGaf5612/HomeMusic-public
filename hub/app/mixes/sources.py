"""Sources of «similar» tracks for mixes (MIXES.md 4.1): YouTube Music radio, Last.fm, Yandex Music.

Deezer's radio, related artists and releases are `DeezerClient` methods (they answer with catalog tracks already);
everything here answers with names (`Hit`) that `app.mixes.resolve` matches with the catalog. Answers are kept in
`catalog_cache` for a week (`TTL_S`), except the personal «Моя волна» of Yandex.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.catalog.cache import cached
from app.config import get_settings
from app.net import RateLimiter, make_client, request_with_backoff
from app.providers.ytdlp import (
    YTM_API_URL,
    YTM_SONGS_PARAMS,
    _parse_duration,
    _ytm_items,
    ytm_client_version,
    ytm_item_to_candidate,
)

log = logging.getLogger(__name__)

TTL_S = 7 * 86400
YTM_NEXT_URL = "https://music.youtube.com/youtubei/v1/next?prettyPrint=false"
LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_RATE = 5.0  # requests per second (Last.fm's terms)


class SourceDown(Exception):
    """The source did not answer (not cached: asked again next time)."""


@dataclass
class Hit:
    """A track a source suggests, by name. `source_id`: its id there (YouTube video, Yandex track) — the key of
    what it turned out to be in the catalog (`source_map`)."""

    artist: str
    title: str
    duration_s: int | None = None
    source_id: str | None = None

    @classmethod
    def of(cls, d: dict[str, Any]) -> Hit:
        return cls(**{k: d.get(k) for k in ("artist", "title", "duration_s", "source_id")})


def _key(*parts: Any) -> str:
    raw = "|".join(" ".join(str(p).split()).casefold() for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest() if len(raw) > 200 else raw


async def _cached_hits(key: str, produce) -> list[Hit]:
    async def run() -> list[dict[str, Any]]:
        return [asdict(h) for h in await produce()]

    return [Hit.of(x) for x in await cached(key, TTL_S, run) or []]


# --- YouTube Music radio ---------------------------------------------------------------------------------------


def _ytm_headers(version: str) -> dict[str, str]:
    return {"X-YouTube-Client-Name": "67", "X-YouTube-Client-Version": version, "Origin": "https://music.youtube.com"}


def _runs_text(o: dict | None) -> str:
    return "".join(r.get("text", "") for r in (o or {}).get("runs") or []).strip()


def radio_rows(data: Any) -> list[dict]:
    """The queue rows (`playlistPanelVideoRenderer`) of a `next` response, in order."""
    out: list[dict] = []
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if isinstance(row := cur.get("playlistPanelVideoRenderer"), dict):
                out.append(row)
                continue
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return out


def radio_hit(row: dict) -> Hit | None:
    """«title» + «artists • album • year» (a song) or «channel • views • likes» (a video)."""
    vid, title = row.get("videoId"), _runs_text(row.get("title"))
    runs = (row.get("longBylineText") or {}).get("runs") or []
    if not vid or not title or not runs:
        return None
    artists = [r["text"] for r in runs if (((r.get("navigationEndpoint") or {}).get("browseEndpoint") or {})
                                           .get("browseEndpointContextSupportedConfigs") or {})
               .get("browseEndpointContextMusicConfig", {}).get("pageType") == "MUSIC_PAGE_TYPE_ARTIST"]
    artist = ", ".join(artists) or "".join(r.get("text", "") for r in runs).split(" • ")[0].strip()
    length = _runs_text(row.get("lengthText"))
    return Hit(artist=artist, title=title, duration_s=_parse_duration(length) if length else None, source_id=vid)


async def _ytm_video_id(c: httpx.AsyncClient, artist: str, title: str) -> str | None:
    version = ytm_client_version()
    r = await c.post(YTM_API_URL, headers=_ytm_headers(version), json={
        "context": {"client": {"clientName": "WEB_REMIX", "clientVersion": version, "hl": "en"}},
        "query": f"{artist} - {title}", "params": YTM_SONGS_PARAMS})
    r.raise_for_status()
    from app.matching.score import score

    best: tuple[float, str | None] = (0.0, None)
    for item in _ytm_items(r.json().get("contents") or {})[:5]:
        if (cand := ytm_item_to_candidate(item)) is not None:
            sc = score(artist, title, cand.artist, cand.title).total
            if sc > best[0]:
                best = (sc, cand.provider_id)
    return best[1] if best[0] >= get_settings().cfg.matching.confirm_threshold else None


async def ytm_radio(artist: str, title: str) -> list[Hit]:
    """YouTube Music's radio of a track (`RDAMVM<videoId>`): ~50 tracks, the seed itself left out."""

    async def produce() -> list[Hit]:
        try:
            async with make_client(timeout=20) as c:
                vid = await _ytm_video_id(c, artist, title)
                if vid is None:
                    return []
                version = ytm_client_version()
                r = await c.post(YTM_NEXT_URL, headers=_ytm_headers(version), json={
                    "context": {"client": {"clientName": "WEB_REMIX", "clientVersion": version, "hl": "en"}},
                    "videoId": vid, "playlistId": f"RDAMVM{vid}", "isAudioOnly": True,
                    "enablePersistentPlaylistPanel": True})
                r.raise_for_status()
                rows = radio_rows(r.json())
        except (httpx.HTTPError, ValueError) as e:
            raise SourceDown(f"YouTube Music: {e!r}") from e
        return [h for row in rows if (h := radio_hit(row)) is not None and h.source_id != vid]

    return await _cached_hits(f"mix:ytm_radio:{_key(artist, title)}", produce)


# --- Last.fm -----------------------------------------------------------------------------------------------------

_lastfm_limiter: RateLimiter | None = None


def lastfm_configured() -> bool:
    return bool(get_settings().lastfm_api_key.strip())


async def _lastfm(method: str, **params: Any) -> dict[str, Any]:
    """One call; an unknown artist/track (`error` 6) is an empty answer, other errors are `SourceDown`."""
    global _lastfm_limiter
    if _lastfm_limiter is None:
        _lastfm_limiter = RateLimiter(LASTFM_RATE)
    q = {"method": method, "api_key": get_settings().lastfm_api_key.strip(), "format": "json", "autocorrect": 1,
         **params}
    try:
        async with make_client(timeout=20) as c:
            r = await request_with_backoff(c, "GET", LASTFM_URL, limiter=_lastfm_limiter, params=q, retries=2)
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SourceDown(f"Last.fm: {e!r}") from e
    if isinstance(body, dict) and body.get("error"):
        if body["error"] == 6:  # not found
            return {}
        raise SourceDown(f"Last.fm {method}: {body.get('message')} ({body['error']})")
    return body if isinstance(body, dict) else {}


def _as_list(v: Any) -> list[dict]:
    return v if isinstance(v, list) else [v] if isinstance(v, dict) else []


async def lastfm_similar_tracks(artist: str, title: str, limit: int = 50) -> list[Hit]:
    async def produce() -> list[Hit]:
        body = await _lastfm("track.getSimilar", artist=artist, track=title, limit=limit)
        return [Hit(artist=(t.get("artist") or {}).get("name") or "", title=t.get("name") or "",
                    duration_s=int(t["duration"]) if str(t.get("duration") or "").isdigit() and int(t["duration"])
                    else None)
                for t in _as_list((body.get("similartracks") or {}).get("track")) if t.get("name")]

    return await _cached_hits(f"mix:lfm_track_sim:{_key(artist, title)}", produce)


async def lastfm_similar_artists(artist: str, limit: int = 20) -> list[str]:
    async def produce() -> list[str]:
        body = await _lastfm("artist.getSimilar", artist=artist, limit=limit)
        return [a["name"] for a in _as_list((body.get("similarartists") or {}).get("artist")) if a.get("name")]

    return await cached(f"mix:lfm_artist_sim:{_key(artist)}", TTL_S, produce) or []


async def lastfm_artist_tags(artist: str, limit: int = 5) -> list[str]:
    """The artist's top tags (genres, moods): what a mix name is made of."""

    async def produce() -> list[str]:
        body = await _lastfm("artist.getTopTags", artist=artist)
        return [t["name"] for t in _as_list((body.get("toptags") or {}).get("tag"))[:limit] if t.get("name")]

    return await cached(f"mix:lfm_artist_tags:{_key(artist)}", TTL_S, produce) or []


# --- Yandex Music ------------------------------------------------------------------------------------------------


def _yandex_rows(tracks: Any) -> list[Hit]:
    from app.catalog.yandex import yandex_track_row

    out: list[Hit] = []
    for t in tracks or []:
        if (row := yandex_track_row(t)) is not None:
            artists = row.get("artists") or [row["artist"]]
            out.append(Hit(artist=artists[0] if artists else row["artist"], title=row["title"],
                           duration_s=row.get("duration_s"), source_id=str(getattr(t, "id", "") or "") or None))
    return out


def _yandex_find(artist: str, title: str, duration_s: int | None) -> str | None:
    from app.catalog.yandex import yandex_client
    from app.matching.score import score

    res = yandex_client().search(f"{artist} {title}", type_="track")
    tracks = res.tracks.results[:5] if res is not None and res.tracks is not None else []
    best: tuple[float, str | None] = (0.0, None)
    for t, h in zip(tracks, _yandex_rows(tracks), strict=False):
        sc = score(artist, title, h.artist, h.title, query_duration=duration_s, cand_duration=h.duration_s).total
        if sc > best[0]:
            best = (sc, str(t.id))
    return best[1] if best[0] >= get_settings().cfg.matching.auto_threshold else None


def _yandex_similar_sync(artist: str, title: str, duration_s: int | None) -> list[Hit]:
    from app.catalog.yandex import yandex_client

    track_id = _yandex_find(artist, title, duration_s)
    if track_id is None:
        return []
    sim = yandex_client().tracks_similar(track_id)
    return _yandex_rows(sim.similar_tracks if sim is not None else [])


async def yandex_similar(artist: str, title: str, duration_s: int | None = None) -> list[Hit]:
    """Yandex's «similar» to a track (no token): goes into little-known artists — the «Незнакомое» generator."""

    async def produce() -> list[Hit]:
        try:
            return await asyncio.to_thread(_yandex_similar_sync, artist, title, duration_s)
        except Exception as e:
            raise SourceDown(f"Yandex: {e!r}") from e

    return await _cached_hits(f"mix:ym_similar:{_key(artist, title)}", produce)


def _yandex_wave_sync(token: str, rounds: int) -> list[Hit]:
    from app.catalog.yandex import yandex_client

    client = yandex_client(token)
    out: list[Hit] = []
    seen: set[str] = set()
    last: str | None = None
    for _ in range(rounds):
        res = client.rotor_station_tracks("user:onyourwave", queue=last)
        tracks = [x.track for x in (res.sequence if res is not None else []) if getattr(x, "track", None)]
        fresh = [h for h in _yandex_rows(tracks) if h.source_id not in seen]
        if not fresh:
            break
        out += fresh
        seen.update(h.source_id for h in fresh if h.source_id)
        last = fresh[-1].source_id
    return out


async def yandex_wave(token: str, rounds: int = 6) -> list[Hit]:
    """«Моя волна» of the Yandex account an import signed in with (M7): personal, never cached. Yandex may refuse it
    (no Plus, a region): `SourceDown`, the generator stays off."""
    try:
        return await asyncio.to_thread(_yandex_wave_sync, token, rounds)
    except Exception as e:
        raise SourceDown(f"Yandex wave: {e!r}") from e
