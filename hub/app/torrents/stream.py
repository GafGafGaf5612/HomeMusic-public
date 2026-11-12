"""Listening to a release's file before downloading it.

A file of a release is a provider's file of `app.listen` (ref "file:torrent:<info hash>:<index>", remembered when the
PWA lists the release's files), so the player, the queue, seeks, the light variant and «Скачать» work as for any
source. Its "upstream" is this module: the listen client sends requests for `http://torrent.homemusic.internal/...`
to `TorrentTransport`, which has qBittorrent download the file in order (top priority, first and last pieces
early: some formats keep their index at the end) and answers the Range with the bytes whose pieces are done, waiting
for the next ones as they come. The torrent is a preview (tag `homemusic-preview`) until the release is downloaded:
removed with its files an hour after the last activity (`core.cleanup_previews`); only files asked for are wanted.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from app.config import get_settings
from app.providers.base import Candidate, StreamSource, file_ref
from app.torrents.bencode import BencodeError, TorrentMeta, parse_torrent

log = logging.getLogger(__name__)

PROVIDER = "torrent"
HOST = "torrent.homemusic.internal"
MAX_PRIO = 7
NO_PROGRESS_S = 90.0  # a stream that gets no new piece this long fails (the listen copy resumes it a few times)
META_WAIT_S = 60.0  # a magnet's file list, for a stream
POLL_S = 0.5
STATES_TTL_S = 0.4  # piece states shared by the streams of a torrent
READ_CHUNK = 256 * 1024
FOCUS_TTL_S = 30.0  # a file asked for again within this long is not re-prioritised
PLAYABLE = {
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac", "ogg": "audio/ogg",
    "opus": "audio/ogg", "wav": "audio/wav",
}
_URL_RE = re.compile(r"^/([0-9a-f]{40})/(\d+)$")

_meta: dict[str, TorrentMeta] = {}
_states: dict[str, tuple[float, list[int]]] = {}
_asked: dict[str, dict[int, float]] = {}  # info hash -> file index -> when a stream last asked for it
_prepare_locks: dict[str, asyncio.Lock] = {}


class StreamError(httpx.ConnectError):
    """The file can't be streamed (no qBittorrent, unknown release, no peers): the listen pipeline reports it."""

    def __init__(self, message: str):
        super().__init__(message)


def media_type(path: str) -> str | None:
    name = PurePosixPath(path).name
    return PLAYABLE.get(name.rsplit(".", 1)[-1].lower()) if "." in name else None


def ref_for(info_hash: str, index: int) -> str:
    return file_ref(PROVIDER, f"{info_hash}:{index}")


def file_url(info_hash: str, index: int) -> str:
    return f"http://{HOST}/{info_hash}/{index}"


def candidate(meta: TorrentMeta, index: int, *, link: str, release_title: str, details_url: str | None) -> Candidate:
    """The listen pipeline's file for a release's track: artist and title from the file name or the release title."""
    from app.torrents.core import parse_filename, release_artist_album

    f = meta.files[index]
    guess = parse_filename(PurePosixPath(f.path).name)
    rel_artist, rel_album = release_artist_album(release_title or meta.name)
    return Candidate(
        provider=PROVIDER, provider_id=f"{meta.info_hash}:{index}", artist=guess.get("artist") or rel_artist or "",
        title=guess.get("title") or PurePosixPath(f.path).stem, page_url=details_url or link, download_url=link,
        album=rel_album, enriched=True,
        extra={"path": f.path, "size": f.size, "release": release_title, "track_no": guess.get("track_no")},
    )


def _split(provider_id: str) -> tuple[str, int]:
    info_hash, _, index = provider_id.partition(":")
    return info_hash, int(index)


# --------------------------------------------------------------------------- qBittorrent side


async def _ensure_torrent(info_hash: str, index: int) -> TorrentMeta:
    """The release is in qBittorrent (added back as a preview if it was removed) and its metadata is known."""
    from app import listen
    from app.torrents import core

    _, qbit = core.clients()
    if await qbit.get(info_hash) is None:
        _meta.pop(info_hash, None)
        cand = await listen.file_candidate(ref_for(info_hash, index))
        if cand is None or not cand.download_url:
            raise StreamError("Раздача неизвестна: откройте её файлы ещё раз")
        try:
            await core.add_preview(cand.download_url)
        except Exception as e:
            raise StreamError(f"Не удалось добавить раздачу: {e}") from e
    meta = _meta.get(info_hash)
    if meta is not None:
        return meta
    deadline = time.monotonic() + META_WAIT_S
    while not await qbit.files(info_hash):
        if time.monotonic() >= deadline:
            raise StreamError("Список файлов раздачи не пришёл от пиров")
        await asyncio.sleep(1)
    data = await qbit.export(info_hash)
    try:
        meta = parse_torrent(data) if data else None
    except BencodeError:
        meta = None
    if meta is None or not meta.piece_length:
        meta = await _meta_from_files(qbit, info_hash)
    _meta[info_hash] = meta
    return meta


