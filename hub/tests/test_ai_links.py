import copy
import json
from pathlib import Path

import httpx
import pytest
import respx
from yandex_music import Album, Playlist, Track
from yandex_music.exceptions import BadRequestError, NotFoundError

import app.ai.links as links
from app.ai.links import (
    LinkFetchError,
    UnsupportedLink,
    clean_yt_title,
    extract_from_link,
    find_url,
    parse_yandex_path,
    spotify_extraction,
    spotify_next_data,
    yandex_album_extraction,
    yandex_playlist_extraction,
    yandex_track_item,
    youtube_entry_item,
    youtube_info_extraction,
    youtube_target,
)

FIX = Path(__file__).parent / "fixtures" / "links"


def load(name: str) -> dict | list:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ym_client():
    return links._yandex_client()  # no network: the client is not init()-ed


# --------------------------------------------------------------------------- URL parsing / dispatch


@pytest.mark.parametrize(("path", "expected"), [
    ("/users/yandexmusic/playlists/1636", ("user_playlist", ("yandexmusic", "1636"))),
    ("/users/music-blog/playlists/2131/", ("user_playlist", ("music-blog", "2131"))),
    ("/playlists/ar.2e74f130-a3f6-481b-82e1-bc04c03d8354", ("playlist", ("ar.2e74f130-a3f6-481b-82e1-bc04c03d8354",))),
    ("/playlists/lk.0d8a1b2c-3d4e-4f50-8a1b-2c3d4e5f6a7b", ("playlist", ("lk.0d8a1b2c-3d4e-4f50-8a1b-2c3d4e5f6a7b",))),
    ("/playlists/ddf290f9-f730-1769-8450-1e76eb97a515", ("playlist", ("ddf290f9-f730-1769-8450-1e76eb97a515",))),
    ("/album/10100", ("album", ("10100",))),
    ("/album/10100/track/38634621", ("track", ("38634621",))),
    ("/track/38634621", ("track", ("38634621",))),
])
def test_parse_yandex_path(path: str, expected: tuple) -> None:
    assert parse_yandex_path(path) == expected


@pytest.mark.parametrize("path", ["/artist/3121", "/", "/users/x/playlists", "/playlists/not-a-uuid", "/album/abc"])
def test_parse_yandex_path_unsupported(path: str) -> None:
    with pytest.raises(UnsupportedLink):
        parse_yandex_path(path)


@pytest.mark.parametrize(("url", "expected"), [
    ("https://www.youtube.com/playlist?list=PL123", ("playlist", "https://www.youtube.com/playlist?list=PL123")),
    ("https://music.youtube.com/playlist?list=OLAK5uy_x", ("playlist", "https://www.youtube.com/playlist?list=OLAK5uy_x")),
    ("https://www.youtube.com/watch?v=abc&list=PL9", ("playlist", "https://www.youtube.com/playlist?list=PL9")),
    ("https://www.youtube.com/watch?v=abc&list=RDabc", ("video", "https://www.youtube.com/watch?v=abc")),
    ("https://youtu.be/abc?si=x", ("video", "https://www.youtube.com/watch?v=abc")),
    ("https://m.youtube.com/watch?v=abc", ("video", "https://www.youtube.com/watch?v=abc")),
    ("https://www.youtube.com/shorts/abc", ("video", "https://www.youtube.com/watch?v=abc")),
    ("https://music.youtube.com/browse/MPREb_OLmD8O5IYNS", ("album", "https://music.youtube.com/browse/MPREb_OLmD8O5IYNS")),
])
def test_youtube_target(url: str, expected: tuple) -> None:
    assert youtube_target(url) == expected


def test_youtube_target_unsupported() -> None:
    with pytest.raises(UnsupportedLink):
        youtube_target("https://www.youtube.com/@kino")


def test_find_url() -> None:
    assert find_url("глянь https://music.yandex.ru/album/10100, классный") == "https://music.yandex.ru/album/10100"
    assert find_url("spotify:album:2noRn2Aes5aoNVsU6iWThc") == "spotify:album:2noRn2Aes5aoNVsU6iWThc"
    assert find_url("no links") is None


async def test_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []

    def fake_yandex(kind, ids):
        seen.append(("yandex", kind, ids))
        return links.Extraction(source_app="yandex_music")

    def fake_ytdlp(url):
        seen.append(("yt", url))
        return load("youtube_playlist.json")

    monkeypatch.setattr(links, "_yandex_fetch", fake_yandex)
    monkeypatch.setattr(links, "_ytdlp_extract", fake_ytdlp)

    await extract_from_link("https://music.yandex.ru/album/10100?utm_source=x")
    await extract_from_link("music.yandex.by/users/yandexmusic/playlists/1636")
    await extract_from_link("Плейлист: https://music.yandex.com/playlists/ar.2e74f130-a3f6-481b-82e1-bc04c03d8354")
    ex = await extract_from_link("https://www.youtube.com/playlist?list=PLiwqEJs7pw6KwZx-h1eLRkYUVWaprllgs")
    assert ex.source_app == "youtube"
    assert seen == [
        ("yandex", "album", ("10100",)),
        ("yandex", "user_playlist", ("yandexmusic", "1636")),
        ("yandex", "playlist", ("ar.2e74f130-a3f6-481b-82e1-bc04c03d8354",)),
        ("yt", "https://www.youtube.com/playlist?list=PLiwqEJs7pw6KwZx-h1eLRkYUVWaprllgs"),
    ]
    for bad in ("https://example.com/x", "https://vk.com/music/playlist/1_2", "просто текст", ""):
        with pytest.raises(UnsupportedLink, match="не поддерживается"):
            await extract_from_link(bad)


