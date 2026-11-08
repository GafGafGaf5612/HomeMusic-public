"""Sources of «similar» tracks for mixes (app/mixes/sources.py, resolve.py, embedder.py) on answers recorded from the
live services (tests/fixtures/mixes, respx; Yandex — a fake of its client library)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.mixes import sources
from app.mixes.sources import Hit, SourceDown
from tests.test_ytdlp import ytm_response, ytm_row

FIX = Path(__file__).parent / "fixtures" / "mixes"
YTM_SEARCH = "https://music.youtube.com/youtubei/v1/search"
YTM_NEXT = "https://music.youtube.com/youtubei/v1/next"
LASTFM = "https://ws.audioscrobbler.com/2.0/"
EMBEDDER = "http://embedder.test"


def load(name: str) -> Any:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
async def db(hub_env, db_file: Path, monkeypatch: pytest.MonkeyPatch):
    from app.db import dispose, init_engine

    monkeypatch.setenv("LASTFM_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDER_URL", EMBEDDER)
    monkeypatch.setattr(sources, "LASTFM_RATE", 0)
    from app.config import get_settings

    get_settings.cache_clear()
    init_engine(db_file)
    yield
    await dispose()


# --- YouTube Music radio ----------------------------------------------------------------------------------------


def test_radio_rows_parse_songs_and_videos() -> None:
    hits = [sources.radio_hit(r) for r in sources.radio_rows(load("ytm_next"))]
    assert [(h.artist, h.title, h.duration_s) for h in hits[:3]] == [
        ("Кино", "Группа крови", 285), ("Кино", "В наших глазах", 215), ("Molchat Doma", "Судно (Борис Рыжий)", 142)]
    # a music video row: its channel is the artist
    assert ("DOOM GENERATION", "Chernikovskaya Hata - Belaya Noch") in [(h.artist, h.title) for h in hits]
    assert hits[1].source_id == "Rng6M38as1A"


async def test_ytm_radio_finds_the_seed_and_leaves_it_out(db, respx_mock: respx.MockRouter) -> None:
    search = respx_mock.post(url__startswith=YTM_SEARCH).respond(json=ytm_response(
        ytm_row("other", "Кукушка", ["Кино"], "Чёрный альбом", "6:37"),
        ytm_row("xtxjm7ciwmc", "Группа крови", ["Кино"], "Группа крови", "4:45")))
    nxt = respx_mock.post(url__startswith=YTM_NEXT).respond(json=load("ytm_next"))

    hits = await sources.ytm_radio("Кино", "Группа крови")

    body = json.loads(nxt.calls.last.request.content)
    assert (body["videoId"], body["playlistId"]) == ("xtxjm7ciwmc", "RDAMVMxtxjm7ciwmc")
    assert body["context"]["client"]["clientName"] == "WEB_REMIX"
    assert len(hits) == 6 and ("Кино", "Группа крови") not in [(h.artist, h.title) for h in hits]
    # a week in catalog_cache: asked once
    assert await sources.ytm_radio("Кино", "Группа крови") == hits
    assert (search.call_count, nxt.call_count) == (1, 1)


async def test_ytm_radio_of_a_song_youtube_lacks_is_empty(db, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(url__startswith=YTM_SEARCH).respond(json=ytm_response(
        ytm_row("zzz", "Совсем другое", ["Кто-то"], None, "3:00")))
    nxt = respx_mock.post(url__startswith=YTM_NEXT)

    assert await sources.ytm_radio("Кино", "Группа крови") == []
    assert not nxt.called


async def test_ytm_radio_down_is_not_cached(db, respx_mock: respx.MockRouter) -> None:
    search = respx_mock.post(url__startswith=YTM_SEARCH).respond(503)

    for _ in range(2):
        with pytest.raises(SourceDown):
            await sources.ytm_radio("Кино", "Группа крови")
    assert search.call_count == 2


# --- Last.fm ------------------------------------------------------------------------------------------------------


async def test_lastfm_similar_tracks(db, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(LASTFM).respond(json=load("lastfm_track_similar"))

    hits = await sources.lastfm_similar_tracks("Кино", "Группа крови", limit=30)

    p = route.calls.last.request.url.params
    assert (p["method"], p["api_key"], p["artist"], p["track"], p["limit"]) == (
        "track.getSimilar", "test-key", "Кино", "Группа крови", "30")
    assert [(h.artist, h.title) for h in hits[:3]] == [
        ("Кино", "В наших глазах"), ("Кино", "Закрой за мной дверь, я ухожу"), ("Агата Кристи", "Как на войне")]
    assert hits[0].duration_s == 213


async def test_lastfm_unknown_track_is_empty_and_cached(db, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(LASTFM).respond(json=load("lastfm_not_found"))

    assert await sources.lastfm_similar_tracks("Нет", "Такого") == []
    assert await sources.lastfm_similar_tracks("Нет", "Такого") == []
    assert route.call_count == 1


async def test_lastfm_error_is_source_down(db, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(LASTFM).respond(json={"error": 10, "message": "Invalid API key"})

    with pytest.raises(SourceDown, match="Invalid API key"):
        await sources.lastfm_similar_tracks("Кино", "Группа крови")


async def test_lastfm_similar_artists_and_tags(db, respx_mock: respx.MockRouter) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        method = request.url.params["method"]
        return httpx.Response(200, json=load({"artist.getSimilar": "lastfm_artist_similar",
                                              "artist.getTopTags": "lastfm_artist_tags"}[method]))

    respx_mock.get(LASTFM).mock(side_effect=answer)

    assert (await sources.lastfm_similar_artists("Кино"))[:3] == ["Nautilus Pompilius", "ДДТ", "Виктор Цой"]
    assert await sources.lastfm_artist_tags("Кино") == ["russian rock", "post-punk", "rock", "russian", "80s"]


# --- Deezer -------------------------------------------------------------------------------------------------------


@pytest.fixture
async def dz(db):
    from app.catalog.deezer import DeezerClient
    from app.config import CatalogCfg

    client = DeezerClient(CatalogCfg(), client=httpx.AsyncClient())
    yield client
    await client._client.aclose()


async def test_deezer_radio_related_and_chart(dz, respx_mock: respx.MockRouter) -> None:
    from app.catalog.deezer import BASE_URL

    radio = respx_mock.get(f"{BASE_URL}/artist/4505880/radio").respond(json=load("deezer_artist_radio"))
    respx_mock.get(f"{BASE_URL}/artist/4505880/related").respond(json=load("deezer_artist_related"))
    respx_mock.get(f"{BASE_URL}/chart/0/artists").respond(json=load("deezer_chart_artists"))

    tracks = await dz.get_artist_radio("4505880")
    assert (tracks[0].ref, tracks[0].artist, tracks[0].title, tracks[0].artist_ref) == (
        "dz:448266002", "Кино", "Война", "dz:4505880")
    assert tracks[0].rank == 312485 and tracks[0].preview_url
    assert await dz.get_artist_radio("4505880") == tracks and radio.call_count == 1
    assert [a.name for a in await dz.get_artist_related("4505880")][:2] == ["Наутилус Помпилиус", "Земфира"]
    assert len(await dz.chart_artists(30)) == 3


# --- Yandex -------------------------------------------------------------------------------------------------------


def ym_track(id_: int, artist: str, title: str, seconds: int = 200) -> SimpleNamespace:
    return SimpleNamespace(id=id_, title=title, version=None, type="music", duration_ms=seconds * 1000,
                           artists=[SimpleNamespace(name=artist)])


class FakeYandex:
    """The `yandex-music` client: a search, «similar» of a track, «Моя волна» (`rotor_station_tracks`)."""

    def __init__(self, token: str | None = None, *, wave: list[list[SimpleNamespace]] | None = None,
                 wave_error: Exception | None = None):
        self.token = token
        self.wave = list(wave or [])
        self.wave_error = wave_error
        self.queues: list[str | None] = []

    def search(self, text: str, type_: str = "track"):
        found = [ym_track(38634621, "КИНО", "Группа крови", 285)] if "крови" in text.lower() else []
        return SimpleNamespace(tracks=SimpleNamespace(results=found))

    def tracks_similar(self, track_id: str):
        assert track_id == "38634621"
        return SimpleNamespace(similar_tracks=[ym_track(1, "MyTsoi", "Я знаю, кто я."), ym_track(2, "2 серия", "Прощай")])

    def rotor_station_tracks(self, station: str, queue: str | None = None):
        assert station == "user:onyourwave"
        if self.wave_error:
            raise self.wave_error
        self.queues.append(queue)
        batch = self.wave.pop(0) if self.wave else []
        return SimpleNamespace(sequence=[SimpleNamespace(track=t) for t in batch])


async def test_yandex_similar_without_token(db, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.catalog.yandex as ym

    monkeypatch.setattr(ym, "yandex_client", lambda token=None: FakeYandex(token))

    hits = await sources.yandex_similar("Кино", "Группа крови", 285)

    assert [(h.artist, h.title, h.source_id) for h in hits] == [("MyTsoi", "Я знаю, кто я.", "1"),
                                                                 ("2 серия", "Прощай", "2")]
    assert await sources.yandex_similar("Нет", "Такого") == []


async def test_yandex_wave_reads_batches_until_repeat(db, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.catalog.yandex as ym

    fake = FakeYandex("tok", wave=[[ym_track(5, "A", "One"), ym_track(6, "B", "Two")], [ym_track(7, "C", "Three")]])
    monkeypatch.setattr(ym, "yandex_client", lambda token=None: fake)

    hits = await sources.yandex_wave("tok", rounds=5)

    assert [h.title for h in hits] == ["One", "Two", "Three"] and fake.queues == [None, "6", "7"]


async def test_yandex_wave_refused_is_source_down(db, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.catalog.yandex as ym

    monkeypatch.setattr(ym, "yandex_client", lambda token=None: FakeYandex(token, wave_error=RuntimeError("451")))

    with pytest.raises(SourceDown):
        await sources.yandex_wave("tok")


# --- matching with the catalog ------------------------------------------------------------------------------------


async def test_resolve_takes_only_sure_matches_and_remembers(db) -> None:
    from app.catalog.models import CatalogTrack
    from app.mixes.resolve import resolve_hits
    from app.services import svc
    from tests.support import FakeDeezer

    other = CatalogTrack(ref="dz:300", source="dz", id="300", title="Война", artist="Кино", artists=["Кино"],
                         duration_s=240, rank=1000)
    fake = svc.deezer = FakeDeezer(more=[other])
    try:
        hits = [Hit("Кино", "Война", 240, "v1"), Hit("Кино", "Группа крови", 245, "v2"), Hit("Никто", "Ничего")]
        first = await resolve_hits("ytm", hits)
        searched = fake.searched
        again = await resolve_hits("ytm", hits)
    finally:
        svc.deezer = None
    assert [t.ref if t else None for t in first] == ["dz:300", "dz:100", None]
    assert [t.ref if t else None for t in again] == ["dz:300", "dz:100", None]
    # source_map remembers every verdict for everyone (a miss is asked again after a week): no second search
    assert searched == 3 and fake.searched == 3


async def test_resolve_looks_up_a_repeated_hit_once(db) -> None:
    """Two seeds' lists share a track: one lookup (in parallel they both inserted the same source_map row)."""
    from app.mixes.resolve import resolve_hits
    from app.services import svc
    from tests.support import FakeDeezer

    fake = svc.deezer = FakeDeezer()
    try:
        out = await resolve_hits("lastfm", [Hit("Кино", "Группа крови", 245), Hit("Кино", "Группа крови", 245)])
    finally:
        svc.deezer = None
    assert [t.ref for t in out] == ["dz:100", "dz:100"] and fake.searched == 1