async def _meta_from_files(qbit: Any, info_hash: str) -> TorrentMeta:
    """Without `torrents/export` (old qBittorrent): offsets from the file sizes (no padding files assumed)."""
    from app.torrents.bencode import TorrentFile

    rows = sorted(await qbit.files(info_hash), key=lambda f: f["index"])
    props = (await qbit._ok("GET", "torrents/properties", params={"hash": info_hash})).json()
    files, offset = [], 0
    for f in rows:
        files.append(TorrentFile(f["name"], int(f["size"]), offset))
        offset += int(f["size"])
    return TorrentMeta(info_hash, PurePosixPath(files[0].path).parts[0], files, int(props.get("piece_size") or 0))


def neighbours(meta: TorrentMeta, index: int) -> list[int]:
    """Other files sharing the first or last piece of this one. Such a piece is wanted for this file, but a web seed
    (archive.org) is not asked for the part of a file nobody wants, so the piece never completes: they are wanted
    too, at the lowest priority."""
    plen = meta.piece_length
    f = meta.files[index]
    if not plen or not f.size:
        return []
    ends = {f.offset // plen, (f.offset + f.size - 1) // plen}
    out = []
    for j, g in enumerate(meta.files):
        if j != index and g.size and any(g.offset // plen <= p <= (g.offset + g.size - 1) // plen for p in ends):
            out.append(j)
    return out


async def _focus(info_hash: str, index: int, meta: TorrentMeta) -> None:
    """This file first, in order. In a preview only the files streams asked for (and their `neighbours`) are wanted,
    the one asked last at the top; a release being downloaded keeps its own choice and only gets this file on top."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import TorrentDownload
    from app.torrents import core

    asked = _asked.setdefault(info_hash, {})
    now = time.monotonic()
    if now - asked.get(index, -FOCUS_TTL_S) < FOCUS_TTL_S:
        asked[index] = now
        return
    asked[index] = now
    _, qbit = core.clients()
    async with session_scope() as s:
        downloading = (await s.execute(select(TorrentDownload.id).where(
            TorrentDownload.info_hash == info_hash,
            TorrentDownload.status.in_((*core.ACTIVE, "done"))))).first() is not None
    near = {j for i in asked for j in neighbours(meta, i)} - set(asked)
    rows = await qbit.files(info_hash)
    if not downloading:
        idle = [f["index"] for f in rows if f["index"] not in asked and f["index"] not in near and f.get("priority", 1) > 0]
        await qbit.set_priority(info_hash, idle, 0)
        await qbit.set_priority(info_hash, sorted({i for i in asked if i != index} | near), 1)
    else:  # the download's choice stays; a neighbour it doesn't want is added at the lowest priority
        prio = {f["index"]: f.get("priority", 1) for f in rows}
        await qbit.set_priority(info_hash, sorted(j for j in neighbours(meta, index) if prio.get(j, 0) == 0), 1)
    await qbit.set_priority(info_hash, [index], MAX_PRIO)
    await qbit.set_sequential(info_hash, sequential=True, first_last=True)
    await qbit.start(info_hash)


async def prepare(info_hash: str, index: int) -> tuple[TorrentMeta, int]:
    lock = _prepare_locks.setdefault(info_hash, asyncio.Lock())
    async with lock:
        meta = await _ensure_torrent(info_hash, index)
        if not 0 <= index < len(meta.files):
            raise StreamError("В раздаче нет такого файла")
        await _focus(info_hash, index, meta)
    return meta, index


async def _piece_states(info_hash: str) -> list[int]:
    from app.torrents import core

    hit = _states.get(info_hash)
    if hit and time.monotonic() - hit[0] < STATES_TTL_S:
        return hit[1]
    _, qbit = core.clients()
    states = await qbit.piece_states(info_hash)
    _states[info_hash] = (time.monotonic(), states)
    return states


def _disk_path(path: str) -> Path | None:
    root = get_settings().torrents_dir
    for p in (root / path, root / f"{path}.!qB"):  # qBittorrent may name an incomplete file "<name>.!qB"
        if p.is_file():
            return p
    return None


def _read(path: Path, start: int, length: int) -> bytes:
    with path.open("rb") as f:
        f.seek(start)
        return f.read(length)


async def read_range(meta: TorrentMeta, index: int, start: int, end: int):
    """Bytes start..end (inclusive) of the file, each as soon as its piece is downloaded."""
    f = meta.files[index]
    plen = meta.piece_length
    pos = start
    last = time.monotonic()
    while pos <= end:
        states = await _piece_states(meta.info_hash)
        first = (f.offset + pos) // plen
        stop = (f.offset + end) // plen
        p = first
        while p <= stop and p < len(states) and states[p] == 2:
            p += 1
        disk = _disk_path(f.path) if p > first else None
        if disk is None:
            if time.monotonic() - last > NO_PROGRESS_S:
                raise httpx.ReadTimeout("Раздача не отдаёт файл: нет данных от пиров")
            await asyncio.sleep(POLL_S)
            continue
        upto = min(end, p * plen - 1 - f.offset)  # the last byte of the file in the done pieces
        while pos <= upto:
            n = min(READ_CHUNK, upto - pos + 1)
            data = await asyncio.to_thread(_read, disk, pos, n)
            if not data:  # the file on disk is shorter than its done pieces say: look again
                break
            pos += len(data)
            last = time.monotonic()
            yield data


# --------------------------------------------------------------------------- the listen client's transport

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int, bool] | None:
    """(start, end inclusive, partial) for a Range header; None when unsatisfiable."""
    if not header:
        return 0, size - 1, False
    m = _RANGE_RE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return 0, size - 1, False
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:
        start, end = max(0, size - int(m.group(2))), size - 1
    if start >= size or start > end:
        return None
    return start, end, True


class _Body(httpx.AsyncByteStream):
    def __init__(self, gen):
        self._gen = gen

    async def __aiter__(self):
        async for chunk in self._gen:
            yield chunk

    async def aclose(self) -> None:
        await self._gen.aclose()


class TorrentTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        m = _URL_RE.match(request.url.path)
        if not m:
            return httpx.Response(404, request=request)
        meta, index = await prepare(m.group(1), int(m.group(2)))
        f = meta.files[index]
        rng = parse_range(request.headers.get("range"), f.size)
        ctype = media_type(f.path) or "application/octet-stream"
        if rng is None:
            return httpx.Response(416, headers={"Content-Range": f"bytes */{f.size}"}, request=request)
        start, end, partial = rng
        headers = {"Content-Type": ctype, "Content-Length": str(end - start + 1), "Accept-Ranges": "bytes"}
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{f.size}"
        return httpx.Response(206 if partial else 200, headers=headers, stream=_Body(read_range(meta, index, start, end)),
                              request=request)


class TorrentProvider:
    """The listen pipeline's source for releases' files (`file:torrent:…` refs): not a search source."""

    name = PROVIDER
    streams = True
    searchable = False  # not in the provider search or its smoke test: it finds nothing by artist and title

    def __init__(self) -> None:
        self._client = httpx.AsyncClient()  # never sends: `app.listen` uses its own client with the transport

    async def search(self, q: Any, limit: int = 10) -> list[Candidate]:
        return []

    async def enrich(self, c: Candidate) -> Candidate:
        return c

    async def stream(self, c: Candidate) -> StreamSource:
        info_hash, index = _split(c.provider_id)
        return StreamSource(url=file_url(info_hash, index), client=self._client,
                            media_type=media_type(str(c.extra.get("path", ""))) or "audio/mpeg")

    async def fetch(self, c: Candidate, dest_dir: Path, on_progress: Any = None) -> Path:
        """The whole file (a download of one track from the player): read as its pieces come."""
        info_hash, index = _split(c.provider_id)
        meta, index = await prepare(info_hash, index)
        f = meta.files[index]
        dest = dest_dir / f"torrent{PurePosixPath(f.path).suffix.lower()}"
        done = 0
        with dest.open("wb") as out:
            async for chunk in read_range(meta, index, 0, f.size - 1):
                await asyncio.to_thread(out.write, chunk)
                done += len(chunk)
                if on_progress is not None and f.size:
                    on_progress(done / f.size)
        return dest

    async def aclose(self) -> None:
        await self._client.aclose()


def mounts() -> dict[str, httpx.AsyncBaseTransport]:
    """For the listen client: requests for releases' files go to qBittorrent (when the feature is on)."""
    from app.services import svc

    return {f"all://{HOST}": TorrentTransport()} if svc.qbit is not None else {}


def forget(info_hash: str) -> None:
    """The torrent was removed from qBittorrent: its cached metadata and states go too."""
    _meta.pop(info_hash, None)
    _states.pop(info_hash, None)
    _asked.pop(info_hash, None)
