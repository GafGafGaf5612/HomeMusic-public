"""Releases from rutracker: search (Jackett), download of the chosen files (qBittorrent), import into the library.

A release is a whole album or discography, so it is not a track source of `app.providers`: nothing to listen to
before the download, and the download takes minutes or hours. `torrents_loop` watches qBittorrent; a finished
release goes to the `torrent_import` job, which tags copies of its audio files (catalog album or the files' own
tags) and imports them like uploads. The originals stay in staging/torrents while qBittorrent seeds them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.jobs.queue import enqueue, handler, notify, on_final_failure
from app.library import probe, tagger
from app.library.importer import ImportSpec, cleanup_dir, import_file, staging_dir_for
from app.matching.normalize import normalize_title
from app.models import Job, TorrentDownload, utcnow
from app.services import svc
from app.torrents.bencode import TorrentFile, TorrentMeta
from app.torrents.jackett import JackettClient, MagnetRedirect, is_magnet, magnet_info_hash
from app.torrents.qbit import ERROR_STATES, QbitClient, is_done

log = logging.getLogger(__name__)

ACTIVE = ("downloading", "importing")
IMAGE_EXTS = {"jpg", "jpeg", "png"}
COVER_STEMS = ("cover", "folder", "front", "обложка")
ETA_UNKNOWN = 8640000  # qBittorrent's "∞"
FILES_WAIT_S = 10.0
# A magnet's file list comes from peers: qBittorrent fetches it in a torrent tagged PREVIEW_TAG that stops once it has
# the metadata. A request waits MAGNET_WAIT_S for it, then the PWA asks again; previews nobody downloaded are removed
# after PREVIEW_TTL_S.
PREVIEW_TAG = "homemusic-preview"
MAGNET_WAIT_S = 8.0
PREVIEW_TTL_S = 3600


class TorrentsUnavailable(Exception):
    """Jackett/qBittorrent are not configured (no JACKETT_API_KEY)."""


class SelectionError(ValueError):
    """The chosen files can't be downloaded and imported (text for the user)."""


class MetadataPending(Exception):
    """A magnet's file list hasn't come from peers yet."""

    def __init__(self, seeds: int | None):
        super().__init__("Список файлов ещё не пришёл от пиров")
        self.seeds = seeds


class TorrentFileOut(BaseModel):
    path: str
    size: int
    kind: str  # audio / cue / image / other
    importable: bool = False  # an audio file the library takes (not a CD image)
    image: bool = False  # one file of a whole disc next to a .cue: needs splitting, not imported yet
    ref: str | None = None  # listen before downloading (`app.torrents.stream`): a file a browser plays
    artist: str | None = None  # for the player: from the file name or the release title
    title: str | None = None


def clients() -> tuple[JackettClient, QbitClient]:
    if svc.jackett is None or svc.qbit is None:
        raise TorrentsUnavailable("Раздачи не настроены: нет JACKETT_API_KEY")
    return svc.jackett, svc.qbit


def _ext(path: str) -> str:
    name = PurePosixPath(path).name
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def audio_exts() -> set[str]:
    return {e.lower() for e in get_settings().cfg.uploads.extensions}


AUDIO_LIKE = {"ape", "wv", "tta", "dsf", "dff", "aiff", "aif", "alac", "wma", "mpc"}


def list_files(meta: TorrentMeta) -> list[TorrentFileOut]:
    """The release's files with what the Hub can do with each."""
    importable_exts = audio_exts()
    by_dir: dict[str, dict[str, int]] = {}
    for f in meta.files:
        d = str(PurePosixPath(f.path).parent)
        ext = _ext(f.path)
        kind = "audio" if ext in importable_exts | AUDIO_LIKE else "cue" if ext == "cue" else "image" if ext in IMAGE_EXTS else ""
        if kind in ("audio", "cue"):
            by_dir.setdefault(d, {"audio": 0, "cue": 0})[kind] += 1
    out = []
    for f in meta.files:
        ext = _ext(f.path)
        d = by_dir.get(str(PurePosixPath(f.path).parent), {"audio": 0, "cue": 0})
        if ext in importable_exts | AUDIO_LIKE:
            # a disc image: as many big audio files as .cue sheets in the folder (tracks+.cue has one .cue per disc)
            image = d["cue"] > 0 and d["audio"] <= d["cue"]
            out.append(TorrentFileOut(path=f.path, size=f.size, kind="audio", image=image,
                                      importable=ext in importable_exts and not image))
        elif ext == "cue":
            out.append(TorrentFileOut(path=f.path, size=f.size, kind="cue"))
        elif ext in IMAGE_EXTS:
            out.append(TorrentFileOut(path=f.path, size=f.size, kind="image"))
        else:
            out.append(TorrentFileOut(path=f.path, size=f.size, kind="other"))
    return out


