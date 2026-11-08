"""Fakes and helpers shared by the Hub's tests (fixtures live in conftest.py)."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx

ND = "http://navidrome.test"


@asynccontextmanager
async def app_client(app: Any, login: tuple[str, str] | None = ("admin", "adminpw")) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client talking to the app in-process; signed in when `login` is given."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        if login:
            r = await client.post("/api/auth/login", json={"username": login[0], "password": login[1]})
            assert r.status_code == 200, r.text
        yield client


def cand(pid: str, url: str, *, title: str = "Группа крови", bitrate: int | None = 320, provider: str = "hitmoz"):
    """A found file of «Кино — Группа крови» (245 s), its page at https://<provider>.test/song/<pid>."""
    from app.providers.base import Candidate

    return Candidate(provider=provider, provider_id=pid, artist="Кино", title=title, duration_s=245,
                     bitrate_kbps=bitrate, page_url=f"https://{provider}.test/song/{pid}", download_url=url,
                     upload_date=date(2019, 5, 1))


class FakeProvider:
    """A download source that counts its calls.

    `cands` — what a search finds: a list, or a function of the query. `src` — the file a download gets;
    without it a download fails the test (listening must not download, unless `streams` is false). `explicit` —
    provider_id -> what the track page says about explicit lyrics. `down` — the site doesn't answer. `streams` —
    false: no plain HTTP audio (SoundCloud), listening downloads `src`.
    """

    def __init__(self, name: str = "hitmoz", cands: Sequence | Callable[[Any], Sequence] = (), *,
                 client: httpx.AsyncClient | None = None, src: Path | None = None,
                 explicit: dict[str, bool] | None = None, streams: bool = True):
        self.name = name
        self.cands = cands
        self.client = client
        self.src = src
        self.streams = streams
        self.explicit = dict(explicit or {})
        self.down = False
        self.searched = 0
        self.fetched = 0
        self.limits: list[int] = []
        self.enriched: list[str] = []

    async def search(self, q, limit=10):
        self.searched += 1
        self.limits.append(limit)
        if self.down:
            from app.providers.base import ProviderUnavailable

            raise ProviderUnavailable(f"{self.name}: ни один домен не отвечает")
        return list(self.cands(q) if callable(self.cands) else self.cands)

    async def enrich(self, c):
        self.enriched.append(c.provider_id)
        extra = {"explicit": self.explicit[c.provider_id]} if c.provider_id in self.explicit else {}
        return c.model_copy(update={"enriched": True, **extra})

    async def fetch(self, c, dest_dir, on_progress=None):
        if self.src is None:
            raise AssertionError("listening must not download")
        self.fetched += 1
        out = Path(dest_dir) / f"{self.name}_{c.provider_id}.mp3"
        shutil.copy(self.src, out)
        if on_progress:
            on_progress(1.0)
        return out

    async def stream(self, c):
        from app.providers.base import StreamSource

        return StreamSource(url=c.download_url or "", client=self.client or httpx.AsyncClient())

    async def aclose(self):
        pass


class FakeDeezer:
    """The catalog: one album «Кино — Группа крови» (dz:10) with one track (dz:100); other ids are not found.

    `preview` — the fresh preview link Deezer signs (None: the track has no preview). `more` — other catalog tracks
    a search finds by their title. `searched` counts track searches; `down` — the catalog does not answer.
    """

    def __init__(self, *, duration_s: int = 245, preview: str | None = "https://preview.test/p.mp3",
                 cover_url: str | None = None, more: Sequence[Any] = ()):
        from app.catalog.models import CatalogAlbum, CatalogTrack

        self.track = CatalogTrack(ref="dz:100", source="dz", id="100", title="Группа крови", artist="Кино",
                                  artists=["Кино"], album_ref="dz:10", album_title="Группа крови",
                                  duration_s=duration_s, track_no=1, disc_no=1, isrc="RUA1D8800001", explicit=False,
                                  cover_url=cover_url,
                                  preview_url="https://preview.test/old.mp3" if preview else None)
        self.album = CatalogAlbum(ref="dz:10", source="dz", id="10", title="Группа крови", artist="Кино",
                                  record_type="album", release_date=date(1988, 1, 1), year=1988,
                                  cover_url=cover_url, nb_tracks=1, tracks=[self.track])
        self.preview = preview
        self.previews = 0
        self.more = list(more)
        self.searched = 0
        self.down = False

    async def get_track(self, id_):
        if id_ != self.track.id:
            from app.catalog.deezer import CatalogNotFound

            raise CatalogNotFound(f"track/{id_}")
        return self.track

    async def get_album(self, id_):
        return self.album

    async def preview_url(self, id_):
        self.previews += 1
        return self.preview

    async def search_tracks(self, artist, title, limit=10):
        self.searched += 1
        if self.down:
            raise httpx.ConnectError("deezer is down")
        found = [self.track] if "крови" in title.lower() or "крови" in artist.lower() else []
        return found + [t for t in self.more if t.title.lower() in title.lower()]

    async def search_albums(self, artist, title, limit=5):
        return [self.album]

    async def search(self, q, kinds=("artist", "album", "track"), limit=10):
        from app.catalog.models import SearchResults

        return SearchResults(tracks=[self.track], albums=[self.album])

    def search_list(self):
        """31 results for paging: the real track, 29 more and a repeat of the 6th (Deezer lists a track twice)."""
        more = [self.track.model_copy(update={"ref": f"dz:{200 + i}", "id": str(200 + i), "title": f"Песня {i}",
                                              "isrc": None}) for i in range(29)]
        return [self.track, *more[:5], more[4], *more[5:]]

    async def search_track_page(self, q, offset=0, limit=25):
        items = self.search_list()
        page = items[offset:offset + limit]
        return page, (offset + len(page) if offset + len(page) < len(items) else None)

    # artists (mixes): «Кино» is dz:1 with dz:100 among its top tracks; `chart` — the chart's artists
    def artist(self, id_: str, name: str):
        from app.catalog.models import CatalogArtist

        return CatalogArtist(ref=f"dz:{id_}", source="dz", id=str(id_), name=name)

    async def get_artist(self, id_):
        if str(id_) != "1":
            from app.catalog.deezer import CatalogNotFound

            raise CatalogNotFound(f"artist/{id_}")
        return self.artist("1", "Кино")

    async def chart_artists(self, limit=30):
        return [self.artist("2", "Земфира"), self.artist("1", "Кино")]

    async def get_artist_top(self, id_, limit=10):
        return [self.track.model_copy(update={"artist_ref": "dz:1"})] if str(id_) == "1" else []

    async def aclose(self):
        pass
