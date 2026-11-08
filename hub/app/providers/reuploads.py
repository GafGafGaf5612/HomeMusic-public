"""Re-uploads of tracks by small channels: YouTube videos and SoundCloud tracks (PLAN 3.2).

Since the 2026 law the official copies of many Russian tracks are edited; fans re-upload the originals and title them
"(без цензуры)", "(правильная версия)". These providers find such copies. They aren't in the regular source order:
the Files tab of the search shows them, and a download of an explicit Russian track takes one when no regular source
has a copy known to be the original (`app.matching.select.trusted_original`). Both go through yt-dlp: no account,
no API key.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

from app.config import YtdlpCfg
from app.matching.normalize import (
    _DASH_SPLIT_RE,
    _prep,
    drop_markers,
    explicit_marker,
    normalize_artist,
    uncensored_marker,
)
from app.matching.score import artist_sim
from app.providers.base import Candidate, ProviderError, StreamSource, TrackQuery
from app.providers.ytdlp import (
    _TOPIC_SUFFIX,
    _VEVO_SUFFIX,
    YoutubeAccess,
    YtdlpProvider,
    _parse_upload_date,
    entry_to_candidate,
    search_text,
    ytsearch_query,
)

log = logging.getLogger(__name__)

UNCENSORED_QUERY = "без цензуры"
MAX_DURATION_S = 15 * 60  # longer uploads are mixtapes, streams, whole albums
# videos about a track rather than the track; dropped unless the query asks for them
NOISE_RE = re.compile(
    r"(?<!\w)(?:реакци\w*|reaction|reacts?|разбор\w*|смотр[ия]т|mashup|м[эе]шап\w*|backstage|бэкстейдж\w*|"
    r"интервью|interview|обзор\w*|review|tutorial|туториал\w*|урок\w*|аккорд\w*|на\s+гитаре|подкаст\w*|podcast|"
    r"стрим\w*|stream|live|лайв|концерт\w*|выступлени\w*|плейлист\w*|playlist|сборник\w*|mixtape|микстейп\w*|"
    r"full\s+album|альбом\s+целиком|сниппет\w*|snippet)(?!\w)",
    re.IGNORECASE,
)


def reupload_queries(q: TrackQuery) -> list[str]:
    """The track as asked and the track "без цензуры": re-uploaders put the word in the title."""
    text = search_text(q)
    if uncensored_marker(text):
        return [text]
    return [text, f"{text} {UNCENSORED_QUERY}"]


def split_upload_title(raw: str, query_artist: str, fallback_artist: str) -> tuple[str, str]:
    """(artist, title) of an upload titled "Artist - Title" or "Title - Artist" (re-uploaders use both: the side
    closer to the asked artist is the artist), without marker groups. No dash: the uploader is the artist."""
    s = drop_markers(raw)
    parts = _DASH_SPLIT_RE.split(s, maxsplit=1)
    if len(parts) == 2 and all(p.strip() for p in parts):
        artist, title = (p.strip() for p in parts)
        if query_artist:
            qa = normalize_artist(query_artist)
            if artist_sim(qa, normalize_artist(title)) > artist_sim(qa, normalize_artist(artist)):
                artist, title = title, artist
        return artist, title
    return fallback_artist, s


def is_noise(raw: str, query_text: str) -> bool:
    return bool(NOISE_RE.search(_prep(raw))) and not NOISE_RE.search(_prep(query_text))


def as_reupload(c: Candidate, raw_title: str, q: TrackQuery, fallback_artist: str, provider: str) -> Candidate:
    """Names parsed from the upload title; its own word on censorship in `explicit` and `extra.uncensored`."""
    artist, title = split_upload_title(raw_title, q.artist, fallback_artist)
    extra: dict[str, Any] = {**c.extra, "reupload": True, "original_title": raw_title}
    if (marker := uncensored_marker(raw_title)) is not None:
        extra["uncensored"] = marker
    return c.model_copy(update={
        "provider": provider,
        "artist": artist or fallback_artist,
        "title": title,
        "explicit": explicit_marker(raw_title),
        "extra": extra,
    })


def order_reuploads(cands: list[Candidate]) -> list[Candidate]:
    """Titled "(без цензуры)" first, titled clean last; otherwise the search order. De-duplicated by id."""
    seen: set[str] = set()
    unique: list[Candidate] = []
    for c in cands:
        if c.provider_id not in seen:
            seen.add(c.provider_id)
            unique.append(c)
    rank = {True: 0, None: 1, False: 2}
    return sorted(unique, key=lambda c: rank[c.extra.get("uncensored")])


async def _gather_searches(searches: list, name: str) -> list[list[dict]]:
    """Run the searches at once; a failed one is skipped unless all of them failed."""
    results = await asyncio.gather(*searches, return_exceptions=True)
    ok = [r for r in results if not isinstance(r, BaseException)]
    if not ok:
        err = results[0]
        raise err if isinstance(err, ProviderError) else ProviderError(f"{name}: {err}")
    for r in results:
        if isinstance(r, BaseException):
            log.info("%s: one of the searches failed: %r", name, r)
    return [r.get("entries") or [] for r in ok]


class YtReuploadProvider(YtdlpProvider):
    """Plain YouTube search without the official copies (YouTube Music, "- Topic", VEVO, verified channels)."""

    name = "yt_reupload"

    def __init__(self, cfg: YtdlpCfg, *, max_parallel: int | None = None, access: YoutubeAccess | None = None) -> None:
        super().__init__(cfg, use_music_search=False, max_parallel=max_parallel, access=access)

    async def search(self, q: TrackQuery, limit: int = 10) -> list[Candidate]:
        texts = reupload_queries(q)
        searches = [
            self._run(self._extract, ytsearch_query(self.cfg.search_prefix, t, limit), extract_flat="in_playlist",
                      noplaylist=False)
            for t in texts
        ]
        out: list[Candidate] = []
        for entries in await _gather_searches(searches, self.name):
            for e in entries:
                if not e or self._official(e) or is_noise(e.get("title") or "", texts[0]):
                    continue
                if (e.get("duration") or 0) > MAX_DURATION_S:
                    continue
                if c := entry_to_candidate(e, source="reupload"):
                    channel = _VEVO_SUFFIX.sub("", _TOPIC_SUFFIX.sub("", c.extra.get("channel") or "")).strip()
                    out.append(as_reupload(c, e.get("title") or c.title, q, channel, self.name))
        return order_reuploads(out)[:limit]

    @staticmethod
    def _official(e: dict) -> bool:
        channel = e.get("channel") or e.get("uploader") or ""
        return bool(e.get("channel_is_verified") or _TOPIC_SUFFIX.search(channel) or _VEVO_SUFFIX.search(channel))


def sc_entry_to_candidate(e: dict) -> Candidate | None:
    """A SoundCloud search entry (yt-dlp, flat): title, uploader, upload time, duration, plays, artwork."""
    tid, raw, url = e.get("id"), (e.get("title") or "").strip(), e.get("webpage_url")
    if not tid or not raw or not url:
        return None
    ts = e.get("timestamp")
    uploaded: date | None = datetime.fromtimestamp(ts, tz=UTC).date() if ts else _parse_upload_date(
        e.get("upload_date"))
    artists = [a for a in (e.get("artists") or []) if isinstance(a, str) and a]
    thumbs = e.get("thumbnails") or []
    duration = e.get("duration")
    return Candidate(
        provider="soundcloud",
        provider_id=str(tid),
        artist=", ".join(artists) or (e.get("uploader") or "").strip(),
        title=raw,
        duration_s=round(float(duration)) if duration else None,
        upload_date=uploaded,
        page_url=url,
        cover_url=e.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None),
        extra={k: v for k, v in {
            "source": "soundcloud",
            "channel": e.get("uploader"),
            "view_count": e.get("view_count"),
        }.items() if v is not None},
    )


class SoundcloudProvider(YtdlpProvider):
    """SoundCloud through yt-dlp (its public web client id, no account). Audio is HLS AAC 160 kbps: no `stream`
    (the Hub proxies plain HTTP audio); listening plays a copy the Hub downloads first (`app.listen`)."""

    name = "soundcloud"
    streams = False

    def __init__(self, cfg: YtdlpCfg, *, max_parallel: int = 2) -> None:
        super().__init__(cfg, use_music_search=False, max_parallel=max_parallel)

    async def search(self, q: TrackQuery, limit: int = 10) -> list[Candidate]:
        texts = reupload_queries(q)
        searches = [
            self._run(self._extract, f"scsearch{max(1, limit)}:{t}", extract_flat="in_playlist", noplaylist=False)
            for t in texts
        ]
        out: list[Candidate] = []
        for entries in await _gather_searches(searches, self.name):
            for e in entries:
                if not e or is_noise(e.get("title") or "", texts[0]) or (e.get("duration") or 0) > MAX_DURATION_S:
                    continue
                if c := sc_entry_to_candidate(e):
                    out.append(as_reupload(c, c.title, q, c.artist, self.name))
        return order_reuploads(out)[:limit]

    async def enrich(self, c: Candidate) -> Candidate:
        if c.enriched:
            return c
        info = await self._run(self._extract, c.page_url, check_formats=False)
        abrs = [f.get("abr") for f in info.get("formats") or [] if f.get("abr")]
        return c.model_copy(update={
            "enriched": True,
            "provider_id": str(info.get("id") or c.provider_id),
            "duration_s": round(float(info["duration"])) if info.get("duration") else c.duration_s,
            "bitrate_kbps": round(max(abrs)) if abrs else c.bitrate_kbps,
            "upload_date": _parse_upload_date(info.get("upload_date")) or c.upload_date,
            "cover_url": info.get("thumbnail") or c.cover_url,
        })

    def media_url(self, c: Candidate) -> str:
        return c.page_url

    async def stream(self, c: Candidate) -> StreamSource:
        raise ProviderError("SoundCloud: звук только через загрузку файла")