def choose_files(files: list[TorrentFileOut], paths: list[str] | None) -> list[TorrentFileOut]:
    """The chosen paths (all importable audio when None), plus the cover images of their folders."""
    by_path = {f.path: f for f in files}
    if paths is None:
        chosen = [f for f in files if f.importable]
        if not chosen:
            if any(f.image for f in files):
                raise SelectionError("В раздаче образ диска + .cue: нарезка на треки пока не поддерживается")
            raise SelectionError("В раздаче нет файлов, которые библиотека принимает "
                                 f"({', '.join(sorted(audio_exts()))})")
    else:
        chosen = []
        for p in dict.fromkeys(paths):
            f = by_path.get(p)
            if f is None:
                raise SelectionError(f"Нет такого файла в раздаче: {p}")
            if not f.importable:
                raise SelectionError(f"Этот файл библиотека не примет: {PurePosixPath(p).name}"
                                     + (" (образ диска + .cue)" if f.image else ""))
            chosen.append(f)
        if not chosen:
            raise SelectionError("Не выбраны файлы")
    dirs = {str(PurePosixPath(f.path).parent) for f in chosen}
    covers = [f for f in files if f.kind == "image" and str(PurePosixPath(f.path).parent) in dirs
              and PurePosixPath(f.path).stem.lower() in COVER_STEMS]
    return chosen + covers


def torrent_out(row: TorrentDownload) -> dict[str, Any]:
    return {
        "id": row.id, "title": row.title, "details_url": row.details_url, "album_ref": row.album_ref,
        "status": row.status, "progress": row.progress, "state": row.state, "seeds": row.seeds, "speed": row.speed,
        "eta_s": row.eta_s, "size": row.size, "files": len([f for f in row.files_json if f.get("kind") == "audio"]),
        "imported": row.imported, "album_id": row.album_id, "error": row.error, "warnings": row.warnings,
        "created_at": row.created_at, "finished_at": row.finished_at,
    }


def _publish(row: TorrentDownload) -> None:
    bus.publish(row.user_id, "torrent", torrent_out(row))


async def _wait_files(qbit: QbitClient, info_hash: str) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FILES_WAIT_S
    while True:
        files = await qbit.files(info_hash)
        if files or loop.time() >= deadline:
            return files
        await asyncio.sleep(0.5)


def meta_from_qbit(info_hash: str, qfiles: list[dict[str, Any]]) -> TorrentMeta:
    """A torrent's metadata as qBittorrent lists it (a magnet's: there is no .torrent)."""
    files = [TorrentFile(f["name"], int(f["size"])) for f in sorted(qfiles, key=lambda f: f["index"])]
    return TorrentMeta(info_hash=info_hash, name=PurePosixPath(files[0].path).parts[0], files=files)


async def magnet_meta(qbit: QbitClient, uri: str, *, wait_s: float | None = None) -> TorrentMeta:
    """The file list of a magnet: added to qBittorrent as a preview on the first call, then waited for (peers)."""
    try:
        info_hash = magnet_info_hash(uri)
    except ValueError as e:
        raise SelectionError(f"Неверная magnet-ссылка: {e}") from e
    if await qbit.get(info_hash) is None:
        cfg = get_settings().cfg.torrents
        await qbit.add_magnet(uri, save_path=cfg.save_path, category=cfg.category, tags=f"homemusic,{PREVIEW_TAG}")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (MAGNET_WAIT_S if wait_s is None else wait_s)
    while True:
        qfiles = await qbit.files(info_hash)
        if qfiles:
            return meta_from_qbit(info_hash, qfiles)
        if loop.time() >= deadline:
            t = await qbit.get(info_hash)
            raise MetadataPending(t.get("num_seeds") if t else None)
        await asyncio.sleep(1)


async def release_meta(link: str, *, wait_s: float | None = None) -> tuple[TorrentMeta, bytes | None]:
    """The release's metadata and its .torrent (None for a magnet)."""
    from app.torrents.bencode import parse_torrent

    jackett, qbit = clients()
    if is_magnet(link):
        return await magnet_meta(qbit, link, wait_s=wait_s), None
    try:
        data = await jackett.fetch_torrent(link)
    except MagnetRedirect as e:  # a .torrent link of an indexer that only has magnets
        return await magnet_meta(qbit, e.uri, wait_s=wait_s), None
    return parse_torrent(data), data


