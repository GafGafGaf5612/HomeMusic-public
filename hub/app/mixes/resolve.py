"""What a source's «artist — title» is in the catalog (MIXES.md 4.1), the way imports match their rows
(`app.imports.resolve`: `source_map` remembers each source id for everyone; `match_catalog` scores the Deezer search).

Only a confident Deezer match (`auto`) is taken: a mix downloads what it suggests, a wrong song costs a download.
The library is asked first (a song the library has needs no catalog search).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from sqlalchemy.exc import IntegrityError

from app.catalog.cache import cache_get, cache_set
from app.catalog.models import CatalogTrack, split_ref
from app.config import get_settings
from app.db import session_scope
from app.imports.resolve import Resolution, _remember, _remembered
from app.library.owned import find_existing
from app.matching.select import classify_scores, match_catalog
from app.mixes.sources import Hit
from app.services import svc

log = logging.getLogger(__name__)

TRACK_TTL_S = 30 * 86400
PARALLEL = 4


def library_track(t) -> CatalogTrack:
    """A library song as a catalog-like track: its Deezer record when it has one, else `nd:<song>`."""
    if t.deezer_track_id:
        return CatalogTrack(ref=f"dz:{t.deezer_track_id}", source="dz", id=str(t.deezer_track_id), title=t.title,
                            artist=t.artist, artists=[t.artist], album_title=t.album, duration_s=t.duration_s,
                            isrc=t.isrc, explicit=t.explicit)
    return CatalogTrack(ref=f"nd:{t.nd_song_id}", source="nd", id=str(t.nd_song_id), title=t.title, artist=t.artist,
                        artists=[t.artist], album_title=t.album, duration_s=t.duration_s, isrc=t.isrc,
                        explicit=t.explicit)


def external_id(hit: Hit) -> str:
    if hit.source_id:
        return hit.source_id[:128]
    raw = f"{hit.artist}|{hit.title}".casefold()
    return raw if len(raw) <= 128 else hashlib.sha1(raw.encode()).hexdigest()


async def remember_track(t: CatalogTrack) -> None:
    await cache_set(f"mix:track:{t.ref}", t.model_dump(mode="json"), TRACK_TTL_S)


async def catalog_track(ref: str) -> CatalogTrack | None:
    """The catalog record of a Deezer track (a source's match remembered, else Deezer's `track/{id}`)."""
    if (hit := await cache_get(f"mix:track:{ref}")) is not None:
        return CatalogTrack.model_validate(hit)
    src, id_ = split_ref(ref)
    if src != "dz" or svc.deezer is None:
        return None
    try:
        t = await svc.deezer.get_track(id_)
    except Exception as e:
        log.info("mix: catalog track %s: %r", ref, e)
        return None
    await remember_track(t)
    return t


async def _search(hit: Hit) -> Resolution:
    if svc.deezer is None:
        return Resolution("not_found")
    found = await svc.deezer.search_tracks(hit.artist, hit.title, limit=10)
    if not found:
        return Resolution("not_found")
    scored = match_catalog(hit.artist, hit.title, found, duration=hit.duration_s)
    if classify_scores([x[1].total for x in scored], get_settings().cfg.matching) != "auto":
        return Resolution("not_found", None, round(scored[0][1].total, 3))
    await remember_track(scored[0][0])
    return Resolution("exact", scored[0][0].ref, round(scored[0][1].total, 3))


async def resolve_hit(source: str, hit: Hit) -> CatalogTrack | None:
    async with session_scope() as s:
        lib = await find_existing(s, artist=hit.artist, title=hit.title, duration_s=hit.duration_s)
        if lib is not None and lib.nd_song_id:
            return library_track(lib)
        known = await _remembered(s, source, "track", external_id(hit))
    if known is None:
        r = await _search(hit)
        try:
            async with session_scope() as s:
                await _remember(s, source, "track", external_id(hit), r)
        except IntegrityError:
            pass  # another build remembered the same id meanwhile
    else:
        r = Resolution(known.match, known.ref, known.score)
    if r.match not in ("exact",) or not r.ref:
        return None
    return await catalog_track(r.ref)


async def resolve_hits(source: str, hits: list[Hit]) -> list[CatalogTrack | None]:
    """Each hit's catalog track (None: not found or not sure), in order. A catalog error fails one hit, not all.
    A source id given twice (two seeds' lists share a track) is looked up once."""
    sem = asyncio.Semaphore(PARALLEL)

    async def one(h: Hit) -> CatalogTrack | None:
        if not h.artist or not h.title:
            return None
        async with sem:
            try:
                return await resolve_hit(source, h)
            except Exception as e:
                log.info("mix: resolve %s - %s (%s): %r", h.artist, h.title, source, e)
                return None

    unique = list({external_id(h): h for h in hits}.values())
    found = dict(zip((external_id(h) for h in unique), await asyncio.gather(*(one(h) for h in unique)), strict=True))
    return [found[external_id(h)] for h in hits]
