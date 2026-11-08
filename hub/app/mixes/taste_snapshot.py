"""Explicit inputs to taste math; local batch loading has no network or write side effects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def calendar_cutoff(now: datetime) -> datetime:
    try:
        return now.replace(year=now.year - 3)
    except ValueError:
        return now.replace(year=now.year - 3, day=28)


@dataclass(frozen=True)
class TasteEvent:
    user_id: int
    event_id: str
    ts: datetime
    kind: str
    ref: str | None = None
    nd_song_id: str | None = None
    target_type: str = "track"
    nd_album_id: str | None = None
    nd_artist_id: str | None = None
    target_data: dict[str, Any] | None = None
    reverts_event_id: str | None = None
    position_s: float | None = None
    duration_s: float | None = None
    context: str | None = None
    client: str = "pwa"
    received_at: datetime | None = None
    id: int = 0


@dataclass(frozen=True)
class Subject:
    target_type: str
    aliases: tuple[str, ...]
    ref: str | None = None
    nd_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrackMetadata:
    ref: str | None = None
    nd_song_id: str | None = None
    artist: str = ""
    title: str = ""
    duration_s: int | None = None
    album_ref: str | None = None
    album_nd_id: str | None = None


@dataclass(frozen=True)
class SavedDownload:
    ref: str | None
    nd_song_id: str | None
    at: datetime


@dataclass(frozen=True)
class DislikedTarget:
    ref: str | None = None
    nd_song_id: str | None = None
    artist_ref: str | None = None
    artist: str = ""
    title: str = ""
    at: datetime | None = None


@dataclass(frozen=True)
class TasteSnapshot:
    user_id: int
    events: tuple[TasteEvent, ...] = ()
    subjects: tuple[Subject, ...] = ()
    metadata: tuple[TrackMetadata, ...] = ()
    downloads: tuple[SavedDownload, ...] = ()
    dislikes: tuple[DislikedTarget, ...] = ()
    picked_artists: tuple[dict[str, Any], ...] = ()
    excluded_user_ids: frozenset[int] = frozenset()
    limitations: tuple[str, ...] = ()


async def load_snapshot(session, user_id: int, *, now: datetime) -> TasteSnapshot:
    """Read local inputs in batches, keeping permanent decisions outside the score window.

    Historical evaluation uses evaluate.read_snapshot instead: mutable current tables are
    suitable for an online read only. No profile cache or ORM mutation is introduced here.
    """
    from sqlalchemy import or_, select

    from app.favorites import AliasIndex, aliases
    from app.library.owned import quiet
    from app.models import Album, Dislike, DownloadItem, FavoriteSubject, MixSettings, PlayEvent, Track
    from app.plays import Ids

    now = utc(now)
    rows = list((await session.execute(select(PlayEvent).where(
        PlayEvent.user_id == user_id, PlayEvent.superseded_scrobble.is_(False), PlayEvent.ts <= now,
        PlayEvent.kind.not_in(("progress", "interrupted", "error")),
        or_(PlayEvent.ts >= calendar_cutoff(now),
            PlayEvent.kind.in_(("like", "unlike", "dislike", "undislike")))))).scalars())
    subjects = [Subject(x.target_type, tuple(x.aliases or ()), x.ref, x.nd_id, x.data or {})
                for x in (await session.execute(select(FavoriteSubject))).scalars()]
    downloads = []
    for item, nd in (await session.execute(select(DownloadItem, Track.nd_song_id).join(
            Track, Track.id == DownloadItem.track_id).where(DownloadItem.user_id == user_id,
                DownloadItem.status == "done", DownloadItem.created_at <= now))).all():
        if not quiet(item.query_json):
            downloads.append(SavedDownload((item.query_json.get("meta") or {}).get("track_ref"), nd,
                                           item.created_at))
    dislikes = tuple(DislikedTarget(x.ref, x.nd_song_id, x.artist_ref, x.artist or "", x.title or "", x.created_at)
        for x in (await session.execute(select(Dislike).where(Dislike.user_id == user_id,
                                                             Dislike.created_at <= now))).scalars())
    # Local verified pairs are metadata, not additional user events.
    ids = Ids(session)
    pairs = {(x.ref, x.nd_song_id) for x in rows if x.target_type == "track"}
    pairs.update((x.ref, x.nd_song_id) for x in (*downloads, *dislikes) if not getattr(x, "artist_ref", None))
    await ids.prime(pairs)
    for ref, nd in pairs:
        ref, nd = await ids.complete(ref, nd)
        if ref and nd:
            subjects.append(Subject("track", (f"ref:{ref}", f"nd:{nd}"), ref, nd))
    index = AliasIndex(rows, subjects)
    wanted = {index.key(e) for e in rows if aliases(e)}
    wanted.update(index.root(("track", alias)) for ref, nd in pairs for alias in
                  ((f"ref:{ref}",) if ref else (f"nd:{nd}",)) if ref or nd)
    subjects = [x for x in subjects if x.aliases and index.root((x.target_type, min(x.aliases))) in wanted]
    refs = {x.ref for x in rows if x.target_type == "track" and x.ref}
    songs = {x.nd_song_id for x in rows if x.target_type == "track" and x.nd_song_id}
    refs.update(ref for ref, nd in pairs if ref)
    songs.update(nd for ref, nd in pairs if nd)
    album_refs = {x.ref for x in rows if x.target_type == "album" and x.ref}
    for x in rows:
        if x.target_type == "album":
            refs.update((x.target_data or {}).get("track_refs") or ())
    for x in subjects:
        if x.target_type == "track":
            refs.update(a[4:] for a in x.aliases if a.startswith("ref:"))
            songs.update(a[3:] for a in x.aliases if a.startswith("nd:"))
        elif x.target_type == "album":
            if x.ref:
                album_refs.add(x.ref)
            refs.update(x.data.get("track_refs") or ())
    metadata = []
    query = select(Track, Album).outerjoin(Album, Album.id == Track.album_id).where(or_(
        Track.nd_song_id.in_(songs), Track.deezer_track_id.in_([r[3:] for r in refs if r.startswith("dz:")]),
        Album.deezer_album_id.in_([r[3:] for r in album_refs if r.startswith("dz:")]))).order_by(Track.id.desc())
    for tr, album in (await session.execute(query)).all():
        metadata.append(TrackMetadata(f"dz:{tr.deezer_track_id}" if tr.deezer_track_id else None,
            tr.nd_song_id, tr.artist, tr.title, tr.duration_s,
            f"dz:{album.deezer_album_id}" if album and album.deezer_album_id else None,
            None))  # Album.uid is a Hub id, not a verified Navidrome album id.
    settings = await session.get(MixSettings, user_id)
    fields = TasteEvent.__dataclass_fields__
    events = tuple(TasteEvent(**{k: getattr(x, k) for k in fields}) for x in rows)
    return TasteSnapshot(user_id, events, tuple(subjects), tuple(metadata), tuple(downloads), dislikes,
                         tuple(settings.artists_json or ()) if settings else ())
