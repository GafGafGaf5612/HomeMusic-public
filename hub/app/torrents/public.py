"""Release search without Jackett: public trackers' own sites (TORRENT_SEARCH=public).

The phone app (pocket/) runs the Hub on Android, where Jackett can't run (.NET, no Linux DNS). It asks the trackers
directly: RuTor (Russian releases), Knaben (an aggregator of public trackers) and The Pirate Bay's apibay. Every hit is
a magnet: the file list comes from peers (`app.torrents.core.magnet_meta`). Same interface as `JackettClient`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import quote

import httpx
from selectolax.lexbor import LexborHTMLParser

from app.net import make_client
from app.torrents.jackett import JackettError, Release, is_magnet, magnet_info_hash, parse_release_title

log = logging.getLogger(__name__)

RUTOR_URL = "https://rutor.info"
KNABEN_URL = "https://api.knaben.org/v1"
APIBAY_URL = "https://apibay.org/q.php"
SOURCES = ("rutor", "knaben", "apibay")
PER_SOURCE = 50

# Torznab audio categories (`cfg.torrents.categories`) -> each site's own; anything else -> audio as a whole
_KNABEN_CATS = {3000: 1000000, 3010: 1001000, 3040: 1002000}
_APIBAY_CATS = {3000: "100", 3010: "101", 3040: "104"}
RUTOR_MUSIC = 2

# announce URLs added to magnets that come without good ones (apibay gives a bare hash, rutor its own retracker)
TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://opentor.net:6969",
]

_RU_MONTHS = {m: i for i, m in enumerate(
    ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"], start=1)}
_SIZE_RE = re.compile(r"([\d.]+)\s*(B|KB|MB|GB|TB)", re.IGNORECASE)
_SIZE_MUL = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


def magnet(info_hash: str, name: str, trackers: list[str] | None = None) -> str:
    tr = "".join(f"&tr={quote(t, safe='')}" for t in (trackers or TRACKERS))
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={quote(name, safe='')}{tr}"


def with_trackers(uri: str) -> str:
    """The magnet with the public announce URLs added (peers come from DHT too, but trackers are quicker)."""
    have = set(re.findall(r"[?&]tr=([^&]+)", uri))
    extra = [t for t in TRACKERS if quote(t, safe="") not in have and t not in have]
    return uri + "".join(f"&tr={quote(t, safe='')}" for t in extra)


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_size(text: str) -> int | None:
    m = _SIZE_RE.search(text.replace("\xa0", " "))
    return int(float(m.group(1)) * _SIZE_MUL[m.group(2).lower()]) if m else None


def parse_rutor_date(text: str) -> datetime | None:
    """"16 Авг 11" -> 2011-08-16."""
    parts = text.replace("\xa0", " ").split()
    if len(parts) != 3 or (month := _RU_MONTHS.get(parts[1].lower()[:3])) is None:
        return None
    try:
        return datetime(2000 + int(parts[2]), month, int(parts[0]))
    except ValueError:
        return None


def _release(title: str, uri: str, *, tracker: str, details_url: str | None = None, size: int | None = None,
             seeders: int | None = None, leechers: int | None = None, grabs: int | None = None,
             published: datetime | None = None, categories: list[int] | None = None) -> Release | None:
    title = " ".join(title.split())
    try:
        info_hash = magnet_info_hash(uri)
    except ValueError:
        return None
    if not title:
        return None
    return Release(title=title, link=with_trackers(uri), magnet=True, info_hash=info_hash, tracker=tracker,
                   details_url=details_url, size=size, seeders=seeders, leechers=leechers, grabs=grabs,
                   published=published, categories=categories or [3000], info=parse_release_title(title))


def parse_rutor(html: str) -> list[Release]:
    out: list[Release] = []
    for row in LexborHTMLParser(html).css("tr.gai, tr.tum"):
        tds = row.css("td")
        if len(tds) < 4:
            continue
        uri = next((a.attributes.get("href") or "" for a in tds[1].css("a")
                    if is_magnet(a.attributes.get("href") or "")), "")
        link = next((a for a in tds[1].css("a") if (a.attributes.get("href") or "").startswith("/torrent/")), None)
        if not uri or link is None:
            continue
        green, red = row.css_first("span.green"), row.css_first("span.red")
        r = _release(
            link.text(), uri, tracker="RuTor", details_url=RUTOR_URL + (link.attributes.get("href") or ""),
            size=parse_size(tds[-2].text()), seeders=_int(green.text(strip=True)) if green else None,
            leechers=_int(red.text(strip=True)) if red else None, published=parse_rutor_date(tds[0].text()),
        )
        if r:
            out.append(r)
    return out


def parse_knaben(data: dict) -> list[Release]:
    out: list[Release] = []
    for h in data.get("hits") or []:
        uri = h.get("magnetUrl") or (magnet(h["hash"], h.get("title") or "") if h.get("hash") else "")
        published = None
        if h.get("date"):
            try:
                published = datetime.fromisoformat(h["date"]).replace(tzinfo=None)
            except ValueError:
                pass
        r = _release(h.get("title") or "", uri, tracker=h.get("tracker") or "Knaben", details_url=h.get("details"),
                     size=_int(h.get("bytes")), seeders=_int(h.get("seeders")), leechers=_int(h.get("peers")),
                     grabs=_int(h.get("grabs")), published=published)
        if r:
            out.append(r)
    return out


def parse_apibay(data: list) -> list[Release]:
    out: list[Release] = []
    for h in data or []:
        info_hash = (h.get("info_hash") or "").lower()
        if h.get("id") in (None, "0") or not info_hash.strip("0"):
            continue  # "No results returned"
        added = _int(h.get("added"))
        r = _release(h.get("name") or "", magnet(info_hash, h.get("name") or ""), tracker="The Pirate Bay",
                     details_url=f"https://thepiratebay.org/description.php?id={h['id']}",
                     size=_int(h.get("size")), seeders=_int(h.get("seeders")), leechers=_int(h.get("leechers")),
                     published=datetime.fromtimestamp(added) if added else None)
        if r:
            r.files = _int(h.get("num_files"))
            out.append(r)
    return out


class PublicSearchClient:
    """`JackettClient`'s interface over public trackers; every release is a magnet (nothing to fetch)."""

    def __init__(self, sources: tuple[str, ...] = SOURCES, client: httpx.AsyncClient | None = None):
        self.sources = sources
        self.indexer = "public"
        self._client = client or make_client(timeout=20, http2=False)

    async def search(self, q: str, categories: list[int], *, timeout_s: float | None = None) -> list[Release]:
        """`timeout_s`: what the trackers that answered by then found (one blocked must not hold the others)."""
        jobs = {name: asyncio.ensure_future(getattr(self, f"_{name}")(q, categories)) for name in self.sources}
        _, late = await asyncio.wait(jobs.values(), timeout=timeout_s)
        for t in late:
            t.cancel()
        out: list[Release] = []
        errors: list[str] = []
        for name, job in jobs.items():
            res = TimeoutError("не ответил вовремя") if job in late else job.exception() or job.result()
            if isinstance(res, BaseException):
                log.warning("torrent search %s failed: %r", name, res)
                errors.append(f"{name}: {res!r}"[:200])
            else:
                out.extend(res)
        if not out and errors and len(errors) == len(jobs):
            raise JackettError("Публичные трекеры не ответили: " + "; ".join(errors))
        return out

    async def _rutor(self, q: str, categories: list[int]) -> list[Release]:
        r = await self._client.get(f"{RUTOR_URL}/search/0/{RUTOR_MUSIC}/000/2/{quote(q, safe='')}")
        r.raise_for_status()
        return parse_rutor(r.text)[:PER_SOURCE]

    async def _knaben(self, q: str, categories: list[int]) -> list[Release]:
        cats = sorted({_KNABEN_CATS.get(c, 1000000) for c in categories}) or [1000000]
        body = {"search_type": "100%", "search_field": "title", "query": q, "order_by": "seeders",
                "order_direction": "desc", "categories": cats, "size": PER_SOURCE, "hide_unsafe": True,
                "hide_xxx": True}
        r = await self._client.post(KNABEN_URL, json=body)
        r.raise_for_status()
        return parse_knaben(r.json())

    async def _apibay(self, q: str, categories: list[int]) -> list[Release]:
        cat = _APIBAY_CATS.get(categories[0], "100") if len(categories) == 1 else "100"
        r = await self._client.get(APIBAY_URL, params={"q": q, "cat": cat})
        r.raise_for_status()
        return parse_apibay(r.json())[:PER_SOURCE]

    def own_link(self, link: str) -> bool:
        return False

    async def fetch_torrent(self, link: str) -> bytes:
        raise JackettError("у публичного поиска нет .torrent: только magnet-ссылки")

    async def aclose(self) -> None:
        await self._client.aclose()