async def test_resolve_prefers_the_library(db) -> None:
    import uuid

    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.mixes.resolve import resolve_hits
    from app.models import Track

    async with session_scope() as s:
        s.add(Track(uid=str(uuid.uuid4()), path="a/b.mp3", nd_song_id="ndOwn", artist="Своя группа", title="Песня",
                    duration_s=200, fingerprint=fingerprint("Своя группа", "Песня")))

    [t] = await resolve_hits("lastfm", [Hit("Своя группа", "Песня", 201)])

    assert (t.ref, t.source) == ("nd:ndOwn", "nd")


# --- the embedder ---------------------------------------------------------------------------------------------------


async def test_embedder_client(db, respx_mock: respx.MockRouter) -> None:
    from app.mixes.embedder import EmbedderClient

    embed = respx_mock.post(f"{EMBEDDER}/embed").respond(json={"queued": 1, "known": 1, "rejected": []})
    sim = respx_mock.post(f"{EMBEDDER}/similarity").respond(json={"model": "fake", "results": [
        {"key": "dz:1", "pos": 0.8, "neg": 0.2, "score": 0.7, "nearest": [], "nearest_neg": None}],
        "missing": ["dz:2"]})
    respx_mock.post(f"{EMBEDDER}/vectors").respond(json={"model": "fake", "dim": 2, "vectors": {"dz:1": [1, 0]},
                                                         "missing": []})

    async with EmbedderClient() as e:
        assert await e.embed([{"key": "dz:1", "url": "https://p.test/1.mp3"}, {"key": "nd:x", "path": "a/b.mp3"}]) == 1
        got = await e.similarity(["dz:1", "dz:2"], {"nd:x": 2.0, "nd:y": -5.0})
        vectors = await e.vectors(["dz:1"])

    assert json.loads(embed.calls.last.request.content)["items"][1] == {"key": "nd:x", "path": "a/b.mp3"}
    assert json.loads(sim.calls.last.request.content)["profile"] == [{"key": "nd:x", "weight": 2.0},
                                                                     {"key": "nd:y", "weight": -5.0}]
    assert (got.scores, got.missing, vectors) == ({"dz:1": 0.7}, {"dz:2"}, {"dz:1": [1, 0]})


async def test_embedder_down(db, respx_mock: respx.MockRouter) -> None:
    from app.mixes.embedder import EmbedderClient, EmbedderDown

    respx_mock.post(f"{EMBEDDER}/similarity").mock(side_effect=httpx.ConnectError("refused"))

    async with EmbedderClient() as e:
        with pytest.raises(EmbedderDown):
            await e.similarity(["dz:1"], {"nd:x": 1.0})
