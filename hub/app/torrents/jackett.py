"""Jackett: tracker search over Torznab (rutracker login, captcha and proxy are set up in Jackett's own UI)."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel

from app.net import make_client

log = logging.getLogger(__name__)

TORZNAB_NS = "{http://torznab.com/schemas/2015/feed}"
APIKEY_PARAM = "jackett_apikey"
MAX_TORRENT_BYTES = 10 * 1024 * 1024


class JackettError(Exception):
    """Jackett is unreachable or refused the request (wrong key, the indexer is not configured, tracker down)."""


class MagnetRedirect(JackettError):
    """The .torrent link led to a magnet (the indexer has no .torrent): use the magnet."""

    def __init__(self, uri: str):
        super().__init__("индексатор отдал magnet-ссылку, а не .torrent")
        self.uri = uri


class ReleaseInfo(BaseModel):
    """What the release title says about its audio (rutracker: "Артист - Альбом - 1988, FLAC (tracks+.cue), lossless")."""

    codec: str | None = None
    bitrate_kbps: int | None = None
    lossless: bool = False
    hires: bool = False  # 24 bit or above 48 kHz
    layout: str | None = None  # "tracks" — a file per track; "image" — one file + .cue (not imported yet)
    year: int | None = None
    discography: bool = False


class Release(BaseModel):
    title: str
    # the .torrent through Jackett, without its API key (`JackettClient.fetch_torrent` adds it); a magnet URI when the
    # tracker gives no .torrent (public trackers: the file list comes from peers, `app.torrents.core.magnet_files`)
    link: str
    magnet: bool = False
    info_hash: str | None = None
    tracker: str | None = None  # Jackett's indexer that found it (searching "all" asks every configured one)
    details_url: str | None = None  # the release page on the tracker
    size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    grabs: int | None = None
    files: int | None = None
    published: datetime | None = None
    categories: list[int] = []
    info: ReleaseInfo = ReleaseInfo()


_CODECS = [
    ("FLAC", r"flac"), ("ALAC", r"alac"), ("APE", r"\bape\b|monkey'?s audio"), ("WavPack", r"wavpack|\bwv\b"),
    ("TTA", r"\btta\b"), ("WAV", r"\bwav\b"), ("DSD", r"\bdsd\d*\b|\bdsf\b|\bdff\b|\bsacd\b"), ("MP3", r"\bmp3\b"),
    ("AAC", r"\baac\b|\bm4a\b"), ("Opus", r"\bopus\b"), ("OGG", r"\bogg\b|vorbis"),
]
_LOSSLESS = {"FLAC", "ALAC", "APE", "WavPack", "TTA", "WAV", "DSD"}
_BITRATE_RE = re.compile(r"(\d{2,3})\s*kbps", re.IGNORECASE)
_IMAGE_RE = re.compile(r"\bimage\b|образ", re.IGNORECASE)
_TRACKS_RE = re.compile(r"\btracks?\b|по трекам", re.IGNORECASE)
_HIRES_RE = re.compile(r"\b24\s*[-/]?\s*bit\b|\b(?:88[.,]2|96|176[.,]4|192)\s*khz\b"
                       r"|\b24\s*[-/]\s*(?:88|96|176|192)\b", re.IGNORECASE)  # public trackers: "FLAC 24-192"
_YEAR_RE = re.compile(r"(?:^|[\s(\[-])((?:19[0-9]|20[0-9])[0-9])(?=[\s,)\]-]|$)")
_DISCOGRAPHY_RE = re.compile(r"discography|дискография|collection|коллекция", re.IGNORECASE)


def parse_release_title(title: str) -> ReleaseInfo:
    low = title.lower()
    codec = next((name for name, rx in _CODECS if re.search(rx, low)), None)
    m = _BITRATE_RE.search(title)
    layout = "image" if _IMAGE_RE.search(title) else "tracks" if _TRACKS_RE.search(title) else None
    if layout is None and codec in ("MP3", "AAC", "Opus", "OGG"):
        layout = "tracks"  # lossy releases are a file per track
    years = [int(y) for y in _YEAR_RE.findall(title)]
    return ReleaseInfo(
        codec=codec,
        bitrate_kbps=int(m.group(1)) if m else None,
        lossless=codec in _LOSSLESS or "lossless" in low,
        hires=bool(_HIRES_RE.search(title)),
        layout=layout,
        year=years[0] if len(years) == 1 else None,
        discography=bool(_DISCOGRAPHY_RE.search(title)),
    )


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _first(attrs: dict[str, list[str]], name: str) -> str | None:
    vals = attrs.get(name)
    return vals[0] if vals else None


_BTIH_RE = re.compile(r"^urn:btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})$")


def is_magnet(link: str) -> bool:
    return link.startswith("magnet:?")


def magnet_info_hash(uri: str) -> str:
    """The v1 info hash of a magnet URI, lower-case hex (qBittorrent's id). ValueError if it has none."""
    if not is_magnet(uri):
        raise ValueError("не magnet-ссылка")
    for xt in parse_qs(uri[len("magnet:?"):]).get("xt", []):
        if m := _BTIH_RE.match(xt):
            h = m.group(1)
            return (h if len(h) == 40 else base64.b32decode(h.upper()).hex()).lower()
    raise ValueError("в magnet-ссылке нет btih")


def merge_duplicates(releases: list[Release]) -> list[Release]:
    """One release per info hash (the same torrent on several trackers): the most seeded copy, a .torrent before a
    magnet (its file list needs no peers)."""
    best: dict[str, Release] = {}
    for r in releases:
        key = r.info_hash or f"title:{r.title.casefold()}"
        cur = best.get(key)
        if cur is None or (r.seeders or 0, not r.magnet) > (cur.seeders or 0, not cur.magnet):
            best[key] = r
    return list(best.values())


def strip_apikey(url: str) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in (APIKEY_PARAM, "apikey")]
    return urlunsplit(parts._replace(query=urlencode(q)))