# --------------------------------------------------------------------------- Yandex Music mapping


def test_yandex_album(ym_client) -> None:
    album = Album.de_json(load("yandex_album_10100.json"), ym_client)
    ex = yandex_album_extraction(album)
    assert ex.source_app == "yandex_music" and ex.page_type == "album"
    assert ex.context.album_title == "Группа крови" and ex.context.album_artist == "КИНО"
    first = ex.items[0]
    assert (first.artist_raw, first.title_raw, first.album_raw, first.duration, first.conf) == (
        "КИНО", "Группа крови", "Группа крови", "4:44", 1.0)
    assert [i.title_raw for i in ex.items][:3] == ["Группа крови", "Закрой за мной дверь, я ухожу", "Война"]


def test_yandex_playlist_uuid(ym_client) -> None:
    pl = Playlist.de_json(load("yandex_playlist_uuid.json"), ym_client)
    ex = yandex_playlist_extraction(pl)
    assert ex.page_type == "playlist" and ex.context.playlist_name == "Лучшее: КИНО"
    assert len(ex.items) == 5
    assert ex.items[0].artist_raw == "КИНО" and ex.items[0].title_raw == "Группа крови"
    assert all(i.duration and i.album_raw for i in ex.items)


def test_yandex_playlist_versions_and_joined_artists(ym_client) -> None:
    data = load("yandex_playlist_user.json")
    pl = Playlist.de_json(data, ym_client)
    ex = yandex_playlist_extraction(pl)
    assert ex.context.playlist_name == "Русский рэп в машину"
    raw0 = data["tracks"][0]["track"]
    assert ex.items[0].title_raw == f"{raw0['title']} ({raw0['version']})"
    multi = [(t["track"], i) for t, i in zip(data["tracks"], ex.items, strict=True) if len(t["track"]["artists"]) > 1]
    for raw, it in multi:
        assert it.artist_raw == ", ".join(a["name"] for a in raw["artists"])


def test_yandex_playlist_fills_missing_tracks(ym_client) -> None:
    data = copy.deepcopy(load("yandex_playlist_uuid.json"))
    removed = data["tracks"][1].pop("track")
    pl = Playlist.de_json(data, ym_client)
    assert len(yandex_playlist_extraction(pl).items) == 4
    extra = {str(removed["id"]): Track.de_json(removed, ym_client)}
    ex = yandex_playlist_extraction(pl, extra)
    assert len(ex.items) == 5 and ex.items[1].title_raw == removed["title"]


def test_yandex_track(ym_client) -> None:
    tracks = Track.de_list(load("yandex_tracks.json"), ym_client)
    it = yandex_track_item(tracks[0])
    assert (it.artist_raw, it.title_raw, it.album_raw, it.duration) == ("КИНО", "Группа крови", "Группа крови", "4:44")
    assert yandex_track_item(None) is None


class FakeYM:
    def __init__(self, client, *, fail: Exception | None = None) -> None:
        self.client = client
        self.fail = fail
        self.track_calls: list[list[str]] = []
        self.removed: dict | None = None

    def playlist(self, uuid):
        if self.fail:
            raise self.fail
        data = copy.deepcopy(load("yandex_playlist_uuid.json"))
        self.removed = data["tracks"][0].pop("track")
        return Playlist.de_json(data, self.client)

    def users_playlists(self, kind, owner):
        if self.fail:
            raise self.fail
        return Playlist.de_json(load("yandex_playlist_user.json"), self.client)

    def albums_with_tracks(self, album_id):
        return Album.de_json(load("yandex_album_10100.json"), self.client)

    def tracks(self, ids):
        self.track_calls.append(list(ids))
        if self.removed and ids == [str(self.removed["id"])]:
            return [Track.de_json(self.removed, self.client)]
        return Track.de_list(load("yandex_tracks.json"), self.client)


def test_yandex_fetch_with_fake_client(monkeypatch: pytest.MonkeyPatch, ym_client) -> None:
    fake = FakeYM(ym_client)
    monkeypatch.setattr(links, "_yandex_client", lambda: fake)
    ex = links._yandex_fetch("playlist", ("ar.2e74f130-a3f6-481b-82e1-bc04c03d8354",))
    assert len(ex.items) == 5 and fake.track_calls == [["38633712"]]
    assert links._yandex_fetch("user_playlist", ("yandexmusic", "1636")).context.playlist_name
    assert links._yandex_fetch("album", ("10100",)).page_type == "album"
    assert links._yandex_fetch("track", ("38634621",)).items[0].title_raw == "Группа крови"


