"""MusicBrainzClient tests on fixtures recorded from the live API (respx, no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.catalog.musicbrainz import BASE_URL, MusicBrainzClient, MusicBrainzError
from app.config import CatalogCfg
from app.db import dispose, init_engine

pytestmark = pytest.mark.respx(assert_all_called=False)

FIX = Path(__file__).parent / "fixtures" / "musicbrainz"
KINO_ID = "064db6e8-fdfb-4acb-a327-fc2de75b37de"


def load(name: str) -> Any:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
async def db(db_file: Path):
    init_engine(db_file)
    yield
    await dispose()


@pytest.fixture
async def mb(db):
    http = httpx.AsyncClient()
    client = MusicBrainzClient(CatalogCfg(musicbrainz_user_agent="HomeMusicTest/1.0 (test)"), client=http)
    yield client
    await http.aclose()


async def test_search_recordings_maps_and_caches(mb: MusicBrainzClient, respx_mock: respx.Router):
    route = respx_mock.get(f"{BASE_URL}/recording").respond(json=load("recording_kino"))

    tracks = await mb.search_recordings("Кино", "Группа крови")

    req = route.calls[0].request
    assert req.headers["User-Agent"] == "HomeMusicTest/1.0 (test)"
    assert req.url.params["fmt"] == "json" and req.url.params["limit"] == "5"
    assert req.url.params["query"] == 'recording:"Группа крови" AND artist:"Кино"'

    assert len(tracks) == 5 and all(t.source == "mb" and t.title == "Группа крови" for t in tracks)
    t = tracks[0]
    assert (t.ref, t.id) == ("mb:cd8cfc02-31af-4b53-9410-50f852ec88bb", "cd8cfc02-31af-4b53-9410-50f852ec88bb")
    assert (t.artist, t.artists, t.artist_ref) == ("Кино", ["Кино"], f"mb:{KINO_ID}")
    assert t.duration_s == 292  # 292280 ms
    assert (t.album_title, t.album_artist, t.track_no, t.disc_no) == ("Последний герой", "Кино", 7, 1)
    assert t.album_ref == "mb:41c1d649-2275-4f72-935a-33c88e6e53a5"
    assert t.release_date == date(2023, 1, 16)
    assert tracks[1].isrc == "FRX871523721" and tracks[1].release_date is None  # first-release-date "2008"

    again = await mb.search_recordings("Кино", "Группа  крови")  # normalized cache key
    assert route.call_count == 1 and again == tracks


async def test_search_artists(mb: MusicBrainzClient, respx_mock: respx.Router):
    route = respx_mock.get(f"{BASE_URL}/artist").respond(json=load("artist_kino"))
    artists = await mb.search_artists("Кино")
    assert artists[0].ref == f"mb:{KINO_ID}" and artists[0].name == "Кино" and artists[0].source == "mb"
    assert len(artists) == 5
    await mb.search_artists("Кино")
    assert route.call_count == 1


async def test_http_error_raises(mb: MusicBrainzClient, respx_mock: respx.Router):
    respx_mock.get(f"{BASE_URL}/artist").respond(400, json={"error": "bad query"})
    with pytest.raises(MusicBrainzError):
        await mb.search_artists("Кино")