async def add_preview(link: str) -> str:
    """Add the release to qBittorrent as a preview (listening, a magnet's file list): nothing wanted yet. Returns
    its info hash."""
    from app.torrents.bencode import parse_torrent

    jackett, qbit = clients()
    cfg = get_settings().cfg.torrents
    tags = f"homemusic,{PREVIEW_TAG}"
    if not is_magnet(link):
        try:
            data = await jackett.fetch_torrent(link)
        except MagnetRedirect as e:
            link = e.uri
        else:
            info_hash = parse_torrent(data).info_hash
            if await qbit.get(info_hash) is None:
                await qbit.add(data, save_path=cfg.save_path, category=cfg.category, tags=tags)
            return info_hash
    info_hash = magnet_info_hash(link)
    if await qbit.get(info_hash) is None:
        await qbit.add_magnet(link, save_path=cfg.save_path, category=cfg.category, tags=tags)
    return info_hash


async def remember_listenable(meta: TorrentMeta, files: list[TorrentFileOut], *, link: str, title: str,
                              details_url: str | None) -> None:
    """Give the release's playable files their listen refs (`app.torrents.stream`) and remember them."""
    from app import listen
    from app.torrents import stream

    cands = []
    for i, f in enumerate(files):
        if f.kind == "audio" and stream.media_type(f.path):
            c = stream.candidate(meta, i, link=link, release_title=title, details_url=details_url)
            f.ref, f.artist, f.title = stream.ref_for(meta.info_hash, i), c.artist, c.title
            cands.append(c)
    if cands:
        await listen.remember_files(cands)


async def start_download(s: AsyncSession, user_id: int, *, link: str, title: str, details_url: str | None,
                         album_ref: str | None, paths: list[str] | None) -> TorrentDownload:
    """Add the release (.torrent through Jackett, or a magnet whose files came) to qBittorrent with only the chosen
    files wanted, and start it."""
    _, qbit = clients()
    cfg = get_settings().cfg.torrents
    meta, data = await release_meta(link, wait_s=0)
    chosen = choose_files(list_files(meta), paths)
    rows = (await s.execute(select(TorrentDownload.status).where(
        TorrentDownload.info_hash == meta.info_hash, TorrentDownload.status.in_((*ACTIVE, "done"))))).scalars().all()
    if any(st in ACTIVE for st in rows):
        raise SelectionError("Эта раздача уже качается")

    if data is not None and await qbit.get(meta.info_hash) is None:
        await qbit.add(data, save_path=cfg.save_path, category=cfg.category, tags="homemusic")
    qfiles = await _wait_files(qbit, meta.info_hash)
    if not qfiles:
        raise SelectionError("qBittorrent не принял торрент")
    index = {f["name"]: f["index"] for f in qfiles}
    missing = [f.path for f in chosen if f.path not in index]
    if missing:
        raise SelectionError(f"qBittorrent не видит файл {missing[0]}")
    wanted = {index[f.path] for f in chosen}
    if not rows:  # nobody else needs files of it (a new torrent or a preview wants all): only the chosen ones stay
        await qbit.set_priority(meta.info_hash, sorted(set(index.values()) - wanted), 0)
    await qbit.set_priority(meta.info_hash, sorted(wanted), 1)
    await qbit.remove_tags(meta.info_hash, PREVIEW_TAG)  # downloaded now: not removed as a stale preview
    await qbit.set_sequential(meta.info_hash, sequential=False)  # a listened preview downloaded in order
    await qbit.start(meta.info_hash)

    row = TorrentDownload(
        user_id=user_id, info_hash=meta.info_hash, title=title[:1024] or meta.name, details_url=details_url,
        album_ref=album_ref, files_json=[{"path": f.path, "size": f.size, "kind": f.kind} for f in chosen],
        size=sum(f.size for f in chosen), status="downloading",
    )
    s.add(row)
    await s.flush()
    log.info("torrent %s: %s files of %s", meta.info_hash, len(chosen), meta.name, extra={"user_id": user_id})
    return row


