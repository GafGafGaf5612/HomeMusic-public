"""Matching an import's rows against the library and the catalog, the way the assistant does it (`match_catalog`,
duration ±3 s, the album as a hint), but quietly and in batches.

Verdicts: `owned` — in the library already; `exact` — a confident catalog match (goes to work without asking);
`probable` — a weaker one (goes to work too, but the report lists it); `not_found` — waits for the user.
What a service id turned out to be is kept in `source_map` for every user: a second import of the same
songs barely searches. The library is checked every time (it changes), the catalog once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import make_ref, split_ref
from app.config import get_settings
from app.library.owned import find_existing
from app.matching.normalize import normalize_artist, split_version, version_kind
from app.matching.score import score
from app.matching.select import classify_scores, match_catalog, rank_candidates
from app.models import SourceMap, Track, utcnow
from app.providers.base import TrackQuery
from app.services import svc

log = logging.getLogger(__name__)

VERDICT = {"auto": "exact", "confirm": "probable", "not_found": "not_found"}
NOT_FOUND_TTL_DAYS = 7  # a catalog miss is looked up again after this long (catalogs and sources grow)


class CatalogDown(Exception):
    """The catalog did not answer: the row stays pending and is tried again later."""


@dataclass
class Resolution:
    match: str  # owned/exact/probable/not_found
    ref: str | None = None
    score: float | None = None
    track: Track | None = None  # owned


async def _remembered(s: AsyncSession, source: str, kind: str, external_id: str) -> SourceMap | None:
    row = await s.get(SourceMap, (source, kind, external_id))
    if row is None:
        return None
    if row.match == "not_found" and (utcnow() - row.updated_at).days >= NOT_FOUND_TTL_DAYS:
        return None
    return row


async def _remember(s: AsyncSession, source: str, kind: str, external_id: str, r: Resolution) -> None:
    # The same song can be resolved concurrently from likes and a playlist (or two users).
    stmt = insert(SourceMap).values(source=source, kind=kind, external_id=external_id,
                                    ref=r.ref, match=r.match, score=r.score, updated_at=utcnow())
    await s.execute(stmt.on_conflict_do_update(
        index_elements=[SourceMap.source, SourceMap.kind, SourceMap.external_id],
        set_={name: getattr(stmt.excluded, name) for name in ("ref", "match", "score", "updated_at")},
    ))


async def _owned(s: AsyncSession, raw: dict[str, Any], ref: str | None = None) -> Track | None:
    dz_id = None
    if ref:
        src, id_ = split_ref(ref)
        dz_id = id_ if src == "dz" else None
    return await find_existing(s, artist=raw.get("artist") or "", title=raw.get("title") or "",
                               duration_s=raw.get("duration_s"), deezer_track_id=dz_id)


async def catalog_track(raw: dict[str, Any]) -> Resolution:
    """Deezer first; a track Deezer lacks may be found on hitmoz (never `exact`: no curated metadata)."""
    cfg = get_settings().cfg.matching
    artist, title = raw.get("artist") or "", raw.get("title") or ""
    main = (raw.get("artists") or [artist])[0]
    base, version = split_version(title)
    best: list = []
    if svc.deezer is not None:
        for qa, qt in dict.fromkeys([(main, title), (main, base), (artist, title)]):
            try:
                found = await svc.deezer.search_tracks(qa, qt, limit=10)
            except Exception as e:
                raise CatalogDown(repr(e)) from e
            if not found:
                continue
            scored = match_catalog(artist, title, found, album=raw.get("album"), duration=raw.get("duration_s"))
            if not best or scored[0][1].total > best[0][1].total:
                best = scored
            if classify_scores([x[1].total for x in best], cfg) == "auto":
                break
    if best:
        verdict = VERDICT[classify_scores([x[1].total for x in best], cfg)]
        if verdict != "not_found":
            return Resolution(verdict, best[0][0].ref, round(best[0][1].total, 3))
    if (hm := svc.providers.get("hitmoz")) is not None:
        q = TrackQuery(artist=main, title=title, album=raw.get("album"), duration_s=raw.get("duration_s"))
        search_title = title if version_kind(version) else base or title
        try:
            ranked = rank_candidates(q, await hm.search(q.model_copy(update={"title": search_title}), limit=5), cfg)
        except Exception as e:
            log.info("import: hitmoz search failed for %s - %s: %r", artist, title, e)
            ranked = []
        if ranked and ranked[0].score >= cfg.auto_threshold:
            return Resolution("probable", make_ref("hm", ranked[0].provider_id), round(ranked[0].score, 3))
    return Resolution("not_found", None, round(best[0][1].total, 3) if best else None)


async def resolve_track(s: AsyncSession, source: str, external_id: str, raw: dict[str, Any]) -> Resolution:
    lib = await _owned(s, raw)
    known = await _remembered(s, source, "track", external_id)
    if lib is not None:
        return Resolution("owned", known.ref if known else None, 1.0, lib)
    if known is not None:
        r = Resolution(known.match, known.ref, known.score)
    else:
        r = await catalog_track(raw)
        await _remember(s, source, "track", external_id, r)
    if r.ref and (lib := await _owned(s, raw, r.ref)) is not None:
        return Resolution("owned", r.ref, 1.0, lib)
    return r


async def resolve_album(s: AsyncSession, source: str, external_id: str, raw: dict[str, Any]) -> Resolution:
    known = await _remembered(s, source, "album", external_id)
    if known is not None:
        return Resolution(known.match, known.ref, known.score)
    cfg = get_settings().cfg.matching
    r = Resolution("not_found")
    if svc.deezer is not None:
        try:
            albums = await svc.deezer.search_albums(raw.get("artist") or "", raw.get("title") or "", limit=5)
        except Exception as e:
            raise CatalogDown(repr(e)) from e
        scored = sorted(((score(raw.get("artist") or "", raw.get("title") or "", a.artist, a.title).total, a)
                         for a in albums), key=lambda x: -x[0])
        if scored:
            verdict = VERDICT[classify_scores([x[0] for x in scored], cfg)]
            if verdict != "not_found":
                r = Resolution(verdict, scored[0][1].ref, round(scored[0][0], 3))
    await _remember(s, source, "album", external_id, r)
    return r


async def resolve_artist(s: AsyncSession, source: str, external_id: str, raw: dict[str, Any]) -> Resolution:
    known = await _remembered(s, source, "artist", external_id)
    if known is not None:
        return Resolution(known.match, known.ref, known.score)
    r = Resolution("not_found")
    name = raw.get("name") or ""
    if svc.deezer is not None and name:
        try:
            found = await svc.deezer.search(name, kinds=("artist",), limit=5)
        except Exception as e:
            raise CatalogDown(repr(e)) from e
        want = normalize_artist(name)
        for a in found.artists:
            got = normalize_artist(a.name)
            if (got.text and got.text == want.text) or (got.latin and got.latin == want.latin):
                r = Resolution("exact", a.ref, 1.0)
                break
    await _remember(s, source, "artist", external_id, r)
    return r


async def library_track_ids(s: AsyncSession, track_ids: set[int]) -> dict[int, str | None]:
    """track id -> Navidrome song id."""
    if not track_ids:
        return {}
    return dict((await s.execute(select(Track.id, Track.nd_song_id).where(Track.id.in_(track_ids)))).all())