@pytest.mark.parametrize("err", [NotFoundError("nf"), BadRequestError("playlistIdBindingError")])
def test_yandex_fetch_errors(monkeypatch: pytest.MonkeyPatch, ym_client, err: Exception) -> None:
    monkeypatch.setattr(links, "_yandex_client", lambda: FakeYM(ym_client, fail=err))
    with pytest.raises(LinkFetchError):
        links._yandex_fetch("user_playlist", ("nobody", "1000"))


# --------------------------------------------------------------------------- YouTube mapping


def test_youtube_playlist_fixture() -> None:
    ex = youtube_info_extraction(load("youtube_playlist.json"))
    assert ex.source_app == "youtube" and ex.page_type == "playlist"
    assert ex.context.playlist_name == "1988 - Группа крови (Maschina Records, 2019)"
    # uploader channel is not the artist: titles cleaned of track numbers, low confidence
    assert ex.items[0].title_raw == "Группа Крови" and ex.items[0].conf == 0.5
    assert ex.items[0].duration == "4:50"


def test_youtube_album_fixture() -> None:
    ex = youtube_info_extraction(load("youtube_music_album.json"))
    assert ex.page_type == "album"
    assert ex.context.album_title == "Группа крови" and ex.context.album_artist == "Группа КИНО"
    assert ex.items[0].artist_raw == "Группа КИНО" and ex.items[0].album_raw == "Группа крови"
    assert ex.items[0].conf >= 0.85


def test_youtube_entry_variants() -> None:
    topic = youtube_entry_item({"title": "Группа крови", "channel": "Кино - Topic", "duration": 284})
    assert (topic.artist_raw, topic.title_raw, topic.duration, topic.conf) == ("Кино", "Группа крови", "4:44", 0.9)
    tema = youtube_entry_item({"title": "Романс", "channel": "Сплин - Тема"})
    assert tema.artist_raw == "Сплин"
    dashed = youtube_entry_item({"title": "Кино - Группа крови (Official Audio)", "channel": "Rock Archive"})
    assert (dashed.artist_raw, dashed.title_raw, dashed.conf) == ("Кино", "Группа крови", 0.8)
    meta = youtube_entry_item({"title": "x", "track": "Группа крови", "artist": "Кино", "album": "Группа крови"})
    assert (meta.artist_raw, meta.title_raw, meta.album_raw, meta.conf) == ("Кино", "Группа крови", "Группа крови", 0.95)
    assert youtube_entry_item({"title": "[Deleted video]"}) is None
    assert clean_yt_title("01. Звезда [Official Music Video HD]") == "Звезда"
    assert clean_yt_title("Song (Remix)") == "Song (Remix)"


def test_youtube_single_video() -> None:
    ex = youtube_info_extraction({"id": "x", "title": "Группа крови", "track": "Группа крови", "artist": "Кино",
                                  "duration": 284})
    assert ex.page_type == "other" and len(ex.items) == 1


# --------------------------------------------------------------------------- Spotify


def test_spotify_playlist_fixture() -> None:
    ex = spotify_extraction(load("spotify_embed_playlist.json"))
    assert ex.source_app == "spotify" and ex.page_type == "playlist"
    assert ex.context.playlist_name == "Today’s Top Hits"
    assert ex.items[0].artist_raw == "Olivia Rodrigo, Chappell Roan"
    assert ex.items[0].title_raw == "expectations (feat. Chappell Roan)" and ex.items[0].duration == "3:44"


def test_spotify_album_fixture() -> None:
    ex = spotify_extraction(load("spotify_embed_album.json"))
    assert ex.page_type == "album" and ex.context.album_title == "Discovery"
    assert ex.context.album_artist == "Daft Punk"
    assert (ex.items[0].artist_raw, ex.items[0].title_raw, ex.items[0].album_raw) == (
        "Daft Punk", "One More Time", "Discovery")


def test_spotify_bad_page() -> None:
    with pytest.raises(LinkFetchError):
        spotify_next_data("<html>nothing</html>")
    with pytest.raises(LinkFetchError):
        spotify_extraction({"props": {}})


def _embed_html(data: dict) -> str:
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(data, ensure_ascii=False) + "</script></body></html>")


@respx.mock
async def test_spotify_fetch() -> None:
    route = respx.get("https://open.spotify.com/embed/album/2noRn2Aes5aoNVsU6iWThc").mock(
        return_value=httpx.Response(200, text=_embed_html(load("spotify_embed_album.json"))))
    ex = await extract_from_link("https://open.spotify.com/intl-de/album/2noRn2Aes5aoNVsU6iWThc?si=1")
    assert route.called and ex.page_type == "album" and len(ex.items) == 6
    ex = await extract_from_link("spotify:album:2noRn2Aes5aoNVsU6iWThc")
    assert ex.context.album_artist == "Daft Punk"


@respx.mock
async def test_spotify_not_found() -> None:
    respx.get("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M").mock(return_value=httpx.Response(404))
    with pytest.raises(LinkFetchError):
        await extract_from_link("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