async def cancel(s: AsyncSession, row: TorrentDownload, *, delete_files: bool) -> None:
    """Stop and forget the release in qBittorrent unless another download of the same release still needs it."""
    _, qbit = clients()
    others = (await s.execute(select(TorrentDownload.id).where(
        TorrentDownload.info_hash == row.info_hash, TorrentDownload.id != row.id,
        TorrentDownload.status.in_((*ACTIVE, "done"))))).first()
    if not others:
        await qbit.delete(row.info_hash, delete_files=delete_files)
        from app.torrents import stream

        stream.forget(row.info_hash)
    if row.status in ACTIVE:
        row.status = "cancelled"
        row.finished_at = utcnow()
    _publish(row)


# --------------------------------------------------------------------------- watching qBittorrent


async def poll_once() -> int:
    """Update the progress of downloading releases; finished ones go to the import. Returns how many finished."""
    if svc.qbit is None:
        return 0
    finished = 0
    async with session_scope() as s:
        rows = list((await s.execute(select(TorrentDownload).where(TorrentDownload.status == "downloading"))).scalars())
        if not rows:
            return 0
        infos = {t["hash"].lower(): t for t in await svc.qbit.info([r.info_hash for r in rows])}
        for row in rows:
            t = infos.get(row.info_hash)
            before = (row.status, round(row.progress, 3), row.state, row.seeds, row.speed)
            if t is None:
                row.status, row.error, row.finished_at = "failed", "Раздачу убрали из qBittorrent", utcnow()
            elif t.get("state") in ERROR_STATES:
                row.status, row.error, row.finished_at = "failed", f"qBittorrent: {t.get('state')}", utcnow()
            else:
                row.progress = float(t.get("progress") or 0)
                row.state = t.get("state")
                row.seeds = t.get("num_seeds")
                row.speed = t.get("dlspeed")
                eta = t.get("eta")
                row.eta_s = eta if isinstance(eta, int) and 0 <= eta < ETA_UNKNOWN else None
                if is_done(t):
                    row.status, row.progress, row.speed, row.eta_s = "importing", 1.0, 0, None
                    await enqueue(s, "torrent_import", {"id": row.id})
                    finished += 1
            if (row.status, round(row.progress, 3), row.state, row.seeds, row.speed) != before:
                _publish(row)
    if finished:
        notify()
    return finished


async def cleanup_previews(now: float | None = None) -> int:
    """Remove previews (a magnet's file list, files listened to before downloading) nobody downloaded: an hour
    after their last activity."""
    if svc.qbit is None:
        return 0
    import time

    now = time.time() if now is None else now
    async with session_scope() as s:
        known = set((await s.execute(select(TorrentDownload.info_hash).where(
            TorrentDownload.status != "cancelled"))).scalars())
    removed = 0
    for t in await svc.qbit.info(category=get_settings().cfg.torrents.category):
        tags = {x.strip() for x in (t.get("tags") or "").split(",")}
        h = t["hash"].lower()
        seen = max(t.get("added_on") or 0, t.get("last_activity") or 0) or now
        if PREVIEW_TAG in tags and h not in known and now - seen > PREVIEW_TTL_S:
            await svc.qbit.delete(h, delete_files=True)
            from app.torrents import stream

            stream.forget(h)
            removed += 1
    return removed


async def torrents_loop() -> None:
    last_cleanup = 0.0
    while True:
        try:
            await poll_once()
            if asyncio.get_running_loop().time() - last_cleanup > 600:
                last_cleanup = asyncio.get_running_loop().time()
                if n := await cleanup_previews():
                    log.info("removed %s stale magnet previews", n)
        except Exception as e:
            log.warning("torrents poll failed: %r", e)
        await asyncio.sleep(get_settings().cfg.torrents.poll_s)


# --------------------------------------------------------------------------- import

_LEADING_TAGS_RE = re.compile(r"^(?:\s*(?:\([^)]*\)|\[[^\]]*\]))+\s*")
_TRACK_FILE_RE = re.compile(r"^(?:(\d)[-.])?(\d{1,3})\s*[-._)]?\s+(.+)$")
_YEAR_TAIL_RE = re.compile(r"\s*[-–(\[]?\s*(?:19|20)\d\d.*$")


def release_artist_album(title: str) -> tuple[str | None, str | None]:
    """"(Rock) [CD] Кино - Группа крови - 1988, FLAC (tracks+.cue), lossless" -> ("Кино", "Группа крови")."""
    t = _LEADING_TAGS_RE.sub("", title.split(",")[0]).strip()
    parts = [p.strip() for p in t.split(" - ") if p.strip()]
    if len(parts) < 2:
        return (parts[0] if parts else None), None
    album = _YEAR_TAIL_RE.sub("", parts[1]).strip() or parts[1]
    return parts[0], album


