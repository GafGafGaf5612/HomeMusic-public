"""YouTube throttling, token provider wiring and reuse; no real network or timer waits."""

import asyncio
import threading

import pytest
import respx
from yt_dlp.utils import DownloadError

from app.config import YtdlpCfg
from app.providers.base import ProviderError, TrackQuery
from app.providers.reuploads import SoundcloudProvider, YtReuploadProvider
from app.providers.ytdlp import DEFAULT_CLIENTS_S, YTM_API_URL, YoutubeAccess, YtdlpProvider, _YdlLogger


@pytest.mark.parametrize("refusal", [
    "Sign in to confirm you're not a bot",
    "HTTP Error 429: Too Many Requests",
    "This content isn't available, try again later",
    "The provided YouTube account cookies are no longer valid. They have likely been rotated",
])
async def test_refusal_stops_both_youtube_providers_until_cooldown_expires(refusal):
    now = [100.0]
    cfg = YtdlpCfg(refusal_cooldown_s=900)
    access = YoutubeAccess(cfg, clock=lambda: now[0])
    p = YtdlpProvider(cfg, access=access)
    reuploads = YtReuploadProvider(cfg, access=access)
    calls = []

    def blocked():
        calls.append(1)
        raise DownloadError(refusal)

    with pytest.raises(ProviderError):
        await p._run(blocked)
    for provider in (p, reuploads):
        with pytest.raises(ProviderError):
            await provider._run(blocked)
    assert len(calls) == 1
    now[0] += 901
    assert await reuploads._run(lambda: "recovered") == "recovered"
    # A YouTube refusal cannot disable SoundCloud's independent fallback.
    assert await SoundcloudProvider(cfg)._run(lambda: "full audio") == "full audio"


async def test_replacing_cookie_export_releases_cooldown_and_does_not_rewrite_file(monkeypatch, tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(cookie_file))
    p = YtdlpProvider(YtdlpCfg())

    def expired():
        _YdlLogger().warning("The provided YouTube account cookies are no longer valid. They were rotated")

    with pytest.raises(ProviderError, match="заменить файл cookies"):
        await p._run(expired)
    with pytest.raises(ProviderError):
        await p._run(lambda: "must not run")
    replacement = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\trefreshed-test\n"
    cookie_file.write_text(replacement, encoding="utf-8")
    assert await p._run(lambda: "new session") == "new session"
    assert cookie_file.read_text(encoding="utf-8") == replacement


async def test_unavailable_video_does_not_disable_other_videos():
    p = YtdlpProvider(YtdlpCfg())

    def unavailable():
        raise DownloadError("Video unavailable")

    with pytest.raises(ProviderError):
        await p._run(unavailable)
    assert await p._run(lambda: "another video") == "another video"


async def test_waiting_youtube_call_rechecks_refusal_before_starting():
    cfg = YtdlpCfg(max_parallel=1)
    p = YtdlpProvider(cfg)
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def blocked():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=1)
        raise DownloadError("Sign in to confirm you're not a bot")

    first = asyncio.create_task(p._run(blocked))
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(p._run(lambda: calls.append(1)))
    await asyncio.sleep(0)  # second waits for the shared semaphore
    release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), timeout=1)
    assert all(isinstance(r, ProviderError) for r in results)
    assert calls == []


@respx.mock
async def test_music_api_rate_limit_does_not_trigger_more_searches(monkeypatch):
    p = YtdlpProvider(YtdlpCfg())
    route = respx.post(YTM_API_URL).respond(429)
    calls = []
    monkeypatch.setattr(p, "_extract", lambda *a, **kw: calls.append(a))
    for _ in range(2):
        with pytest.raises(ProviderError, match="ограничил запросы"):
            await p.search(TrackQuery(artist="Test", title="Song"))
    assert route.call_count == 1
    assert calls == []
    await p.aclose()


