"""Library edits (FR-5): rename albums, move/merge tracks, covers, delete to trash.

Files are retagged and relocated together. Navidrome keeps the song/album ids because its PIDs come from our
HOMEMUSIC_TRACK_ID / HOMEMUSIC_ALBUM_ID tags, which never change here (album uid changes only on merge/move,
and then the track is re-linked after the scan by path).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.library import tagger
from app.library.importer import _move, _write_cover_file, album_reldir, library_abs, library_lock, tagdata_for
from app.library.layout import track_filename, unique_path
from app.matching.normalize import fingerprint
from app.models import Album, Track

log = logging.getLogger(__name__)


def _remove_empty_dirs(start: Path) -> None:
    root = get_settings().library_dir.resolve()
    d = start
    while d.resolve() != root and d.is_dir():
        leftovers = [p for p in d.iterdir() if p.name not in ("cover.jpg", ".cover.jpg.part")]
        if leftovers:
            return
        shutil.rmtree(d, ignore_errors=True)
        d = d.parent


async def _album_cover(album: Album) -> bytes | None:
    p = library_abs(album_reldir(album)) / "cover.jpg"
    return await asyncio.to_thread(p.read_bytes) if p.is_file() else None


async def relocate_track(s: AsyncSession, track: Track, album: Album, *, cover: bytes | None = None,
                         retag: bool = True) -> None:
    """Write album/track fields into the file and move it to the album folder if the path changed."""
    old_abs = library_abs(track.path)
    track.album_id = album.id
    track.album = album.title
    track.album_artist = album.album_artist
    track.year = album.year
    track.fingerprint = fingerprint(track.artist, track.title)
    if retag and old_abs.is_file():
        await asyncio.to_thread(tagger.write_tags, old_abs, tagdata_for(track, album, cover=cover))
    ext = old_abs.suffix.lstrip(".") or "mp3"
    new_dir_rel = album_reldir(album)
    new_name = track_filename(track.disc_no, track.track_no, track.title, ext)
    new_rel = PurePosixPath(new_dir_rel, new_name).as_posix()
    if new_rel != track.path and old_abs.is_file():
        dest = library_abs(new_rel)
        if dest.exists():
            dest = unique_path(dest.parent, new_name)
        await asyncio.to_thread(_move, old_abs, dest)
        track.path = PurePosixPath(new_dir_rel, dest.name).as_posix()
        track.nd_song_id = None  # re-linked after the next scan
        await asyncio.to_thread(_remove_empty_dirs, old_abs.parent)


async def update_album(s: AsyncSession, album: Album, **fields) -> Album:
    async with library_lock:
        old_dir = library_abs(album_reldir(album))
        for k, v in fields.items():
            if v is not None:
                setattr(album, k, v)
        new_dir = library_abs(album_reldir(album))
        cover = await asyncio.to_thread((old_dir / "cover.jpg").read_bytes) if (old_dir / "cover.jpg").is_file() else None
        tracks = (await s.execute(select(Track).where(Track.album_id == album.id))).scalars().all()
        for t in tracks:
            await relocate_track(s, t, album, cover=cover)
        if cover and old_dir != new_dir:
            await asyncio.to_thread(_write_cover_file, new_dir, cover)
            await asyncio.to_thread(_remove_empty_dirs, old_dir)
        album.cover_path = (album_reldir(album) / "cover.jpg").as_posix() if cover else album.cover_path
        await s.flush()
        return album


async def set_album_cover(s: AsyncSession, album: Album, image: bytes) -> Album:
    cover = await asyncio.to_thread(tagger.prepare_cover, image)
    async with library_lock:
        d = library_abs(album_reldir(album))
        await asyncio.to_thread(_write_cover_file, d, cover)
        album.cover_path = (album_reldir(album) / "cover.jpg").as_posix()
        tracks = (await s.execute(select(Track).where(Track.album_id == album.id))).scalars().all()
        for t in tracks:
            p = library_abs(t.path)
            if p.is_file():
                await asyncio.to_thread(tagger.update_tags, p, cover=cover)
        await s.flush()
        return album


async def move_tracks(s: AsyncSession, track_ids: list[int], target: Album) -> list[Album]:
    """Move tracks into `target`; returns source albums that became empty (already deleted)."""
    async with library_lock:
        cover = await _album_cover(target)
        tracks = (await s.execute(select(Track).where(Track.id.in_(track_ids)))).scalars().all()
        sources = {t.album_id for t in tracks if t.album_id and t.album_id != target.id}
        for t in tracks:
            if t.album_id == target.id:
                continue
            await relocate_track(s, t, target, cover=cover)
        await s.flush()
        removed = []
        for aid in sources:
            n = (await s.execute(select(func.count(Track.id)).where(Track.album_id == aid))).scalar_one()
            if n == 0:
                a = await s.get(Album, aid)
                if a:
                    await asyncio.to_thread(_remove_empty_dirs, library_abs(album_reldir(a)))
                    removed.append(a)
                    await s.delete(a)
        await s.flush()
        return removed


async def merge_albums(s: AsyncSession, source_ids: list[int], target: Album) -> Album:
    ids = (await s.execute(select(Track.id).where(Track.album_id.in_([i for i in source_ids if i != target.id]))))
    await move_tracks(s, list(ids.scalars()), target)
    return target


async def update_track(s: AsyncSession, track: Track, **fields) -> Track:
    async with library_lock:
        for k, v in fields.items():
            if v is not None:
                setattr(track, k, v)
        album = await s.get(Album, track.album_id) if track.album_id else None
        p = library_abs(track.path)
        if album is None:
            if p.is_file():
                await asyncio.to_thread(tagger.update_tags, p, **{k: v for k, v in fields.items() if v is not None})
            await s.flush()
            return track
        await relocate_track(s, track, album, cover=await _album_cover(album))
        await s.flush()
        return track


async def trash_track(s: AsyncSession, track: Track) -> None:
    """Move the file to MUSIC_ROOT/.trash/<date>/<path> (purged after N days) and delete the row."""
    async with library_lock:
        src = library_abs(track.path)
        if src.is_file():
            dest = get_settings().trash_dir / datetime.now().strftime("%Y-%m-%d") / Path(*PurePosixPath(track.path).parts)
            await asyncio.to_thread(_move, src, dest)
            await asyncio.to_thread(_remove_empty_dirs, src.parent)
        album_id = track.album_id
        await s.delete(track)
        await s.flush()
        if album_id:
            n = (await s.execute(select(func.count(Track.id)).where(Track.album_id == album_id))).scalar_one()
            if n == 0:
                a = await s.get(Album, album_id)
                if a:
                    await s.delete(a)


def purge_trash(days: int) -> int:
    root = get_settings().trash_dir
    if not root.is_dir():
        return 0
    cutoff = datetime.now().timestamp() - days * 86400
    n = 0
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except OSError:
            continue
    return n


def file_size(path: Path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
