"""Subsonic/OpenSubsonic API client for Navidrome (token auth: t = md5(password + salt))."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

API_VERSION = "1.16.1"
CLIENT_NAME = "homemusic-hub"


class SubsonicError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"subsonic error {code}: {message}")
        self.code = code
        self.message = message


class AuthError(SubsonicError):
    """Wrong username or password (code 40/41)."""


class NavidromeClient:
    def __init__(self, username: str, password: str, base_url: str | None = None, client: httpx.AsyncClient | None = None):
        self.username = username
        self._password = password
        self.base_url = (base_url or get_settings().navidrome_url).rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        self._own_client = client is None

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def __aenter__(self) -> NavidromeClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def _auth_params(self) -> dict[str, str]:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self._password + salt).encode("utf-8")).hexdigest()
        return {"u": self.username, "t": token, "s": salt, "v": API_VERSION, "c": CLIENT_NAME, "f": "json"}

    async def call(self, endpoint: str, params: dict[str, Any] | list[tuple[str, Any]] | None = None,
                   *, form: bool = False) -> dict:
        """GET (or form POST) /rest/<endpoint>; list params repeat keys (songId=1&songId=2)."""
        items: list[tuple[str, Any]] = list(self._auth_params().items())
        if isinstance(params, dict):
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, (list, tuple)):
                    items.extend((k, x) for x in v)
                else:
                    items.append((k, v))
        elif params:
            items.extend(params)
        if form:
            r = await self._client.post(f"{self.base_url}/rest/{endpoint}", content=urlencode(items),
                                        headers={"Content-Type": "application/x-www-form-urlencoded"})
        else:
            r = await self._client.get(f"{self.base_url}/rest/{endpoint}", params=items)
        r.raise_for_status()
        body = r.json().get("subsonic-response", {})
        if body.get("status") != "ok":
            err = body.get("error", {})
            code, msg = int(err.get("code", 0)), err.get("message", "unknown error")
            if code in (40, 41):
                raise AuthError(code, msg)
            raise SubsonicError(code, msg)
        return body

    # --- system -----------------------------------------------------------------
    async def ping(self) -> bool:
        await self.call("ping")
        return True

    async def get_user(self, username: str | None = None) -> dict:
        body = await self.call("getUser", {"username": username or self.username})
        return body.get("user", {})

    async def start_scan(self, full: bool = False) -> dict:
        body = await self.call("startScan", {"fullScan": "true" if full else None})
        return body.get("scanStatus", {})

    async def get_scan_status(self) -> dict:
        body = await self.call("getScanStatus")
        return body.get("scanStatus", {})

    async def scan_and_wait(self, timeout_s: float = 600, poll_s: float = 1.0) -> dict:
        """Start a scan (or join a running one) and wait until it finishes."""
        status = await self.get_scan_status()
        if not status.get("scanning"):
            status = await self.start_scan()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        # a just-started scan may report scanning=false for a moment; give it a grace period
        await asyncio.sleep(poll_s)
        while loop.time() < deadline:
            status = await self.get_scan_status()
            if not status.get("scanning"):
                return status
            await asyncio.sleep(poll_s)
        raise TimeoutError("Navidrome scan did not finish in time")

    # --- browsing / search ----------------------------------------------------------
    async def search3(
        self, query: str, *, song_count: int = 20, album_count: int = 0, artist_count: int = 0, song_offset: int = 0
    ) -> dict:
        body = await self.call(
            "search3",
            {
                "query": query,
                "songCount": song_count,
                "albumCount": album_count,
                "artistCount": artist_count,
                "songOffset": song_offset,
            },
        )
        return body.get("searchResult3", {})

    async def get_song(self, song_id: str) -> dict:
        return (await self.call("getSong", {"id": song_id})).get("song", {})

    async def get_artist(self, artist_id: str) -> dict:
        return (await self.call("getArtist", {"id": artist_id})).get("artist", {})

    async def get_album(self, album_id: str) -> dict:
        return (await self.call("getAlbum", {"id": album_id})).get("album", {})

    async def get_artists(self) -> list[dict]:
        """All artists (the alphabetical index flattened)."""
        index = (await self.call("getArtists")).get("artists", {}).get("index", [])
        return [a for group in index for a in group.get("artist", [])]

    async def get_album_list2(self, type_: str = "newest", size: int = 50, offset: int = 0) -> list[dict]:
        body = await self.call("getAlbumList2", {"type": type_, "size": size, "offset": offset})
        return body.get("albumList2", {}).get("album", [])

    async def get_starred2(self) -> dict:
        body = await self.call("getStarred2")
        if not isinstance(body.get("starred2"), dict):
            raise SubsonicError(0, "Incomplete getStarred2 response")
        return body["starred2"]

    # --- playlists ------------------------------------------------------------------
    async def get_playlists(self) -> list[dict]:
        return (await self.call("getPlaylists")).get("playlists", {}).get("playlist", [])

    async def get_playlist(self, playlist_id: str) -> dict:
        return (await self.call("getPlaylist", {"id": playlist_id})).get("playlist", {})

    async def create_playlist(self, name: str, song_ids: list[str] | None = None) -> dict:
        body = await self.call("createPlaylist", {"name": name, "songId": song_ids or None})
        return body.get("playlist", {})

    async def update_playlist(
        self,
        playlist_id: str,
        *,
        name: str | None = None,
        comment: str | None = None,
        add: list[str] | None = None,
        remove_indexes: list[int] | None = None,
    ) -> None:
        await self.call(
            "updatePlaylist",
            {
                "playlistId": playlist_id,
                "name": name,
                "comment": comment,
                "songIdToAdd": add or None,
                "songIndexToRemove": remove_indexes or None,
            },
        )

    async def set_playlist_songs(self, playlist_id: str, song_ids: list[str], chunk: int = 300) -> None:
        """Replace a playlist's songs, in this order: createPlaylist with playlistId replaces them all; the rest is
        added in chunks, so a long playlist does not make a too long URL."""
        await self.call("createPlaylist", {"playlistId": playlist_id, "songId": song_ids[:chunk] or None})
        for i in range(chunk, len(song_ids), chunk):
            await self.update_playlist(playlist_id, add=song_ids[i:i + chunk])

    async def delete_playlist(self, playlist_id: str) -> None:
        await self.call("deletePlaylist", {"id": playlist_id})

    async def replace_playlist_songs(self, playlist_id: str, song_ids: list[str]) -> None:
        """Replace in one request: form POST avoids URL limits and partial replacement between chunks."""
        await self.call("createPlaylist", {"playlistId": playlist_id, "songId": song_ids or None}, form=True)

    # --- annotations ----------------------------------------------------------------
    async def star(self, *, song_ids: list[str] = (), album_ids: list[str] = (), artist_ids: list[str] = ()) -> None:
        await self.call("star", {"id": list(song_ids) or None, "albumId": list(album_ids) or None,
                                 "artistId": list(artist_ids) or None})

    async def unstar(self, *, song_ids: list[str] = (), album_ids: list[str] = (), artist_ids: list[str] = ()) -> None:
        await self.call("unstar", {"id": list(song_ids) or None, "albumId": list(album_ids) or None,
                                   "artistId": list(artist_ids) or None})

    # --- media ---------------------------------------------------------------------
    def cover_art_url_params(self, cover_id: str, size: int | None = None) -> tuple[str, list[tuple[str, Any]]]:
        params = list(self._auth_params().items()) + [("id", cover_id)]
        if size:
            params.append(("size", size))
        return f"{self.base_url}/rest/getCoverArt", params

    def stream_url_params(self, song_id: str, max_bitrate: int | None = None) -> tuple[str, list[tuple[str, Any]]]:
        params = list(self._auth_params().items()) + [("id", song_id)]
        if max_bitrate:
            params.append(("maxBitRate", max_bitrate))
        return f"{self.base_url}/rest/stream", params

    @property
    def http(self) -> httpx.AsyncClient:
        return self._client


def admin_client() -> NavidromeClient:
    s = get_settings()
    return NavidromeClient(s.navidrome_admin_user, s.navidrome_admin_password)


class NavidromeUserExists(Exception):
    """Navidrome already has a user with this name."""


class NavidromeAdmin:
    """Navidrome's own REST API (/auth/login → JWT, /api/user) as the admin: the Subsonic API cannot create users."""

    _token: str | None = None
    _token_at: float = 0

    def __init__(self, client: httpx.AsyncClient | None = None):
        s = get_settings()
        self.base_url = s.navidrome_url.rstrip("/")
        self._user, self._password = s.navidrome_admin_user, s.navidrome_admin_password
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        self._own_client = client is None

    async def __aenter__(self) -> NavidromeAdmin:
        return self

    async def __aexit__(self, *exc) -> None:
        if self._own_client:
            await self._client.aclose()

    async def _jwt(self, fresh: bool = False) -> str:
        cls = NavidromeAdmin
        # Navidrome's tokens live 48 h; renew well before, and its login endpoint is rate-limited
        if fresh or cls._token is None or time.monotonic() - cls._token_at > 3600:
            r = await self._client.post(f"{self.base_url}/auth/login",
                                        json={"username": self._user, "password": self._password})
            if r.status_code == 401:
                raise AuthError(40, "admin login failed")
            r.raise_for_status()
            cls._token, cls._token_at = r.json()["token"], time.monotonic()
        return cls._token

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        for fresh in (False, True):
            headers = {"X-ND-Authorization": f"Bearer {await self._jwt(fresh)}"}
            r = await self._client.request(method, f"{self.base_url}{path}", headers=headers, **kw)
            if r.status_code != 401:
                return r
        return r

    async def list_users(self) -> list[dict]:
        r = await self._request("GET", "/api/user", params={"_start": 0, "_end": 1000})
        r.raise_for_status()
        return r.json()

    async def create_user(self, username: str, password: str, name: str = "") -> str:
        """Creates a regular (non-admin) user; returns its Navidrome id."""
        r = await self._request("POST", "/api/user", json={
            "userName": username, "name": name or username, "email": "", "password": password, "isAdmin": False,
        })
        if r.status_code == 400 and "userName" in (r.json().get("errors") or {}):
            raise NavidromeUserExists(username)
        r.raise_for_status()
        return r.json()["id"]

    @classmethod
    def forget_token(cls) -> None:
        cls._token = None


