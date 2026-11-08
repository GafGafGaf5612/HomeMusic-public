"""Re-uploads by small channels: title parsing, filters, YouTube/SoundCloud search (yt-dlp mocked)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.config import YtdlpCfg
from app.providers.base import Candidate, ProviderError, TrackQuery
from app.providers.reuploads import (
    SoundcloudProvider,
    YtReuploadProvider,
    is_noise,
    reupload_queries,
    sc_entry_to_candidate,
    split_upload_title,
)

Q = TrackQuery(artist="Слава КПСС", title="Кайфоград", duration_s=154, explicit=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Слава КПСС - Кайфоград (без цензуры)", ("Слава КПСС", "Кайфоград")),
        ("Кайфоград – Слава КПСС (без цензуры)", ("Слава КПСС", "Кайфоград")),  # reversed, en dash
        ("Кайфоград", ("канал", "Кайфоград")),  # no dash: the uploader
    ],
    ids=["artist-title", "title-artist", "no-dash"],
)
def test_split_upload_title(raw, expected):
    assert split_upload_title(raw, "Слава КПСС", "канал") == expected


@pytest.mark.parametrize(
    ("raw", "query", "noise"),
    [
        ("РЕАКЦИЯ БУКЕРА НА Слава КПСС - Кайфоград", "Слава КПСС - Кайфоград", True),
        ("Слава КПСС - Кайфоград | Разбор клипа", "Слава КПСС - Кайфоград", True),
        ("Кайфоград x Duvet (mashup)", "Слава КПСС - Кайфоград", True),
        ("Кайфоград (live)", "Слава КПСС - Кайфоград (live)", False),  # asked for
        ("Кайфоград – Слава КПСС (без цензуры)", "Слава КПСС - Кайфоград", False),
    ],
)
def test_noise(raw, query, noise):
    assert is_noise(raw, query) is noise


def test_queries_add_the_uncensored_word_once():
    assert reupload_queries(Q) == ["Слава КПСС - Кайфоград", "Слава КПСС - Кайфоград без цензуры"]
    asked = Q.model_copy(update={"title": "Кайфоград без цензуры"})
    assert reupload_queries(asked) == ["Слава КПСС - Кайфоград без цензуры"]


def yt_entry(vid: str, title: str, channel: str, duration: int = 154, **kw: Any) -> dict:
    return {"_type": "url", "ie_key": "Youtube", "id": vid, "title": title, "channel": channel,
            "duration": duration, **kw}


async def test_youtube_reuploads_skip_official_copies_and_noise(monkeypatch):
    p = YtReuploadProvider(YtdlpCfg())
    asked: list[str] = []

    def fake_extract(url: str, **opts: Any) -> dict:
        asked.append(url)
        if url.endswith("без цензуры"):
            return {"entries": [
                yt_entry("unc00000001", "Кайфоград – Слава КПСС (без цензуры)", "Олег Овощенский", 155),
                yt_entry("plain000001", "Слава КПСС – Кайфоград", "Олег Овощенский"),  # repeat of the other search
            ]}
        return {"entries": [
            yt_entry("official001", "Слава КПСС - Кайфоград (Video)", "СЛАВА КПСС", 176, channel_is_verified=True),
            yt_entry("topic000001", "Кайфоград", "Слава КПСС - Topic"),
            yt_entry("plain000001", "Слава КПСС – Кайфоград", "Олег Овощенский"),
            yt_entry("react000001", "РЕАКЦИЯ НА Слава КПСС - Кайфоград", "ХАЙЛАЙТЫ"),
            yt_entry("mixtape0001", "Слава КПСС - Все треки", "фан", 3792),
            yt_entry("clean000001", "Слава КПСС - Кайфоград (без мата)", "music_hub", 150),
        ]}

    monkeypatch.setattr(p, "_extract", fake_extract)
    out = await p.search(Q, limit=10)

    assert sorted(asked) == ["ytsearch10:Слава КПСС - Кайфоград", "ytsearch10:Слава КПСС - Кайфоград без цензуры"]
    assert [c.provider_id for c in out] == ["unc00000001", "plain000001", "clean000001"]
    first = out[0]
    assert (first.provider, first.artist, first.title) == ("yt_reupload", "Слава КПСС", "Кайфоград")
    assert first.explicit is True and first.extra["uncensored"] is True and first.extra["reupload"] is True
    assert first.extra["original_title"] == "Кайфоград – Слава КПСС (без цензуры)"
    assert "uncensored" not in out[1].extra
    assert out[2].extra["uncensored"] is False and out[2].explicit is False


async def test_youtube_reuploads_survive_one_failed_search(monkeypatch):
    p = YtReuploadProvider(YtdlpCfg())

    def fake_extract(url: str, **opts: Any) -> dict:
        if url.endswith("без цензуры"):
            raise ProviderError("yt-dlp: boom")
        return {"entries": [yt_entry("plain000001", "Слава КПСС - Кайфоград", "фан")]}

    monkeypatch.setattr(p, "_extract", fake_extract)
    assert [c.provider_id for c in await p.search(Q)] == ["plain000001"]

    def down(url: str, **opts: Any) -> dict:
        raise ProviderError("down")

    monkeypatch.setattr(p, "_extract", down)
    with pytest.raises(ProviderError):
        await p.search(Q)


def sc_entry(tid: str, title: str, uploader: str, uploaded: date, duration: float = 153.66, **kw: Any) -> dict:
    ts = int(datetime(uploaded.year, uploaded.month, uploaded.day, 12, tzinfo=UTC).timestamp())
    return {"_type": "url", "ie_key": "Soundcloud", "id": tid, "title": title, "uploader": uploader,
            "timestamp": ts, "duration": duration, "webpage_url": f"https://soundcloud.com/{uploader}/{tid}",
            "view_count": 1000, "thumbnails": [{"url": "https://i1.sndcdn.com/mini.jpg"},
                                               {"url": "https://i1.sndcdn.com/original.jpg"}], **kw}


def test_soundcloud_entry():
    c = sc_entry_to_candidate(sc_entry("1647355104", "Слава КПСС - Кайфоград", "пук хрюк", date(2023, 10, 23)))
    assert c is not None
    assert (c.provider, c.provider_id, c.duration_s, c.upload_date) == (
        "soundcloud", "1647355104", 154, date(2023, 10, 23))
    assert c.page_url == "https://soundcloud.com/пук хрюк/1647355104"
    assert c.cover_url == "https://i1.sndcdn.com/original.jpg" and c.extra["channel"] == "пук хрюк"
    assert sc_entry_to_candidate({"id": "1", "title": ""}) is None


async def test_soundcloud_search_parses_titles(monkeypatch):
    p = SoundcloudProvider(YtdlpCfg())

    def fake_extract(url: str, **opts: Any) -> dict:
        assert url.startswith("scsearch10:")
        return {"entries": [
            sc_entry("2", "Кайфоград", "Слава КПСС", date(2024, 6, 1)),
            sc_entry("3", "Кайфоград (правильная версия)", "wwrtuyi", date(2026, 5, 2)),
            sc_entry("4", "Слава КПСС - Кайфоград (Любимые песни)", "пук хрюк", date(2023, 10, 23)),
        ]}

    monkeypatch.setattr(p, "_extract", fake_extract)
    out = await p.search(Q, limit=10)
    assert [c.provider_id for c in out] == ["3", "2", "4"]  # titled uncensored first
    assert [(c.artist, c.title) for c in out[:2]] == [("wwrtuyi", "Кайфоград"), ("Слава КПСС", "Кайфоград")]
    assert all(c.provider == "soundcloud" for c in out)


async def test_soundcloud_has_no_listening_stream():
    p = SoundcloudProvider(YtdlpCfg())
    c = Candidate(provider="soundcloud", provider_id="1", artist="a", title="t", page_url="https://soundcloud.com/a/t")
    assert p.media_url(c) == "https://soundcloud.com/a/t"
    assert p.streams is False  # listening downloads the file first (app.listen)
    with pytest.raises(ProviderError):
        await p.stream(c)
