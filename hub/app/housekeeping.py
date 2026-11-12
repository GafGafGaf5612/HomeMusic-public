"""Periodic maintenance: DB backup, trash/staging cleanup, cache purge, provider smoke test (NFR-5, NFR-6)."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

KEEP_BACKUPS = 7


def backup_hub_db() -> Path | None:
    s = get_settings()
    if not s.db_path.is_file():
        return None
    dest_dir = s.backups_dir / "hub"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"hub-{datetime.now():%Y%m%d}.sqlite3"
    tmp = dest_dir / ".hub-backup.part"
    # backup into the WSL2 filesystem first, then copy: SQLite file locking on the Windows mount is unreliable
    local_tmp = s.data_dir / "backup.tmp.sqlite3"
    src = sqlite3.connect(s.db_path)
    try:
        dst = sqlite3.connect(local_tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    tmp.write_bytes(local_tmp.read_bytes())
    tmp.replace(dest)
    local_tmp.unlink(missing_ok=True)
    for old in sorted(dest_dir.glob("hub-*.sqlite3"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


async def smoke_test_providers() -> dict[str, str]:
    """Search a well-known track on every provider; a parser that returns nothing means the layout changed."""
    from app.providers.base import TrackQuery
    from app.services import svc

    q = TrackQuery(artist="Кино", title="Группа крови")
    out: dict[str, str] = {}
    for name, p in svc.providers.items():
        if not getattr(p, "searchable", True):  # releases' files: nothing to search by artist and title
            continue
        try:
            res = await p.search(q, limit=5)
            out[name] = f"ok ({len(res)})" if res else "EMPTY — возможно, сменилась вёрстка"
        except Exception as e:
            out[name] = f"error: {e!r}"[:200]
    bad = {k: v for k, v in out.items() if not v.startswith("ok")}
    (log.warning if bad else log.info)("provider smoke test: %s", out)
    return out


async def run_daily() -> None:
    from app.accounts import purge_email_codes
    from app.api.uploads import purge_stale_uploads
    from app.catalog.cache import cache_purge_expired
    from app.library.editor import purge_trash

    cfg = get_settings().cfg
    try:
        path = await asyncio.to_thread(backup_hub_db)
        log.info("hub db backup: %s", path)
    except Exception:
        log.exception("hub db backup failed")
    try:
        n = await asyncio.to_thread(purge_trash, cfg.library.trash_days)
        m = await asyncio.to_thread(purge_stale_uploads)
        c = await cache_purge_expired()
        e = await purge_email_codes()
        log.info("housekeeping: trash dirs %s, stale uploads %s, cache rows %s, email codes %s removed", n, m, c, e)
    except Exception:
        log.exception("housekeeping failed")
    await smoke_test_providers()


async def scheduler_loop() -> None:
    """Runs `run_daily` at cfg.library.backup_hour local time every day."""
    hour = get_settings().cfg.library.backup_hour
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        await run_daily()