def parse_filename(name: str) -> dict[str, Any]:
    """"01 - Title.flac", "1-02. Artist - Title.mp3" -> track_no, disc_no, title (and artist)."""
    stem = PurePosixPath(name).stem.strip()
    out: dict[str, Any] = {}
    if m := _TRACK_FILE_RE.match(stem):
        if m.group(1):
            out["disc_no"] = int(m.group(1))
        out["track_no"] = int(m.group(2))
        stem = m.group(3).strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        out["artist"], out["title"] = artist.strip(), title.strip()
    else:
        out["title"] = stem
    return out


def _match_catalog(tags: dict[str, Any], tracks: list[Any], index: int, n_files: int) -> Any | None:
    if not tracks:
        return None
    disc, no = tags.get("disc_no") or 1, tags.get("track_no")
    if no:
        hit = [t for t in tracks if t.track_no == no and (t.disc_no or 1) == disc]
        if len(hit) == 1:
            return hit[0]
    title = normalize_title(tags.get("title") or "").text
    if title:
        hit = [t for t in tracks if normalize_title(t.title).text == title]
        if len(hit) == 1:
            return hit[0]
    if n_files == len(tracks):
        return tracks[index]  # same count, files sorted by path: the album order
    return None


def _cover_files(root: Path, files: list[dict[str, Any]]) -> dict[str, bytes]:
    """{folder: its cover image} among the downloaded files."""
    out: dict[str, bytes] = {}
    for f in files:
        if f.get("kind") == "image":
            p = root / f["path"]
            d = str(PurePosixPath(f["path"]).parent)
            if d not in out and p.is_file() and p.stat().st_size < 20 * 1024 * 1024:
                out[d] = p.read_bytes()
    return out


def _in_album(tags: dict[str, Any], ct: Any | None, album: Any | None) -> bool:
    """The file belongs to the catalog album the search started from (a discography holds other albums too)."""
    if album is None:
        return False
    if ct is not None or not tags.get("album"):
        return True
    return normalize_title(tags["album"]).text == normalize_title(album.title).text


@handler("torrent_import")
async def handle_torrent_import(job: Job) -> None:
    from app.jobs.downloads import enqueue_scan, fetch_cover, get_catalog_album

    settings = get_settings()
    keep = settings.cfg.torrents.keep_seeding
    async with session_scope() as s:
        row = await s.get(TorrentDownload, int(job.payload_json["id"]))
        if row is None or row.status != "importing":
            return
        root = settings.torrents_dir
        audio = sorted((f for f in row.files_json if f.get("kind") == "audio"), key=lambda f: f["path"])
        absent = [f["path"] for f in audio if not (root / f["path"]).is_file()]
        if absent:  # not retried: the folders don't match (settings), not a passing failure
            row.status, row.finished_at = "failed", utcnow()
            row.error = (f"Нет файла {absent[0]} в staging/torrents: проверьте torrents.save_path "
                         "и папку загрузок qBittorrent (docker-compose.yml)")
            await s.commit()
            _publish(row)
            return
        album = None
        if row.album_ref:
            try:
                album = await get_catalog_album(row.album_ref)
            except Exception as e:
                log.warning("catalog album %s for torrent %s: %r", row.album_ref, row.id, e)
                row.warnings = [*row.warnings, "Каталог недоступен: теги взяты из файлов"]
        cat_cover = await fetch_cover(album.cover_url) if album else None
        dir_covers = await asyncio.to_thread(_cover_files, root, row.files_json)
        rel_artist, rel_album = release_artist_album(row.title)
        cat_tracks = (album.tracks or []) if album else []

        work = staging_dir_for("torrent_imports", row.id)
        imported, warnings = 0, list(row.warnings)
        try:
            for i, f in enumerate(audio):
                src = root / f["path"]
                name = PurePosixPath(f["path"]).name
                try:
                    tags = await asyncio.to_thread(tagger.read_tags, src)
                except Exception as e:
                    log.warning("tags of %s: %r", src, e)
                    tags = {}
                guess = parse_filename(name)
                for k in ("title", "track_no", "disc_no"):
                    tags[k] = tags.get(k) or guess.get(k)
                tags["artist"] = tags.get("artist") or guess.get("artist")
                in_album = _in_album(tags, None, album)
                ct = _match_catalog(tags, cat_tracks, i, len(audio)) if in_album else None
                file_cover = ((cat_cover if in_album else None) or dir_covers.get(str(PurePosixPath(f["path"]).parent))
                              or await asyncio.to_thread(tagger.read_cover, src))
                try:
                    info = await probe.probe(src)
                except Exception as e:
                    log.warning("probe failed for %s: %r", src, e)
                    info = None
                spec = _spec(tags, ct, album if in_album else None, rel_artist, rel_album, cover=file_cover, row=row,
                             info=info)
                dest = work / f"{i:04d}{src.suffix.lower()}"
                try:
                    await asyncio.to_thread(shutil.copyfile if keep else shutil.move, src, dest)
                    track = await import_file(s, dest, spec)
                except Exception as e:
                    log.exception("torrent %s: import of %s failed", row.id, name)
                    warnings.append(f"{name}: {str(e)[:200]}")
                    dest.unlink(missing_ok=True)
                    continue
                imported += 1
                row.imported, row.album_id = imported, track.album_id
                await s.commit()  # file by file: a big release doesn't hold the DB lock for minutes
        finally:
            cleanup_dir(work)
        row.warnings = warnings
        row.finished_at = utcnow()
        if imported:
            row.status = "done"
            await enqueue_scan(s)
        else:
            row.status, row.error = "failed", "Ни один файл не импортирован"
        await s.commit()
        notify()
        _publish(row)
        if imported:
            bus.publish(row.user_id, "library", {"kind": "imported", "album_id": row.album_id})
        if not keep and svc.qbit is not None:
            try:
                await svc.qbit.delete(row.info_hash, delete_files=True)
            except Exception as e:
                log.warning("removing torrent %s: %r", row.info_hash, e)


