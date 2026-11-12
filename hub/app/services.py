"""Process-wide singletons: download providers and catalog clients (created in the app lifespan)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import get_settings
from app.providers.base import Provider, TrackQuery

if TYPE_CHECKING:
    from app.catalog.deezer import DeezerClient
    from app.catalog.hitmoz_catalog import HitmozCatalog
    from app.catalog.musicbrainz import MusicBrainzClient
    from app.torrents.jackett import JackettClient
    from app.torrents.public import PublicSearchClient
    from app.torrents.qbit import QbitClient

log = logging.getLogger(__name__)


class Services:
    def __init__(self) -> None:
        self.providers: dict[str, Provider] = {}
        self.deezer: DeezerClient | None = None
        self.hitmoz_catalog: HitmozCatalog | None = None
        self.musicbrainz: MusicBrainzClient | None = None
        # releases from rutracker (README, «Раздачи с rutracker»): None while JACKETT_API_KEY is empty
        self.jackett: JackettClient | PublicSearchClient | None = None
        self.qbit: QbitClient | None = None

    def start(self) -> None:
        from app.catalog.deezer import DeezerClient
        from app.catalog.hitmoz_catalog import HitmozCatalog
        from app.catalog.musicbrainz import MusicBrainzClient
        from app.providers.hitmos_fm import HitmosFmProvider
        from app.providers.hitmoz import HitmozProvider
        from app.providers.reuploads import SoundcloudProvider, YtReuploadProvider
        from app.providers.ytdlp import YoutubeAccess, YtdlpProvider

        cfg = get_settings().cfg
        hitmoz = HitmozProvider(cfg.hitmoz)
        youtube = YoutubeAccess(cfg.ytdlp)
        self.providers = {
            "hitmoz": hitmoz,
            "hitmos_fm": HitmosFmProvider(cfg.hitmos_fm),
            "ytdlp": YtdlpProvider(cfg.ytdlp, access=youtube),
            "yt_reupload": YtReuploadProvider(cfg.ytdlp, access=youtube),
            "soundcloud": SoundcloudProvider(cfg.ytdlp),
        }
        self.deezer = DeezerClient(cfg.catalog)
        self.hitmoz_catalog = HitmozCatalog(hitmoz)
        self.musicbrainz = MusicBrainzClient(cfg.catalog)
        s = get_settings()
        if s.torrents_enabled:
            from app.torrents.jackett import JackettClient
            from app.torrents.qbit import QbitClient
            from app.torrents.stream import PROVIDER, TorrentProvider

            if s.torrent_search == "public":
                from app.torrents.public import PublicSearchClient

                self.jackett = PublicSearchClient()
            else:
                self.jackett = JackettClient(s.jackett_url, s.jackett_api_key, cfg.torrents.indexer)
            self.qbit = QbitClient(s.qbit_url, s.qbit_username, s.qbit_password)
            # releases' files listened to before downloading (`file:torrent:…`): not a search source
            self.providers[PROVIDER] = TorrentProvider()

    async def stop(self) -> None:
        for p in self.providers.values():
            try:
                await p.aclose()
            except Exception:
                log.exception("closing provider %s", p.name)
        for c in (self.deezer, self.musicbrainz, self.jackett, self.qbit):
            if c is not None:
                try:
                    await c.aclose()
                except Exception:
                    log.exception("closing catalog client")
        self.jackett = self.qbit = None

    def provider_order(self, q: TrackQuery, *, for_download: bool = False) -> list[Provider]:
        from app.matching.normalize import is_russian_query

        sources = get_settings().cfg.sources
        names = sources.ru if is_russian_query(q) else sources.default
        if for_download:
            names = [*names, *sources.download_fallback]
        return [self.providers[n] for n in dict.fromkeys(names) if n in self.providers]

    def reupload_providers(self) -> list[Provider]:
        """Sources of re-uploads by small channels: only for copies of explicit tracks without censorship."""
        return [self.providers[n] for n in get_settings().cfg.sources.reuploads if n in self.providers]


svc = Services()
