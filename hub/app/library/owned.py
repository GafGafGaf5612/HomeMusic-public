"""What is already in the library: de-duplication before downloads and ownership badges for catalog pages.

Match order (PLAN 8.4 / 2.3): ISRC → Deezer track id → fingerprint (normalized artist+title) + duration ±3 s.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AlbumOut, TrackOut
from app.catalog.models import CatalogAlbum, CatalogTrack, split_ref
from app.matching.normalize import fingerprint, normalize_artist, normalize_title
from app.models import Album, DownloadItem, Track

ACTIVE_STATUSES = ("queued", "searching", "downloading", "tagging", "importing")


def star_only(query_json: dict[str, Any] | None) -> bool:
    """A download queued only for ♥ (`DownloadIn.star`): the song is starred after the import, and nobody asked to
    download it explicitly (`requested`). It is the Hub's own business: clients don't show it as a download."""
    qj = query_json or {}
    return bool(qj.get("star")) and not qj.get("requested")


def quiet(query_json: dict[str, Any] | None) -> bool:
    """A download nobody asked for explicitly: queued only for ♥ (`star_only`), by an import (`app.imports`), for
    a «Микс дня» (`app.mixes`) or a track of «Моя волна» played to its end (`app.mixes.wave`). Clients don't show it
    as a download."""
    qj = query_json or {}
    return star_only(qj) or (bool(qj.get("import") or qj.get("mix") or qj.get("wave")) and not qj.get("requested"))


def _duration_ok(a: int | None, b: int | None, tol: int = 3) -> bool:
    return a is None or b is None or abs(a - b) <= tol


async def find_existing(
    s: AsyncSession,
    *,
    artist: str,
    title: str,
    duration_s: int | None = None,
    isrc: str | None = None,
    deezer_track_id: str | None = None,
) -> Track | None:
    """The library track of this song; of several versions (a file picked next to an older one) the newest."""
    newest = Track.id.desc()
    if isrc:
        t = (await s.execute(
            select(Track).where(Track.isrc == isrc.upper()).order_by(newest).limit(1))).scalar_one_or_none()
        if t:
            return t
    if deezer_track_id:
        t = (
            await s.execute(
                select(Track).where(Track.deezer_track_id == str(deezer_track_id)).order_by(newest).limit(1))
        ).scalar_one_or_none()
        if t:
            return t
    fp = fingerprint(artist, title)
    for t in (await s.execute(select(Track).where(Track.fingerprint == fp).order_by(newest))).scalars():
        if _duration_ok(t.duration_s, duration_s):
            return t
    return None


def _dz_id(ref: str | None) -> str | None:
    if not ref:
        return None
    try:
        src, id_ = split_ref(ref)
    except ValueError:
        return None
    return id_ if src == "dz" else None


async def annotate_tracks(
    s: AsyncSession, tracks: Iterable[CatalogTrack], user_id: int | None = None
) -> list[TrackOut]:
    """Ownership and download state. A download queued only for ♥ is not «downloading»; for the user who liked the
    track any download that stars it is `star_pending` (the ♥ shows filled until the import stars the song)."""
    tracks = list(tracks)
    if not tracks:
        return []
    isrcs = {t.isrc.upper() for t in tracks if t.isrc}
    dz_ids = {t.id for t in tracks if t.source == "dz"}
    fps = {fingerprint(t.artist, t.title) for t in tracks}
    rows = (
        await s.execute(
            select(Track).where(
                or_(Track.isrc.in_(isrcs), Track.deezer_track_id.in_(dz_ids), Track.fingerprint.in_(fps))
            ).order_by(Track.id.desc())
        )
    ).scalars().all()
    # several versions of a song: the newest one wins, as in `find_existing`
    by_isrc = {r.isrc: r for r in reversed(rows) if r.isrc}
    by_dz = {r.deezer_track_id: r for r in reversed(rows) if r.deezer_track_id}
    by_fp: dict[str, list[Track]] = {}
    for r in rows:
        by_fp.setdefault(r.fingerprint, []).append(r)

    active = (
        await s.execute(
            select(DownloadItem.user_id, DownloadItem.query_json).where(DownloadItem.status.in_(ACTIVE_STATUSES))
        )
    ).all()
    downloading_refs: set[str | None] = set()
    downloading_fps: set[str] = set()
    star_refs: set[str | None] = set()
    for uid, q in active:
        q = q or {}
        ref = (q.get("meta") or {}).get("track_ref")
        if q.get("star") and not q.get("star_failed") and user_id is not None and uid == user_id:
            star_refs.add(ref)
        if quiet(q):
            continue
        downloading_refs.add(ref)
        if q.get("query"):
            downloading_fps.add(fingerprint(q["query"]["artist"], q["query"]["title"]))

    out: list[TrackOut] = []
    for t in tracks:
        hit = (t.isrc and by_isrc.get(t.isrc.upper())) or (t.source == "dz" and by_dz.get(t.id)) or None
        fp = fingerprint(t.artist, t.title)
        if hit is None:
            hit = next((r for r in by_fp.get(fp, []) if _duration_ok(r.duration_s, t.duration_s)), None)
        o = TrackOut(**t.model_dump())
        if hit is not None:
            o.owned = True
            o.library_track_id = hit.id
            o.nd_song_id = hit.nd_song_id
        else:
            o.downloading = t.ref in downloading_refs or fp in downloading_fps
            o.star_pending = t.ref in star_refs
        out.append(o)
    return out


def _album_key(artist: str, title: str) -> str:
    return f"{normalize_artist(artist).text}|{normalize_title(title).text}"


async def annotate_albums(
    s: AsyncSession, albums: Iterable[CatalogAlbum], user_id: int | None = None
) -> list[AlbumOut]:
    albums = list(albums)
    if not albums:
        return []
    dz_ids = {a.id for a in albums if a.source == "dz"}
    counts_dz = dict(
        (
            await s.execute(
                select(Album.deezer_album_id, func.count(Track.id))
                .join(Track, Track.album_id == Album.id)
                .where(Album.deezer_album_id.in_(dz_ids))
                .group_by(Album.deezer_album_id)
            )
        ).all()
    )
    # albums without a Deezer id (hitmoz catalog, own uploads): match by normalized artist + title
    lib_albums = (
        await s.execute(
            select(Album.album_artist, Album.title, func.count(Track.id))
            .join(Track, Track.album_id == Album.id)
            .group_by(Album.id)
        )
    ).all()
    counts_key: dict[str, int] = {}
    for artist, title, n in lib_albums:
        k = _album_key(artist, title)
        counts_key[k] = max(counts_key.get(k, 0), n)

    out: list[AlbumOut] = []
    for a in albums:
        d = a.model_dump(exclude={"tracks"})
        o = AlbumOut(**d)
        n = counts_dz.get(a.id, 0) if a.source == "dz" else 0
        if not n:
            n = counts_key.get(_album_key(a.artist, a.title), 0)
        o.owned_count = n
        total = a.nb_tracks or (len(a.tracks) if a.tracks else None)
        if n and total and n >= total:
            o.owned_status = "full"
        elif n:
            o.owned_status = "partial"
        if a.tracks is not None:
            o.tracks = await annotate_tracks(s, a.tracks, user_id)
            owned = sum(1 for t in o.tracks if t.owned)
            o.owned_count = owned
            o.owned_status = "full" if owned and owned >= len(o.tracks) else ("partial" if owned else "none")
        out.append(o)
    return out


__all__ = ["ACTIVE_STATUSES", "_dz_id", "annotate_albums", "annotate_tracks", "find_existing", "quiet", "star_only"]