def _spec(tags: dict[str, Any], ct: Any | None, album: Any | None, rel_artist: str | None, rel_album: str | None,
          *, cover: bytes | None, row: TorrentDownload, info: Any | None) -> ImportSpec:
    if ct is not None and album is not None:  # the catalog's data, as a regular download tags its file
        return ImportSpec(
            artist=ct.artist, title=ct.title, artists=ct.artists or [ct.artist], album=album.title,
            album_artist=album.artist, year=album.year, release_date=album.release_date or ct.release_date,
            release_type=album.record_type, track_no=ct.track_no, disc_no=ct.disc_no or 1,
            track_total=album.nb_tracks or (len(album.tracks) if album.tracks else None), genre=album.genre,
            isrc=ct.isrc, explicit=ct.explicit, deezer_track_id=ct.id if ct.source == "dz" else None,
            deezer_album_id=album.id if album.source == "dz" else None, upc=album.upc, cover=cover,
            source="torrent", source_url=row.details_url, duration_s=_duration(info, ct.duration_s),
            bitrate_kbps=info.bitrate_kbps if info else None, codec=info.codec if info else None,
            added_by=row.user_id,
        )
    if album is not None:  # a track the catalog album lacks (a bonus): album fields from the catalog
        album_artist, album_title, year = album.artist, album.title, album.year or tags.get("year")
    else:
        album_artist = tags.get("album_artist") or rel_artist or tags.get("artist")
        album_title, year = tags.get("album") or rel_album or row.title[:200], tags.get("year")
    artist = tags.get("artist") or album_artist or "Неизвестный исполнитель"
    return ImportSpec(
        artist=artist, title=tags.get("title") or "Без названия", artists=tags.get("artists") or [],
        album=album_title, album_artist=album_artist or artist, year=year, track_no=tags.get("track_no"),
        track_total=tags.get("track_total"), disc_no=tags.get("disc_no") or 1, genre=tags.get("genre"),
        deezer_album_id=album.id if album is not None and album.source == "dz" else None,
        isrc=tags.get("isrc"), explicit=tags.get("explicit"), cover=cover, source="torrent",
        source_url=row.details_url, duration_s=_duration(info, tags.get("duration_s")),
        bitrate_kbps=info.bitrate_kbps if info else None, codec=info.codec if info else None, added_by=row.user_id,
    )


def _duration(info: Any | None, fallback: float | None) -> int | None:
    v = info.duration_s if info and info.duration_s else fallback
    return round(v) if v else None


@on_final_failure("torrent_import")
async def _import_failed(job: Job, exc: Exception) -> None:
    async with session_scope() as s:
        row = await s.get(TorrentDownload, int(job.payload_json["id"]))
        if row is not None and row.status == "importing":
            row.status, row.error, row.finished_at = "failed", str(exc)[:500], utcnow()
            await s.commit()
            _publish(row)
