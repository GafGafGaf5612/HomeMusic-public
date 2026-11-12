"""Catalog tracks no source has a full file of: listening to them gives only Deezer's 30 s preview. «Микс дня»,
«Моя волна» and the smart shuffle leave them out (`app.mixes.build.keep_reason`, the wave's and the smart shuffle's
picks); a ♥ keeps such a track in «Понравившиеся» as before.

A track is marked when a pick of `app.listen` ends in the preview with every source answering (not `transient`), or a
download finds it in no source; a pick of a full file or the library's clears the mark. A mark lasts `KEEP_S`: then
the track gets another chance (sources gain tracks). The marks live in `catalog_cache` (`nofull:<ref>`) and in memory
(the Hub is one process): `load` at startup, `has` without I/O for the mix filters.
"""

from __future__ import annotations

import logging
import time

from app.catalog.cache import cache_delete, cache_prefix, cache_set

log = logging.getLogger(__name__)

KEEP_S = 14 * 86400
PREFIX = "nofull:"

_until: dict[str, float] = {}  # catalog ref -> wall time until which it has no full file


def _counts(ref: str | None) -> bool:
    return bool(ref) and ref.startswith("dz:")


def has(ref: str | None) -> bool:
    """`ref` is known to have only the preview."""
    until = _until.get(ref or "")
    if until is None:
        return False
    if until > time.time():
        return True
    _until.pop(ref, None)
    return False


async def mark(ref: str | None) -> None:
    if not _counts(ref):
        return
    if not has(ref):
        log.info("no full file of %s: left out of mixes and waves", ref)
    _until[ref] = time.time() + KEEP_S
    await cache_set(PREFIX + ref, True, KEEP_S)


async def clear(ref: str | None) -> None:
    if ref and _until.pop(ref, None) is not None:
        log.info("full file of %s found: back in mixes and waves", ref)
        await cache_delete(PREFIX + ref)


async def load() -> int:
    """The marks kept in the DB (app startup)."""
    rows = await cache_prefix(PREFIX)
    now = time.time()
    _until.clear()
    _until.update({k[len(PREFIX):]: until for k, until in rows.items() if until > now})
    return len(_until)


def reset() -> None:
    _until.clear()