async def test_metadata_is_coalesced_cached_and_invalidated_on_cookie_replacement(monkeypatch, tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(cookie_file))
    now = [100.0]
    cfg = YtdlpCfg()
    access = YoutubeAccess(cfg, clock=lambda: now[0])
    p = YtdlpProvider(cfg, access=access)
    other = YtReuploadProvider(cfg, access=access)
    calls = []

    def extract(url, **opts):
        calls.append(url)
        return {"id": "test", "title": "Song", "formats": []}

    monkeypatch.setattr(p, "_extract", extract)
    monkeypatch.setattr(other, "_extract", extract)
    first, second = await asyncio.gather(p._metadata("test"), other._metadata("test"))
    first["title"] = "mutated"
    assert second["title"] == "Song"
    assert (await other._metadata("test"))["title"] == "Song"
    assert len(calls) == 1
    now[0] += 601
    await p._metadata("test")
    assert len(calls) == 2
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    await other._metadata("test")
    assert len(calls) == 3


def test_youtube_opts_select_mweb_and_internal_provider_without_affecting_soundcloud(monkeypatch):
    monkeypatch.setenv("YTDLP_POT_BASE_URL", "http://youtube-pot:4416")
    cfg = YtdlpCfg()
    for p in (YtdlpProvider(cfg), YtReuploadProvider(cfg)):
        with p._youtube_dl({}) as ydl:
            assert ydl.params["extractor_args"]["youtube"]["player_client"] == ["mweb"]
            assert ydl.params["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == ["http://youtube-pot:4416"]
            assert ydl.params["sleep_interval_requests"] == 1
            assert ydl.params["sleep_interval"] == 5
            assert ydl.params["retries"] == 1
            assert ydl.params["concurrent_fragment_downloads"] == 1
    sc_opts = SoundcloudProvider(cfg)._base_opts()
    assert "extractor_args" not in sc_opts and "sleep_interval" not in sc_opts


def test_yt_dlp_loads_the_installed_token_provider():
    # Embedding yt-dlp must discover the pip plugin too (not just the CLI).
    from yt_dlp.extractor.youtube.pot._registry import _pot_providers

    with YtdlpProvider(YtdlpCfg())._youtube_dl({}):
        assert "BgUtilHTTP" in _pot_providers.value


def test_token_diagnostic_traces_are_never_logged(caplog):
    with caplog.at_level("DEBUG"):
        _YdlLogger().debug("[debug] [youtube] [pot:bgutil:http] Generated POT: secret-token")
    assert "secret-token" not in caplog.text


async def test_metadata_cache_keeps_stream_shortcut_for_both_youtube_providers(monkeypatch):
    cfg = YtdlpCfg()
    access = YoutubeAccess(cfg)
    first = YtdlpProvider(cfg, access=access)
    other = YtReuploadProvider(cfg, access=access)
    calls = []

    def extract(url, **opts):
        calls.append(url)
        return {"id": "test", "title": "Song", "formats": [{
            "url": "https://audio.test/full", "ext": "m4a", "acodec": "aac", "vcodec": "none",
        }]}

    monkeypatch.setattr(first, "_extract", extract)
    monkeypatch.setattr(other, "_extract", extract)
    from app.providers.ytdlp import entry_to_candidate

    c = entry_to_candidate(await first._metadata("test"))
    await other._metadata("test")
    assert (await other.stream(c)).url == "https://audio.test/full"
    assert (await first.stream(c)).url == "https://audio.test/full"
    assert len(calls) == 1
    await first.aclose()
    await other.aclose()


def test_sabr_warning_switches_to_the_default_clients_for_a_while(monkeypatch):
    monkeypatch.setenv("YTDLP_POT_BASE_URL", "http://youtube-pot:4416")
    now = [1000.0]
    cfg = YtdlpCfg()
    access = YoutubeAccess(cfg, clock=lambda: now[0])
    p = YtdlpProvider(cfg, access=access)
    assert p._base_opts()["extractor_args"]["youtube"]["player_client"] == ["mweb"]

    _YdlLogger(access=access).warning(
        "[youtube] vid1: Some mweb client https formats have been skipped as they are missing a URL. YouTube may "
        "have enabled the SABR-only streaming experiment for the current session.")
    args = p._base_opts()["extractor_args"]
    assert "youtube" not in args  # yt-dlp's default clients
    assert args["youtubepot-bgutilhttp"]["base_url"] == ["http://youtube-pot:4416"]

    now[0] += DEFAULT_CLIENTS_S + 1
    assert p._base_opts()["extractor_args"]["youtube"]["player_client"] == ["mweb"]
