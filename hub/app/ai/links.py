"""Track lists from links (PLAN.md 8.5.2) — exact data, no LLM.

- Yandex Music: playlists (old `/users/<owner>/playlists/<kind>` and new `/playlists/<uuid|lk.uuid|ar.uuid|…>`),
  albums and single tracks via `yandex-music` (MarshalX) WITHOUT a token. The sync client runs in a worker thread
  (the async client needs aiohttp, which is not installed). The old web handlers (`/handlers/playlist.jsx`) are gone
  (they redirect to the home page), so there is no web fallback.
- YouTube / YouTube Music: playlists, albums (OLAK5uy_… / browse/MPREb_…) and single videos via yt-dlp flat extraction.
- Spotify: playlists, albums and tracks from the public embed page (`__NEXT_DATA__` JSON) — best effort.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from app.ai.extract import ExtractedItem, Extraction, ExtractionContext
from app.net import make_client, proxy_for_domains, request_with_backoff
from app.providers.ytdlp import YOUTUBE_PROXY_DOMAINS

log = logging.getLogger(__name__)

YANDEX_API_HOST = "api.music.yandex.net"
YANDEX_TIMEOUT_S = 20
YANDEX_TRACKS_CHUNK = 200
_YANDEX_HOST_RE = re.compile(r"^(?:www\.|m\.)?music\.yandex\.[a-z]{2,3}$")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_YANDEX_PLAYLIST_ID_RE = re.compile(rf"^(?:[a-z]{{2}}\.)?{_UUID}$", re.I)
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
_SPOTIFY_RE = re.compile(r"^/(?:intl-[a-z-]+/)?(?:embed/)?(playlist|album|track)/([A-Za-z0-9]{10,})", re.I)
_SPOTIFY_URI_RE = re.compile(r"^spotify:(playlist|album|track):([A-Za-z0-9]{10,})$")
_NEXT_DATA_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_URL_RE = re.compile(r"(?:https?://|spotify:)\S+", re.I)

_YT_CHANNEL_SUFFIX_RE = re.compile(r"\s*-\s*(?:Topic|Тема)\s*$", re.I)
_YT_TRACK_NO_RE = re.compile(r"^\s*(?:\d{1,3}(?:[-.]\d{1,3})?\s*[.)]\s*|\d{1,3}\s+-\s+)")
_YT_JUNK_RE = re.compile(
    r"\s*[\(\[](?:official\s*)?(?:music\s*|lyric\s*|lyrics\s*|hd\s*|hq\s*|4k\s*)*"
    r"(?:video|audio|clip|visualizer|lyrics?|видео|клип|аудио|премьера[^\)\]]*|official)"
    r"(?:\s*(?:hd|hq|4k))?[\)\]]",
    re.I,
)
_DASH_SPLIT_RE = re.compile(r"^(?P<artist>.+?)\s+[-–—]\s+(?P<title>.+)$")


class UnsupportedLink(Exception):
    """The link is not a supported playlist/album/track URL (message is user-facing, Russian)."""

    def __init__(self, message: str = "Ссылка не поддерживается") -> None:
        super().__init__(message)


class LinkFetchError(UnsupportedLink):
    """The link is supported, but the data could not be fetched (private playlist, network error, …)."""


# --------------------------------------------------------------------------- helpers


def find_url(text: str) -> str | None:
    """First URL (or spotify: URI) in a pasted text, without trailing punctuation."""
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;:!?)»\"'") if m else None


def fmt_duration(seconds: float | int | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _join_names(names: list[str | None]) -> str:
    return ", ".join(n.strip() for n in names if n and n.strip())


# --------------------------------------------------------------------------- dispatch


async def extract_from_link(url: str) -> Extraction:
    """Read the track list behind a link. Raises UnsupportedLink (or its subclass LinkFetchError)."""
    raw = find_url(url) or (url or "").strip()
    if m := _SPOTIFY_URI_RE.match(raw):
        return await _spotify(m.group(1).lower(), m.group(2))
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    host = (p.hostname or "").lower()
    if _YANDEX_HOST_RE.match(host):
        return await _yandex(p.path)
    if host in _YT_HOSTS:
        return await _youtube(raw, p)
    if host in {"open.spotify.com", "play.spotify.com"} and (m := _SPOTIFY_RE.match(p.path)):
        return await _spotify(m.group(1).lower(), m.group(2))
    if host in {"spotify.link", "spotify.app.link"}:
        return await _spotify_short(raw)
    raise UnsupportedLink()


def parse_yandex_path(path: str) -> tuple[str, tuple[str, ...]]:
    """Yandex Music URL path → (kind, ids): ("user_playlist", (owner, kind)), ("playlist", (uuid,)),
    ("album", (album_id,)), ("track", (track_id,)). Raises UnsupportedLink."""
    parts = [x for x in path.split("/") if x]
    if len(parts) >= 4 and parts[0] == "users" and parts[2] == "playlists" and parts[3].isdigit():
        return "user_playlist", (parts[1], parts[3])
    if len(parts) >= 2 and parts[0] == "playlists" and _YANDEX_PLAYLIST_ID_RE.match(parts[1]):
        return "playlist", (parts[1],)
    if len(parts) >= 4 and parts[0] == "album" and parts[2] == "track" and parts[3].isdigit():
        return "track", (parts[3],)
    if len(parts) >= 2 and parts[0] == "album" and parts[1].isdigit():
        return "album", (parts[1],)
    if len(parts) >= 2 and parts[0] == "track" and parts[1].isdigit():
        return "track", (parts[1],)
    raise UnsupportedLink("Ссылка Яндекс Музыки не поддерживается: нужен плейлист, альбом или трек")


# --------------------------------------------------------------------------- Yandex Music


def _yandex_client() -> Any:
    from app.catalog.yandex import yandex_client

    return yandex_client()


def yandex_track_item(track: Any, *, album_title: str | None = None) -> ExtractedItem | None:
    """yandex_music.Track → ExtractedItem (None for podcasts/unavailable rows without a title)."""
    if track is None or not getattr(track, "title", None):
        return None
    ttype = getattr(track, "type", None)
    if ttype and ttype not in ("music", "asmr"):
        return None
    title = track.title.strip()
    if version := (getattr(track, "version", None) or "").strip():
        title = f"{title} ({version})"
    albums = getattr(track, "albums", None) or []
    album = album_title or (albums[0].title if albums and getattr(albums[0], "title", None) else None)
    ms = getattr(track, "duration_ms", None)
    return ExtractedItem(
        artist_raw=_join_names([a.name for a in (track.artists or [])]),
        title_raw=title,
        album_raw=album,
        duration=fmt_duration(ms / 1000 if ms else None),
        conf=1.0,
    )


def yandex_album_extraction(album: Any) -> Extraction:
    artist = _join_names([a.name for a in (album.artists or [])])
    title = album.title.strip() if album.title else None
    if title and (version := (getattr(album, "version", None) or "").strip()):
        title = f"{title} ({version})"
    items = [it for vol in (album.volumes or []) for t in vol if (it := yandex_track_item(t, album_title=title))]
    return Extraction(
        source_app="yandex_music",
        page_type="album",
        context=ExtractionContext(album_title=title, album_artist=artist or None),
        items=items,
    )


def yandex_playlist_extraction(playlist: Any, extra_tracks: dict[str, Any] | None = None) -> Extraction:
    """yandex_music.Playlist → Extraction; `extra_tracks` fills TrackShort rows that came without `.track`."""
    extra_tracks = extra_tracks or {}
    items: list[ExtractedItem] = []
    for short in playlist.tracks or []:
        track = short.track or extra_tracks.get(str(short.id).split(":")[0])
        if it := yandex_track_item(track):
            items.append(it)
    return Extraction(
        source_app="yandex_music",
        page_type="playlist",
        context=ExtractionContext(playlist_name=playlist.title),
        items=items,
    )


def _yandex_fetch(kind: str, ids: tuple[str, ...]) -> Extraction:
    from yandex_music.exceptions import (
        BadRequestError,
        NotFoundError,
        TimedOutError,
        UnauthorizedError,
        YandexMusicError,
    )

    client = _yandex_client()
    try:
        if kind == "album":
            album = client.albums_with_tracks(ids[0])
            if album is None:
                raise LinkFetchError("Альбом не найден")
            return yandex_album_extraction(album)
        if kind == "track":
            tracks = client.tracks([ids[0]])
            items = [it for t in tracks or [] if (it := yandex_track_item(t))]
            if not items:
                raise LinkFetchError("Трек не найден")
            return Extraction(source_app="yandex_music", page_type="other", items=items)
        if kind == "user_playlist":
            owner, pl_kind = ids
            playlist = client.users_playlists(int(pl_kind), owner)
        else:
            playlist = client.playlist(ids[0])
        if playlist is None:
            raise LinkFetchError("Плейлист не найден")
        missing = [str(s.id).split(":")[0] for s in playlist.tracks or [] if s.track is None]
        extra: dict[str, Any] = {}
        for i in range(0, len(missing), YANDEX_TRACKS_CHUNK):
            for t in client.tracks(missing[i:i + YANDEX_TRACKS_CHUNK]) or []:
                extra[str(t.id)] = t
        result = yandex_playlist_extraction(playlist, extra)
        if not result.items and (playlist.track_count or 0) > 0:
            raise LinkFetchError("Плейлист недоступен без входа в Яндекс Музыку (закрытый?)")
        return result
    except (NotFoundError, BadRequestError) as e:  # unknown owner/kind → BadRequest "playlistIdBindingError"
        raise LinkFetchError("Не найдено в Яндекс Музыке (ссылка устарела или плейлист закрыт)") from e
    except TimedOutError as e:
        raise LinkFetchError("Яндекс Музыка не ответила вовремя") from e
    except UnauthorizedError as e:
        raise LinkFetchError("Яндекс Музыка требует вход для этой ссылки") from e
    except YandexMusicError as e:
        raise LinkFetchError(f"Яндекс Музыка не ответила: {e}") from e


async def _yandex(path: str) -> Extraction:
    kind, ids = parse_yandex_path(path)
    return await asyncio.to_thread(_yandex_fetch, kind, ids)


# --------------------------------------------------------------------------- YouTube


def clean_yt_channel(name: str | None) -> str:
    """'Кино - Topic' / 'Кино - Тема' → 'Кино'; 'KinoVEVO' stays (no reliable split)."""
    return _YT_CHANNEL_SUFFIX_RE.sub("", (name or "").strip()).strip()


def clean_yt_title(title: str) -> str:
    t = _YT_TRACK_NO_RE.sub("", title or "")
    t = _YT_JUNK_RE.sub("", t)
    return " ".join(t.split())


def youtube_entry_item(entry: dict, *, album_mode: bool = False) -> ExtractedItem | None:
    """yt-dlp (flat) entry → ExtractedItem. Artist: metadata > '- Topic' channel > album channel > 'A - B' title >
    channel (low confidence)."""
    raw_title = (entry.get("track") or entry.get("title") or "").strip()
    if not raw_title or raw_title in {"[Deleted video]", "[Private video]", "[Удаленное видео]"}:
        return None
    channel_raw = entry.get("channel") or entry.get("uploader") or ""
    channel = clean_yt_channel(channel_raw)
    artist = entry.get("artist") or _join_names(entry.get("artists") or [])
    duration = fmt_duration(entry.get("duration"))
    album = entry.get("album")
    if artist and entry.get("track"):
        return ExtractedItem(artist_raw=artist, title_raw=raw_title, album_raw=album, duration=duration, conf=0.95)
    title = clean_yt_title(raw_title)
    if channel and channel != channel_raw.strip():  # auto-generated "Artist - Topic" channel: title is the track
        return ExtractedItem(artist_raw=channel, title_raw=title, album_raw=album, duration=duration, conf=0.9)
    if album_mode and channel:
        return ExtractedItem(artist_raw=channel, title_raw=title, album_raw=album, duration=duration, conf=0.85)
    if m := _DASH_SPLIT_RE.match(title):
        return ExtractedItem(
            artist_raw=m["artist"], title_raw=m["title"], album_raw=album, duration=duration, conf=0.8
        )
    return ExtractedItem(artist_raw=artist or channel, title_raw=title, album_raw=album, duration=duration, conf=0.5)


def youtube_info_extraction(info: dict) -> Extraction:
    """yt-dlp info (playlist with flat entries, or a single video) → Extraction."""
    if info.get("_type") == "playlist" or "entries" in info:
        pl_id = str(info.get("id") or "")
        title = (info.get("title") or "").strip()
        album_mode = pl_id.startswith("OLAK5uy_") or title.startswith("Album - ")
        entries = [e for e in info.get("entries") or [] if e]
        items = [it for e in entries if (it := youtube_entry_item(e, album_mode=album_mode))]
        if album_mode:
            album_title = title.removeprefix("Album - ").strip() or None
            artists = Counter(clean_yt_channel(e.get("channel") or e.get("uploader")) for e in entries)
            artists.pop("", None)
            album_artist = artists.most_common(1)[0][0] if artists else None
            items = [it.model_copy(update={"album_raw": it.album_raw or album_title}) for it in items]
            ctx = ExtractionContext(album_title=album_title, album_artist=album_artist)
            return Extraction(source_app="youtube", page_type="album", context=ctx, items=items)
        ctx = ExtractionContext(playlist_name=title or None)
        return Extraction(source_app="youtube", page_type="playlist", context=ctx, items=items)
    item = youtube_entry_item(info)
    return Extraction(source_app="youtube", page_type="other", items=[item] if item else [])


def youtube_target(url: str, p: Any | None = None) -> tuple[str, str]:
    """Normalize a YouTube URL → ("playlist"|"video"|"album", url for yt-dlp). Raises UnsupportedLink."""
    p = p or urlparse(url)
    host = (p.hostname or "").lower()
    qs = parse_qs(p.query)
    lst = (qs.get("list") or [""])[0]
    path = p.path.rstrip("/")
    if host.endswith("youtu.be"):
        vid = path.lstrip("/")
        if vid:
            return "video", f"https://www.youtube.com/watch?v={vid}"
    if path.startswith("/browse/MPREb_"):
        return "album", f"https://music.youtube.com{path}"
    # radio/mix lists (RD…) are generated per user: treat as the video itself
    if lst and not lst.startswith("RD") and path in ("/playlist", "/watch", ""):
        return "playlist", f"https://www.youtube.com/playlist?list={lst}"
    if path == "/watch" and (vid := (qs.get("v") or [""])[0]):
        return "video", f"https://www.youtube.com/watch?v={vid}"
    if path.startswith("/shorts/") and (vid := path.split("/")[2] if len(path.split("/")) > 2 else ""):
        return "video", f"https://www.youtube.com/watch?v={vid}"
    raise UnsupportedLink("Ссылка YouTube не поддерживается: нужен плейлист, альбом или видео")


def _ytdlp_extract(url: str) -> dict:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignore_no_formats_error": True,
        "check_formats": False,
        "socket_timeout": 30,
        "retries": 2,
    }
    if proxy := proxy_for_domains(*YOUTUBE_PROXY_DOMAINS):
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info) or {}


async def _youtube(url: str, p: Any) -> Extraction:
    _, target = youtube_target(url, p)
    try:
        info = await asyncio.to_thread(_ytdlp_extract, target)
    except (DownloadError, ExtractorError) as e:
        raise LinkFetchError(f"YouTube: не удалось прочитать ссылку ({e})") from e
    result = youtube_info_extraction(info)
    if not result.items:
        raise LinkFetchError("YouTube: в плейлисте нет доступных видео")
    return result


# --------------------------------------------------------------------------- Spotify


def spotify_next_data(html: str) -> dict:
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        raise LinkFetchError("Spotify: не удалось прочитать страницу")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise LinkFetchError("Spotify: не удалось прочитать страницу") from e


def _spotify_artists(subtitle: str | None) -> str:
    parts = [x.strip() for x in (subtitle or "").replace(" ", " ").split(",")]
    return ", ".join(x for x in parts if x)


def spotify_extraction(data: dict) -> Extraction:
    """`__NEXT_DATA__` of an open.spotify.com/embed/... page → Extraction."""
    try:
        ent = data["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError) as e:
        raise LinkFetchError("Spotify: страница не содержит треков (изменился формат?)") from e
    etype = ent.get("type")
    name = (ent.get("name") or ent.get("title") or "").strip() or None
    if etype == "track":
        artist = _join_names([a.get("name") for a in ent.get("artists") or []]) or _spotify_artists(ent.get("subtitle"))
        duration = fmt_duration((ent.get("duration") or 0) / 1000)
        item = ExtractedItem(artist_raw=artist, title_raw=name or "", duration=duration, conf=1.0)
        return Extraction(source_app="spotify", page_type="other", items=[item] if item.title_raw else [])
    album_mode = etype == "album"
    items: list[ExtractedItem] = []
    for t in ent.get("trackList") or []:
        if t.get("entityType", "track") != "track" or not t.get("title"):
            continue
        items.append(ExtractedItem(
            artist_raw=_spotify_artists(t.get("subtitle")),
            title_raw=t["title"],
            album_raw=name if album_mode else None,
            duration=fmt_duration((t.get("duration") or 0) / 1000),
            conf=1.0,
        ))
    if album_mode:
        ctx = ExtractionContext(album_title=name, album_artist=_spotify_artists(ent.get("subtitle")) or None)
        return Extraction(source_app="spotify", page_type="album", context=ctx, items=items)
    return Extraction(source_app="spotify", page_type="playlist", context=ExtractionContext(playlist_name=name),
                      items=items)


async def _spotify(kind: str, sid: str) -> Extraction:
    url = f"https://open.spotify.com/embed/{kind}/{sid}"
    try:
        async with make_client(http2=False, headers={"Accept-Language": "en"}) as client:
            resp = await request_with_backoff(client, "GET", url, retries=2)
    except Exception as e:  # network errors of any kind → user-facing message
        raise LinkFetchError(f"Spotify недоступен: {e}") from e
    if resp.status_code == 404:
        raise LinkFetchError("Spotify: не найдено (ссылка устарела или плейлист закрыт)")
    if resp.status_code != 200:
        raise LinkFetchError(f"Spotify ответил HTTP {resp.status_code}")
    result = spotify_extraction(spotify_next_data(resp.text))
    if not result.items:
        raise LinkFetchError("Spotify: треки не найдены")
    return result


async def _spotify_short(url: str) -> Extraction:
    try:
        async with make_client(http2=False) as client:
            resp = await client.get(url)
    except Exception as e:
        raise LinkFetchError(f"Spotify недоступен: {e}") from e
    final = urlparse(str(resp.url))
    if final.hostname == "open.spotify.com" and (m := _SPOTIFY_RE.match(final.path)):
        return await _spotify(m.group(1).lower(), m.group(2))
    raise UnsupportedLink()
