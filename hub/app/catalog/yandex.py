"""Yandex Music (anonymous API) as the source of original Cyrillic names for catalog tracks.

Deezer often lists Russian releases transliterated ("LSP — Chto-to eshhyo"). hitmoz finds them only by the original
names ("ЛСП — Что-то ещё"), and YouTube copies of Russian tracks are often censored (PLAN 3.2), so such tracks must
take the Russian source order with Cyrillic queries (PLAN 8.3).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.catalog.cache import cached
from app.config import get_settings
from app.matching.normalize import has_cyrillic, is_russian_query
from app.matching.score import score
from app.providers.base import TrackQuery

log = logging.getLogger(__name__)

YANDEX_API_HOST = "api.music.yandex.net"
YANDEX_TIMEOUT_S = 20
SEARCH_LIMIT = 5
TTL_S = 7 * 86400

# GS1 prefixes of EAN-13 release barcodes: Russia 460-469, Kyrgyzstan 470, Uzbekistan 478, Belarus 481, Ukraine 482,
# Moldova 484, Kazakhstan 487. ISRC starts with the registrant's country code.
_CIS_EAN_PREFIXES = ("46", "470", "478", "481", "482", "484", "487")
_CIS_ISRC_COUNTRIES = {"RU", "BY", "UA", "KZ"}


# Latin -> Cyrillic for search queries only (Yandex search does not understand transliterated titles, but finds
# "Oxxxymiron Где нас нет"). Longest match first; soft/hard signs can't be restored, the search is fuzzy enough.
_FROM_LAT = [
    ("shch", "щ"), ("shh", "щ"), ("sch", "щ"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ye", "е"), ("zh", "ж"),
    ("kh", "х"), ("ch", "ч"), ("sh", "ш"), ("ts", "ц"), ("iy", "ий"), ("yy", "ый"), ("x", "кс"), ("j", "й"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("w", "в"), ("g", "г"), ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"),
    ("k", "к"), ("q", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"),
    ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "ц"), ("'", "ь"),
]
_FROM_LAT_RE = re.compile("|".join(re.escape(a) for a, _ in _FROM_LAT) + "|y")
_FROM_LAT_MAP = dict(_FROM_LAT)
_VOWELS = set("aeiouy")


def latin_to_cyrillic(s: str) -> str:
    def repl(m: re.Match[str]) -> str:
        t = m.group(0)
        if t == "y":  # й after a vowel ("Moy"), otherwise ы ("Ty")
            return "й" if m.start() and s_low[m.start() - 1] in _VOWELS else "ы"
        return _FROM_LAT_MAP[t]

    s_low = s.casefold()
    return _FROM_LAT_RE.sub(repl, s_low)


def yandex_client(token: str | None = None) -> Any:
    """Anonymous client, or one signed in with the user's token (imports: `app.imports.sources.yandex`)."""
    from yandex_music import Client
    from yandex_music.utils.request import Request

    Client._Client__notice_displayed = True  # silence the banner the library prints to stdout
    s = get_settings()
    proxy = s.outbound_proxy if s.outbound_proxy and YANDEX_API_HOST in s.proxy_hosts else None
    return Client(token, request=Request(proxy_url=proxy, timeout=YANDEX_TIMEOUT_S), language="ru")


def cis_release(upc: str | None, isrc: str | None) -> bool:
    """Barcode or ISRC registered in Russia/CIS."""
    u = (upc or "").strip()
    if len(u) == 13 and u.isdigit() and u.startswith(_CIS_EAN_PREFIXES):
        return True
    return (isrc or "")[:2].upper() in _CIS_ISRC_COUNTRIES


def yandex_track_row(t: Any) -> dict[str, Any] | None:
    if t is None or not getattr(t, "title", None) or getattr(t, "type", None) not in (None, "music"):
        return None
    base = t.title.strip()
    version = (getattr(t, "version", None) or "").strip()
    artists = [a.name for a in (t.artists or []) if getattr(a, "name", None)]
    ms = getattr(t, "duration_ms", None)
    return {
        "artist": ", ".join(artists),
        "artists": artists,
        "title": f"{base} ({version})" if version else base,  # full, for matching
        "base": base,  # without the version ("Remix; feat. Oxxxymiron")
        "duration_s": round(ms / 1000) if ms else None,
    }


_SUFFIX_RE = re.compile(r"^(.*?\S)(\s*[(\[].*|\s+-\s+.*)?$")


def split_suffix(title: str) -> tuple[str, str]:
    """"Bezumie (Remix)" -> ("Bezumie", " (Remix)"); "Chto-to eshhyo" -> ("Chto-to eshhyo", "")."""
    m = _SUFFIX_RE.match(title.strip())
    return (m.group(1), m.group(2) or "") if m else (title, "")


def _search_tracks(text: str) -> list[dict[str, Any]]:
    res = yandex_client().search(text, type_="track")
    tracks = res.tracks.results if res is not None and res.tracks is not None else []
    return [row for t in tracks[:SEARCH_LIMIT] if (row := yandex_track_row(t))]


async def original_names(artist: str, title: str, duration_s: int | None) -> dict[str, Any] | None:
    """The same track on Yandex Music ({artist, title, duration_s}) if it matches confidently, else None."""
    threshold = get_settings().cfg.matching.auto_threshold

    async def produce() -> dict[str, Any] | None:
        queries = [f"{artist} {title}"]
        if not has_cyrillic(title) and (cyr := latin_to_cyrillic(title)) != title.casefold():
            queries.append(f"{artist} {cyr}")  # transliterated title: the artist name is found in Latin
        for text in queries:
            try:
                rows = await asyncio.to_thread(_search_tracks, text)
            except Exception as e:  # network/API trouble: not cached, the download keeps the catalog names
                log.warning("yandex search failed for %s: %r", text, e)
                return None
            scored = [(score(artist, title, r["artist"], r["title"], query_duration=duration_s,
                             cand_duration=r["duration_s"]).total, r) for r in rows]
            best = max(scored, key=lambda x: x[0], default=(0.0, {}))
            if best[0] >= threshold:
                return best[1]
        return {}  # "no match", cached too

    found = await cached(f"ym:orig3:{artist}|{title}|{duration_s}", TTL_S, produce)
    return found or None


async def russian_original(q: TrackQuery, upc: str | None = None) -> tuple[TrackQuery, TrackQuery]:
    """For a track with Latin catalog names: (tag query, provider query).

    Cyrillic original found on Yandex Music -> providers search by it in the Russian source order, and a transliterated
    title is replaced in the tags (the artist stays as in the catalog, so albums stay consistent). The search uses the
    main artist and the Cyrillic base title + the catalog's own suffix: hitmoz finds "ЛСП Безумие (Remix)", not
    "ЛСП, Oxxxymiron Безумие (Remix; feat. Oxxxymiron)". A CIS barcode/ISRC alone -> Russian order, catalog names."""
    if is_russian_query(q):
        return q, q
    found = await original_names(q.artist, q.title, q.duration_s)
    if found and has_cyrillic(found["artist"] + found["title"]):
        log.info("russian original for %s - %s: %s - %s", q.artist, q.title, found["artist"], found["title"])
        artist = (found.get("artists") or found["artist"].split(", "))[0]
        title = (found.get("base") or found["title"]) + split_suffix(q.title)[1]
        tag_q = q.model_copy(update={"title": title}) if has_cyrillic(title) else q
        return tag_q, q.model_copy(update={"artist": artist, "title": title, "lang": "ru"})
    if cis_release(upc, q.isrc):
        return q, q.model_copy(update={"lang": "ru"})
    return q, q
