"""`YOUTUBE_PROXY`: yt-dlp, the httpx clients that talk to YouTube and playback from googlevideo take it."""

from collections.abc import Iterator

import httpx
import pytest

import app.providers.ytdlp as ytdlp_mod
from app import config
from app.config import YtdlpCfg
from app.net import make_client, youtube_proxy
from app.providers.ytdlp import YtdlpProvider, entry_to_candidate

SOCKS = "socks5://user:pw@proxy.test:1080"
GV = "https://rr7---sn-isp1.googlevideo.com/videoplayback?expire=1&mn=sn-isp1%2Csn-far2&fvip=1&sig=x"
FLAT = {"_type": "url", "ie_key": "Youtube", "id": "abc123def45", "url": "https://www.youtube.com/watch?v=abc123def45",
        "title": "Группа крови", "duration": 284.0, "channel": "Кино - Topic"}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for name in ("YOUTUBE_PROXY", "OUTBOUND_PROXY", "OUTBOUND_PROXY_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    config.get_settings.cache_clear()
    yield monkeypatch
    config.get_settings.cache_clear()


def _set(env: pytest.MonkeyPatch, **values: str) -> None:
    for k, v in values.items():
        env.setenv(k, v)
    config.get_settings.cache_clear()


def _proxied(client: httpx.AsyncClient, url: str) -> str | None:
    """The proxy URL of the transport `client` sends `url` through (None: straight)."""
    transport = client._transport_for_url(httpx.URL(url))
    pool = getattr(transport, "_pool", None)
    proxy = getattr(pool, "_proxy_url", None)
    return None if proxy is None else f"{proxy.scheme.decode()}://{proxy.host.decode()}:{proxy.port}"


def test_no_proxy_by_default(env: pytest.MonkeyPatch) -> None:
    assert youtube_proxy() is None
    assert "proxy" not in YtdlpProvider(YtdlpCfg())._base_opts()


def test_youtube_proxy_wins_over_the_outbound_one(env: pytest.MonkeyPatch) -> None:
    _set(env, OUTBOUND_PROXY="http://ru.test:3128", OUTBOUND_PROXY_HOSTS="music.youtube.com")
    assert youtube_proxy() == "http://ru.test:3128"  # the old way still works
    _set(env, YOUTUBE_PROXY=SOCKS)
    assert youtube_proxy() == SOCKS


def test_ytdlp_takes_the_proxy_soundcloud_does_not(env: pytest.MonkeyPatch) -> None:
    _set(env, YOUTUBE_PROXY=SOCKS)
    assert YtdlpProvider(YtdlpCfg())._base_opts()["proxy"] == SOCKS

    class SoundCloud(YtdlpProvider):
        name = "soundcloud"

    assert "proxy" not in SoundCloud(YtdlpCfg())._base_opts()


async def test_clients_send_youtube_hosts_through_the_proxy(env: pytest.MonkeyPatch) -> None:
    _set(env, YOUTUBE_PROXY=SOCKS, OUTBOUND_PROXY="http://ru.test:3128", OUTBOUND_PROXY_HOSTS="*hitmos.fm")
    async with make_client() as c:
        for url in ("https://music.youtube.com/youtubei/v1/search", "https://www.youtube.com/watch?v=x",
                    "https://youtube.com/", GV):
            assert _proxied(c, url) == "socks5://proxy.test:1080", url
        assert _proxied(c, "https://rus.hitmos.fm/") == "http://ru.test:3128"
        assert _proxied(c, "https://api.deezer.com/search") is None
        assert _proxied(c, "https://notyoutube.com/") is None
    async with make_client(proxy_hosts=set()) as local:  # Jackett, qBittorrent: never proxied
        assert _proxied(local, "https://www.youtube.com/") is None


async def test_stream_keeps_to_the_proxy(env: pytest.MonkeyPatch) -> None:
    """Extraction and the stream (redirects included) take YOUTUBE_PROXY; no direct probe of media servers."""
    probed: list[str] = []
    env.setattr(ytdlp_mod, "_media_host_answers", lambda host: probed.append(host) or False)
    _set(env, YOUTUBE_PROXY=SOCKS, OUTBOUND_PROXY="http://ru.test:3128", OUTBOUND_PROXY_HOSTS="*hitmos.fm")
    p = YtdlpProvider(YtdlpCfg())
    env.setattr(p, "_extract", lambda url, **o: {"url": GV, "ext": "m4a"})
    c = entry_to_candidate(FLAT)
    assert c is not None
    src = await p.stream(c)
    assert src.url == GV and probed == []
    assert _proxied(src.client, GV) == _proxied(src.client, "https://redirect.test/") == "socks5://proxy.test:1080"
    await p.aclose()
