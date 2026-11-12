"""qBittorrent Web API v2 client (4.x and 5.x): add a torrent stopped, choose its files, start, watch, remove."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.net import make_client

log = logging.getLogger(__name__)

# torrent states where every wanted file is complete (qBittorrent 4.x "paused…", 5.x "stopped…")
DONE_STATES = {"uploading", "stalledUP", "pausedUP", "stoppedUP", "queuedUP", "forcedUP"}
ERROR_STATES = {"error", "missingFiles"}


class QbitError(Exception):
    """qBittorrent is unreachable or refused the request."""


class QbitClient:
    def __init__(self, base_url: str, username: str, password: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        # the Web UI checks Referer/Origin against its host (CSRF protection)
        self._client = client or make_client(timeout=30, proxy_hosts=set(), http2=False,
                                             headers={"Referer": self.base_url, "Origin": self.base_url})
        self._login_lock = asyncio.Lock()
        self._logged_in = False

    async def _login(self) -> None:
        async with self._login_lock:
            if self._logged_in:
                return
            try:
                r = await self._client.post(f"{self.base_url}/api/v2/auth/login",
                                            data={"username": self.username, "password": self.password})
            except httpx.HTTPError as e:
                raise QbitError(f"qBittorrent недоступен: {e!r}") from e
            if r.status_code != 200 or r.text.strip() not in ("Ok.", ""):
                raise QbitError("qBittorrent не принял логин и пароль (QBIT_USERNAME / QBIT_PASSWORD)")
            self._logged_in = True

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        if self.password and not self._logged_in:
            await self._login()
        for attempt in (1, 2):
            try:
                r = await self._client.request(method, f"{self.base_url}/api/v2/{path}", **kw)
            except httpx.HTTPError as e:
                raise QbitError(f"qBittorrent недоступен: {e!r}") from e
            if r.status_code in (401, 403) and attempt == 1 and self.password:
                self._logged_in = False  # the session expired (qBittorrent restarted)
                await self._login()
                continue
            return r
        raise AssertionError("unreachable")

    async def _ok(self, method: str, path: str, **kw: Any) -> httpx.Response:
        r = await self._call(method, path, **kw)
        if r.status_code in (401, 403):
            raise QbitError("qBittorrent: нет доступа (логин/пароль или белый список подсетей Web UI)")
        if r.status_code != 200:
            raise QbitError(f"qBittorrent ответил {r.status_code} на {path}: {r.text[:200]}")
        return r

    async def version(self) -> str:
        return (await self._ok("GET", "app/version")).text.strip()

    async def add(self, torrent: bytes, *, save_path: str, category: str, tags: str = "") -> None:
        """Add stopped (files are chosen before the start). An already added torrent is left as it is."""
        data = {
            "savepath": save_path, "category": category, "tags": tags,
            "stopped": "true", "paused": "true",  # 5.x / 4.x
            "autoTMM": "false", "contentLayout": "Original", "root_folder": "true",
        }
        r = await self._call("POST", "torrents/add", data=data,
                             files={"torrents": ("release.torrent", torrent, "application/x-bittorrent")})
        if r.status_code == 409:  # 5.x: already there
            return
        if r.status_code != 200:
            raise QbitError(f"qBittorrent не добавил торрент: {r.status_code} {r.text[:200]}")
        if r.text.strip() == "Fails.":  # 4.x: a bad file or already there — the caller looks it up by hash
            log.info("qBittorrent answered Fails. to torrents/add")

    async def add_magnet(self, uri: str, *, save_path: str, category: str, tags: str = "") -> None:
        """Add a magnet that stops once its metadata (the file list) has come from peers: files are chosen after."""
        data = {
            "urls": uri, "savepath": save_path, "category": category, "tags": tags,
            "stopCondition": "MetadataReceived", "autoTMM": "false", "contentLayout": "Original",
        }
        r = await self._call("POST", "torrents/add", data=data)
        if r.status_code not in (200, 409):
            raise QbitError(f"qBittorrent не добавил magnet: {r.status_code} {r.text[:200]}")

    async def export(self, info_hash: str) -> bytes | None:
        """The .torrent of a torrent with metadata (qBittorrent 4.5+); None when there is none (yet)."""
        r = await self._call("GET", "torrents/export", params={"hash": info_hash})
        return r.content if r.status_code == 200 and r.content[:1] == b"d" else None

    async def piece_states(self, info_hash: str) -> list[int]:
        """Per piece: 0 not downloaded, 1 downloading, 2 downloaded."""
        return (await self._ok("GET", "torrents/pieceStates", params={"hash": info_hash})).json()

    async def set_sequential(self, info_hash: str, *, sequential: bool, first_last: bool | None = None) -> None:
        """Download in order (listening) or not; qBittorrent only toggles, so its state is read first."""
        t = await self.get(info_hash)
        if t is None:
            return
        if bool(t.get("seq_dl")) != sequential:
            await self._ok("POST", "torrents/toggleSequentialDownload", data={"hashes": info_hash})
        if first_last is not None and bool(t.get("f_l_piece_prio")) != first_last:
            await self._ok("POST", "torrents/toggleFirstLastPiecePrio", data={"hashes": info_hash})

    async def remove_tags(self, info_hash: str, tags: str) -> None:
        await self._ok("POST", "torrents/removeTags", data={"hashes": info_hash, "tags": tags})

    async def info(self, hashes: list[str] | None = None, category: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if hashes is not None:
            if not hashes:
                return []
            params["hashes"] = "|".join(hashes)
        if category is not None:
            params["category"] = category
        return (await self._ok("GET", "torrents/info", params=params)).json()

    async def get(self, info_hash: str) -> dict[str, Any] | None:
        rows = await self.info([info_hash])
        return rows[0] if rows else None

    async def files(self, info_hash: str) -> list[dict[str, Any]]:
        r = await self._call("GET", "torrents/files", params={"hash": info_hash})
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise QbitError(f"qBittorrent ответил {r.status_code} на torrents/files")
        rows = r.json()
        for i, f in enumerate(rows):
            f.setdefault("index", i)  # before 4.x's index field
        return rows

    async def set_priority(self, info_hash: str, ids: list[int], priority: int) -> None:
        if ids:
            await self._ok("POST", "torrents/filePrio",
                           data={"hash": info_hash, "id": "|".join(str(i) for i in ids), "priority": str(priority)})

    async def start(self, info_hash: str) -> None:
        r = await self._call("POST", "torrents/start", data={"hashes": info_hash})
        if r.status_code == 404:  # 4.x
            await self._ok("POST", "torrents/resume", data={"hashes": info_hash})
        elif r.status_code != 200:
            raise QbitError(f"qBittorrent ответил {r.status_code} на torrents/start")

    async def delete(self, info_hash: str, *, delete_files: bool) -> None:
        await self._ok("POST", "torrents/delete",
                       data={"hashes": info_hash, "deleteFiles": "true" if delete_files else "false"})

    async def aclose(self) -> None:
        await self._client.aclose()


def is_done(t: dict[str, Any]) -> bool:
    """All wanted files are downloaded (unwanted ones never are)."""
    return t.get("state") in DONE_STATES or (t.get("amount_left") == 0 and t.get("progress", 0) >= 1)