def parse_torznab(xml: bytes | str) -> list[Release]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise JackettError(f"не XML: {e}") from e
    if root.tag == "error":
        raise JackettError(root.get("description") or f"ошибка {root.get('code')}")
    out: list[Release] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        enclosure = item.find("enclosure")
        link = (item.findtext("link") or (enclosure.get("url") if enclosure is not None else "") or "").strip()
        attrs: dict[str, list[str]] = {}
        for a in item.iter(f"{TORZNAB_NS}attr"):
            attrs.setdefault(a.get("name", ""), []).append(a.get("value", ""))
        if not link:
            link = (_first(attrs, "magneturl") or "").strip()
        if not title or not link:
            continue
        info_hash = None
        if is_magnet(link):
            try:
                info_hash = magnet_info_hash(link)
            except (ValueError, TypeError):
                continue
        else:
            link = strip_apikey(link)
        guid = (item.findtext("guid") or "").strip()
        seeders = _int(_first(attrs, "seeders"))
        peers = _int(_first(attrs, "peers"))
        published = None
        if pub := item.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(pub).replace(tzinfo=None)
            except (TypeError, ValueError):
                pass
        cats = [c for c in (_int(v) for v in attrs.get("category", [])) if c is not None]
        if not cats:
            cats = [c for c in (_int(e.text) for e in item.findall("category")) if c is not None]
        out.append(Release(
            title=title,
            link=link,
            magnet=info_hash is not None,
            info_hash=info_hash or (_first(attrs, "infohash") or "").lower() or None,
            tracker=(item.findtext("jackettindexer") or "").strip() or None,
            details_url=(item.findtext("comments") or "").strip() or (guid if guid.startswith("http") else None),
            size=_int(item.findtext("size")) or _int(_first(attrs, "size")),
            seeders=seeders,
            leechers=max(0, peers - seeders) if peers is not None and seeders is not None else None,
            grabs=_int(_first(attrs, "grabs")),
            files=_int(_first(attrs, "files")),
            published=published,
            categories=cats,
            info=parse_release_title(title),
        ))
    return out


class JackettClient:
    def __init__(self, base_url: str, api_key: str, indexer: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.indexer = indexer
        self._client = client or make_client(timeout=60, proxy_hosts=set(), http2=False)

    async def search(self, q: str, categories: list[int], *, timeout_s: float | None = None) -> list[Release]:
        """Everything the indexer (or every configured one, "all") found: the caller ranks and cuts."""
        url = f"{self.base_url}/api/v2.0/indexers/{self.indexer}/results/torznab/api"
        params = {"apikey": self.api_key, "t": "search", "q": q}
        if categories:
            params["cat"] = ",".join(str(c) for c in categories)
        try:
            r = await asyncio.wait_for(self._client.get(url, params=params), timeout_s)
        except TimeoutError as e:
            raise JackettError("Jackett не ответил вовремя") from e
        except httpx.HTTPError as e:
            raise JackettError(f"Jackett недоступен: {e!r}") from e
        if r.status_code != 200:
            raise JackettError(_error_text(r))
        return parse_torznab(r.content)

    def own_link(self, link: str) -> bool:
        """`link` leads to this Jackett's download proxy: the Hub fetches nothing else (links come from clients)."""
        p, base = urlsplit(link), urlsplit(self.base_url)
        return (p.scheme, p.netloc) == (base.scheme, base.netloc) and p.path.startswith(f"{base.path}/dl/")

    async def fetch_torrent(self, link: str) -> bytes:
        if not self.own_link(link):
            raise JackettError("ссылка не на этот Jackett")
        parts = urlsplit(link)
        query = urlencode([*parse_qsl(parts.query, keep_blank_values=True), (APIKEY_PARAM, self.api_key)])
        try:
            async with self._client.stream("GET", urlunsplit(parts._replace(query=query)),
                                           follow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    location = r.headers.get("location", "")
                    if is_magnet(location):
                        try:
                            magnet_info_hash(location)
                        except ValueError:  # "magnet:?null": Jackett could not get the .torrent from the tracker
                            raise JackettError("Jackett не смог скачать .torrent с трекера") from None
                        raise MagnetRedirect(location)
                    raise JackettError(f"Jackett перенаправил на {location[:100]}")
                if r.status_code != 200:
                    await r.aread()
                    raise JackettError(_error_text(r))
                data = bytearray()
                async for chunk in r.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_TORRENT_BYTES:
                        raise JackettError("слишком большой .torrent")
                return bytes(data)
        except httpx.HTTPError as e:
            raise JackettError(f"Jackett недоступен: {e!r}") from e

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_text(r: httpx.Response) -> str:
    try:
        root = ET.fromstring(r.content)
        if root.tag == "error" and root.get("description"):
            return f"Jackett: {root.get('description')}"
    except ET.ParseError:
        pass
    return f"Jackett ответил {r.status_code}"
