"""The light variant of a track's audio for a metered network (`GET /api/listen/stream?q=mobile`).

A file above `HEAVY_KBPS` (mp3 320, flac, heavy aac) is re-encoded by ffmpeg to Ogg Opus at `LIGHT_KBPS` (VBR): about
half the bytes at a sound that is hard to tell apart. A file at or below the threshold is served as it is: encoding
lossy audio again only costs quality, and the file is light already.

Copies live on disk under staging (`LIGHT_DIR`), a file per source (named by a hash of what identifies the source
bytes), served like any file: with Content-Length and Range, so the phone can seek and cache. They are made ahead of
time (a mix's songs, a wave's tracks) and, failing that, on the first request. A request waits for a copy for a
moment only (`get`): a stream must start at once, so when the copy is not ready the original is served, the copy is
finished in the background, and that answer is flagged ("pending"). Bytes of the two variants must never meet in
one cache entry of a client, so for `STICKY_S` after such an answer the same source keeps getting the original.

ffmpeg runs a few at a time (`ENCODERS`), at a low CPU priority, and a background encode yields to one a user waits
for (`app.net.PrioritySemaphore`). The copies are kept least recently used out, up to `MAX_BYTES`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

from app.config import get_settings
from app.library.probe import ProbeError, ffmpeg_bin, probe
from app.net import Priority, PrioritySemaphore, in_background, priority_context

log = logging.getLogger(__name__)

LIGHT_DIR = "listen-light"  # under staging
LIGHT_KBPS = 160
HEAVY_KBPS = 192  # a file above this is re-encoded
MAX_BYTES = 2500 * 2**20
ENCODERS = 3  # ffmpeg processes at once; background work takes all but one, which a user's request may take
WAIT_S = 1.5  # a stream waits this long for a copy that is not ready, then serves the original
WAIT_AHEAD_S = 25.0  # a track loaded ahead (nobody waits for its first byte) waits longer
STICKY_S = 600  # after an original was served in place of a copy, the same source keeps getting it this long
FAILED_S = 1800  # a source ffmpeg failed on is not tried again this long
ENCODE_TIMEOUT_S = 300
SUFFIX = ".opus"
MEDIA_TYPE = "audio/ogg"

# What a request is answered with (header `X-HomeMusic-Variant`)
LIGHT = "light"  # the Opus copy
ORIGINAL = "original"  # the source as it is: it is light already (or the request was for the original)
PENDING = "original-pending"  # the copy is not ready (or failed): the original, flagged so a client does not cache it

_sem: PrioritySemaphore | None = None
_jobs: dict[str, tuple[asyncio.Task[Path], Priority]] = {}
_kbps: dict[str, int | None] = {}  # key -> probed bitrate
_served_original: dict[str, float] = {}  # key -> monotonic time an original was served in place of the copy
_failed: dict[str, float] = {}  # key -> monotonic time ffmpeg failed


def root() -> Path:
    return get_settings().staging_dir / LIGHT_DIR


def key_of(ident: str) -> str:
    """The copy's name: `ident` says which bytes the source is (a library file: path, size, mtime; a server copy:
    its ref and file)."""
    return hashlib.sha1(ident.encode()).hexdigest()[:24]


def copy_path(key: str) -> Path:
    return root() / (key + SUFFIX)


def is_heavy(kbps: int | None) -> bool:
    return kbps is not None and kbps > HEAVY_KBPS


async def bitrate(src: Path, key: str, hint: int | None = None) -> int | None:
    """The source's audio bitrate: the known one (the library's), else probed once. None: unreadable."""
    if hint:
        _kbps[key] = hint
    elif key not in _kbps:
        try:
            _kbps[key] = (await probe(src)).bitrate_kbps
        except ProbeError as e:
            log.info("light %s: not probed: %s", src.name, e)
            _kbps[key] = None
    return _kbps[key]


async def cached(ident: str) -> tuple[Path | None, str] | None:
    """The answer for a source without touching the source, when it is known already: the copy on disk, the verdict
    that the source is light as it is, or that an original was served in its place a moment ago. None: not known."""
    key = key_of(ident)
    if time.monotonic() - _served_original.get(key, -STICKY_S) < STICKY_S:
        return None, PENDING
    dest = copy_path(key)
    if await asyncio.to_thread(dest.is_file):
        await asyncio.to_thread(os.utime, dest)  # in use: pruned last
        return dest, LIGHT
    if key in _kbps and not is_heavy(_kbps[key]):
        return None, ORIGINAL
    return None


def served_original(ident: str) -> None:
    """An original is being served in place of the copy (not ready): see `STICKY_S`."""
    _served_original[key_of(ident)] = time.monotonic()


async def get(src: Path, ident: str, *, kbps: int | None = None, wait_s: float = WAIT_S) -> tuple[Path | None, str]:
    """The light copy of `src` for a request: (path, LIGHT); or (None, ORIGINAL) when the source is light enough as
    it is; or (None, PENDING) when the copy is not ready within `wait_s` (it is finished in the background) or could
    not be made."""
    if (hit := await cached(ident)) is not None:
        return hit
    key = key_of(ident)
    if not is_heavy(await bitrate(src, key, kbps)):
        return None, ORIGINAL
    if time.monotonic() - _failed.get(key, -FAILED_S) < FAILED_S:
        served_original(ident)
        return None, PENDING
    task, prio = _start(src, key)
    if prio.background and not in_background():
        prio.foreground()  # a request is waiting for it now
        if _sem is not None:
            await _sem.kick()
    done, _ = await asyncio.wait([task], timeout=wait_s)
    if done and not task.cancelled() and task.exception() is None:
        return copy_path(key), LIGHT
    served_original(ident)
    return None, PENDING


def ensure(src: Path, ident: str, *, kbps: int | None = None) -> asyncio.Task | None:
    """Makes the copy in the background if the source is heavy and there is none yet (a mix's songs, a wave's
    tracks). The task ends with the copy; None: nothing to make."""
    key = key_of(ident)
    if copy_path(key).is_file() or time.monotonic() - _failed.get(key, -FAILED_S) < FAILED_S:
        return None
    return asyncio.create_task(_ensure(src, key, kbps), context=priority_context(Priority(True)))


async def _ensure(src: Path, key: str, kbps: int | None) -> None:
    if not is_heavy(await bitrate(src, key, kbps)):
        return
    try:
        await _start(src, key)[0]
    except Exception:
        pass  # logged and remembered by the encode itself


def _start(src: Path, key: str) -> tuple[asyncio.Task[Path], Priority]:
    """The encode of this source: running already, or started now."""
    if (job := _jobs.get(key)) is not None:
        return job
    prio = Priority(in_background())
    task = asyncio.create_task(_encode(src, key), context=priority_context(prio))
    _jobs[key] = (task, prio)

    def done(t: asyncio.Task, _key: str = key) -> None:
        _jobs.pop(_key, None)
        if not t.cancelled():
            t.exception()  # retrieved here: nobody may be awaiting it any more

    task.add_done_callback(done)
    return _jobs[key]


async def _encode(src: Path, key: str) -> Path:
    global _sem
    if _sem is None:
        _sem = PrioritySemaphore(ENCODERS, reserve=1)
    dest = copy_path(key)
    part = dest.with_name(dest.name + ".part")
    async with _sem:
        if await asyncio.to_thread(dest.is_file):
            return dest
        t0 = time.monotonic()
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
        try:
            await _ffmpeg(src, part)
            await asyncio.to_thread(os.replace, part, dest)
        except BaseException as e:
            await asyncio.to_thread(part.unlink, missing_ok=True)
            if not isinstance(e, asyncio.CancelledError):
                _failed[key] = time.monotonic()
                log.warning("light %s: not encoded: %r", src.name, e)
            raise
        log.info("light %s: %d → %d KB (%.1f s)", src.name, src.stat().st_size // 1024, dest.stat().st_size // 1024,
                 time.monotonic() - t0)
    await asyncio.to_thread(prune, root(), MAX_BYTES, {k + SUFFIX for k in _jobs} | {dest.name})
    return dest


def ffmpeg_args(src: Path, out: Path) -> list[str]:
    """Audio only, no tags or cover; bit-exact so the same source gives the same bytes (a copy that was pruned and
    made again has the same size: a client that has half of it cached keeps reading the same file)."""
    args = [ffmpeg_bin(), "-hide_banner", "-nostats", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
            "-map", "0:a:0", "-vn", "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-c:a", "libopus", "-b:a", f"{LIGHT_KBPS}k", "-vbr", "on", "-application", "audio", "-f", "ogg", str(out)]
    nice = shutil.which("nice")
    return [nice, "-n", "10", *args] if nice else args


async def _ffmpeg(src: Path, out: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_args(src, out), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), ENCODE_TIMEOUT_S)
    except BaseException:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(err.decode("utf-8", "replace").strip()[-300:] or f"ffmpeg exited with {proc.returncode}")


def prune(folder: Path, max_bytes: int, busy: set[str] | None = None) -> None:
    """Least recently used copies go while all of them take more than `max_bytes`; leftovers of an interrupted
    encode go too. `busy`: file names to leave alone (being written, just made)."""
    if not folder.is_dir():
        return
    files: list[tuple[float, int, Path]] = []  # what may go
    total = 0
    for f in folder.iterdir():
        if not f.is_file():
            continue
        st = f.stat()
        if f.name.endswith(".part"):
            if f.name not in (busy or ()) and time.time() - st.st_mtime > 3600:
                f.unlink(missing_ok=True)
            continue
        total += st.st_size
        if f.name not in (busy or ()):
            files.append((st.st_mtime, st.st_size, f))
    for _, size, f in sorted(files, key=lambda x: x[0]):
        if total <= max_bytes:
            break
        f.unlink(missing_ok=True)
        total -= size


def reset() -> None:
    """Forget what is in memory and stop the encodes (app shutdown, tests)."""
    global _sem
    for task, _ in _jobs.values():
        task.cancel()
    _jobs.clear()
    _kbps.clear()
    _served_original.clear()
    _failed.clear()
    _sem = None
