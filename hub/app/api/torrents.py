"""Releases from rutracker: search through Jackett, download with qBittorrent, import (README, «Раздачи с rutracker»)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    TorrentConfigOut,
    TorrentFilesOut,
    TorrentOut,
    TorrentReleaseOut,
    TorrentSearchOut,
    TorrentStartIn,
)
from app.auth import current_user
from app.config import get_settings
from app.db import get_session
from app.models import TorrentDownload, User
from app.torrents import core
from app.torrents.bencode import BencodeError
from app.torrents.jackett import JackettError, Release, merge_duplicates
from app.torrents.qbit import QbitError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/torrents", tags=["torrents"])

# formats the library can't take (`uploads.extensions`), whatever the files are called
UNSUPPORTED_CODECS = {"APE", "WavPack", "TTA", "DSD"}


def _allowed(user: User) -> bool:
    s = get_settings()
    return s.torrents_enabled and (user.is_admin or not s.cfg.torrents.admin_only)


def torrent_user(user: User = Depends(current_user)) -> User:
    if not get_settings().torrents_enabled:
        raise HTTPException(404, "Раздачи не настроены")
    if not _allowed(user):
        raise HTTPException(403, "Раздачи доступны только администратору")
    return user


def _upstream(e: Exception) -> HTTPException:
    if isinstance(e, core.TorrentsUnavailable):
        return HTTPException(503, str(e))
    if isinstance(e, BencodeError):
        return HTTPException(502, f"Трекер отдал не .torrent: {e}")
    return HTTPException(502, str(e))


def release_out(r: Release) -> TorrentReleaseOut:
    i = r.info
    importable = False if i.layout == "image" or i.codec in UNSUPPORTED_CODECS else True if i.codec else None
    return TorrentReleaseOut(
        title=r.title, link=r.link, magnet=r.magnet, tracker=r.tracker, details_url=r.details_url, size=r.size, seeders=r.seeders, leechers=r.leechers,
        grabs=r.grabs, files=r.files, published=r.published, codec=i.codec, bitrate_kbps=i.bitrate_kbps,
        lossless=i.lossless, hires=i.hires, layout=i.layout, year=i.year, discography=i.discography,
        importable=importable,
    )


@router.get("/config", response_model=TorrentConfigOut)
async def config(user: User = Depends(current_user)):
    s = get_settings()
    if s.torrent_search == "public":
        sub = "rutor, Knaben, The Pirate Bay"
    else:
        indexer = s.cfg.torrents.indexer
        sub = f"{'трекеры' if indexer == 'all' else indexer} через Jackett → qBittorrent"
    return TorrentConfigOut(enabled=_allowed(user), sub=sub)


@router.get("/search", response_model=TorrentSearchOut)
async def search(q: str = Query(min_length=1, max_length=300), user: User = Depends(torrent_user)):
    """Releases of the tracker for the query; seeded and importable ones first."""
    cfg = get_settings().cfg.torrents
    try:
        jackett, _ = core.clients()
        found = await jackett.search(q.strip(), cfg.categories)
    except (core.TorrentsUnavailable, JackettError) as e:
        return TorrentSearchOut(errors=[str(e)])
    out = [release_out(r) for r in merge_duplicates(found)]
    out.sort(key=lambda r: (r.importable is False, -(r.seeders or 0)))
    return TorrentSearchOut(releases=out[:cfg.max_results])


@router.get("/files", response_model=TorrentFilesOut)
async def files(link: str = Query(min_length=1, max_length=4096), title: str = Query("", max_length=1024),
                details_url: str | None = Query(None, max_length=1024), user: User = Depends(torrent_user)):
    """The files of a release and which of them the library takes: from its .torrent through Jackett, or for a magnet
    from peers through qBittorrent (`pending` until they come: ask again)."""
    try:
        meta, _ = await core.release_meta(link)
    except core.MetadataPending as e:
        return TorrentFilesOut(name="", size=0, pending=True, seeds=e.seeds)
    except core.SelectionError as e:
        raise HTTPException(400, str(e)) from e
    except (core.TorrentsUnavailable, JackettError, QbitError, BencodeError) as e:
        raise _upstream(e) from e
    listed = core.list_files(meta)
    await core.remember_listenable(meta, listed, link=link, title=title or meta.name, details_url=details_url)
    return TorrentFilesOut(name=meta.name, size=meta.size, files=[f.model_dump() for f in listed])


@router.post("", response_model=TorrentOut)
async def start(body: TorrentStartIn, user: User = Depends(torrent_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    try:
        row = await core.start_download(s, user.id, link=body.link, title=body.title, details_url=body.details_url,
                                        album_ref=body.album_ref, paths=body.files)
    except core.SelectionError as e:
        raise HTTPException(409 if "уже качается" in str(e) else 400, str(e)) from e
    except core.MetadataPending as e:
        raise HTTPException(409, f"{e}: подождите, пока появится список файлов") from e
    except (core.TorrentsUnavailable, JackettError, QbitError, BencodeError) as e:
        raise _upstream(e) from e
    await s.commit()
    core._publish(row)
    return core.torrent_out(row)


@router.get("", response_model=list[TorrentOut])
async def list_torrents(user: User = Depends(torrent_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.execute(select(TorrentDownload).where(TorrentDownload.user_id == user.id)
                            .order_by(TorrentDownload.id.desc()).limit(200))).scalars()
    return [core.torrent_out(r) for r in rows]


@router.delete("/{torrent_id}", response_model=dict[str, bool])
async def remove(torrent_id: int, delete_files: bool = True, user: User = Depends(torrent_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """Stop the download (or the seeding of an imported release) and remove it from qBittorrent. The library keeps
    what was imported."""
    row = await s.get(TorrentDownload, torrent_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Раздача не найдена")
    try:
        await core.cancel(s, row, delete_files=delete_files)
    except (core.TorrentsUnavailable, QbitError) as e:
        raise _upstream(e) from e
    await s.commit()
    return {"ok": True}