class NavidromeHistory:
    """A user's own scrobble history from Navidrome's REST API (`GET /api/scrobble`, 0.59+; the Subsonic API has
    none): `{id, mediaFileId, submissionTime}` rows, `mediaFileId` being the Subsonic song id. Signs in as the user
    (`/auth/login`, rate-limited by Navidrome: tokens are kept per user for a day)."""

    _tokens: dict[str, tuple[str, str, float]] = {}  # username -> (JWT, Navidrome user id, when)
    TOKEN_TTL_S = 86400  # Navidrome's tokens live 48 h

    def __init__(self, username: str, password: str, client: httpx.AsyncClient | None = None):
        self.base_url = get_settings().navidrome_url.rstrip("/")
        self.username, self._password = username, password
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        self._own_client = client is None

    async def __aenter__(self) -> NavidromeHistory:
        return self

    async def __aexit__(self, *exc) -> None:
        if self._own_client:
            await self._client.aclose()

    async def _login(self, fresh: bool = False) -> tuple[str, str]:
        cached = NavidromeHistory._tokens.get(self.username)
        if fresh or cached is None or time.monotonic() - cached[2] > self.TOKEN_TTL_S:
            r = await self._client.post(f"{self.base_url}/auth/login",
                                        json={"username": self.username, "password": self._password})
            if r.status_code == 401:
                raise AuthError(40, "login failed")
            r.raise_for_status()
            body = r.json()
            cached = (body["token"], str(body["id"]), time.monotonic())
            NavidromeHistory._tokens[self.username] = cached
        return cached[0], cached[1]

    async def scrobbles_after(self, last_id: int, page: int = 200) -> list[dict]:
        """The user's scrobbles with an id above `last_id`, oldest first (read newest first, page by page)."""
        out: list[dict] = []
        start = 0
        while True:
            for fresh in (False, True):
                token, nd_user_id = await self._login(fresh)
                r = await self._client.get(
                    f"{self.base_url}/api/scrobble",
                    params={"user_id": nd_user_id, "_sort": "id", "_order": "DESC", "_start": start,
                            "_end": start + page},
                    headers={"X-ND-Authorization": f"Bearer {token}"})
                if r.status_code != 401:
                    break
            r.raise_for_status()
            rows = r.json()
            fresh_rows = [x for x in rows if int(x.get("id", 0)) > last_id]
            out += fresh_rows
            if len(rows) < page or len(fresh_rows) < len(rows):
                break
            start += page
        return sorted(out, key=lambda x: int(x["id"]))

    @classmethod
    def forget_tokens(cls) -> None:
        cls._tokens.clear()
