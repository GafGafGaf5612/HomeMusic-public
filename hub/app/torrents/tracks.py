"""A catalog track no source has, from a release with seeders: `app.listen` tries this before Deezer's preview.

The trackers are searched for the album ("artist album") and for the artist (discographies). Releases that have
seeders, are by this artist and hold files a browser plays (not a disc image, not APE) are ranked: the album itself
first, then discographies, then the most seeded. The file lists of the best few are asked for at once (a .torrent
through Jackett; a magnet's list comes from peers, qBittorrent fetches it as a preview, `core.magnet_meta`), and the
first release whose list has the track by its file name wins. Its file is then a release's file of
`app.torrents.stream`: qBittorrent downloads it in order while it plays, and «Скачать» takes it.

Searches are kept for a while and qBittorrent keeps the file lists: the next track of the album is found at once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import PurePosixPath

from app.config import get_settings
from app.matching.normalize import normalize_artist, normalize_title, to_latin, version_kind
from app.providers.base import Candidate, TrackQuery
from app.services import svc
from app.torrents.jackett import JackettError, Release, merge_duplicates

log = logging.getLogger(__name__)

SEARCH_TIMEOUT_S = 8.0  # trackers that haven't answered by then are left out: a blocked one must not hold the rest
META_WAIT_S = 20.0  # a magnet's file list from peers
RELEASES = 3  # releases whose file lists are asked for, per track
FILES = 3  # files of the track kept (the next takes over when one doesn't play)
SEARCH_TTL_S = 30 * 60
SEARCHES_MAX = 300
UNPLAYABLE_CODECS = {"APE", "WavPack", "TTA", "DSD"}

_searches: dict[str, tuple[float, list[Release]]] = {}


def enabled() -> bool:
    return svc.jackett is not None and svc.qbit is not None


def reset() -> None:
    _searches.clear()


def _words(s: str | None) -> str:
    """Script-independent words with a space on each side: `" kino "` is in `_words("Кино - Группа крови")`."""
    return f" {to_latin(s or '')} "


def _has(text: str, names: set[str]) -> bool:
    return any(f" {n} " in text for n in names if n)


class Want:
    """The track as the releases' titles and file names spell it (catalog name and Cyrillic original)."""

    def __init__(self, q: TrackQuery, pq: TrackQuery):
        qs = (pq, q)
        self.artists = {normalize_artist(t.artist).latin for t in qs} - {""}
        self.albums = {normalize_title(t.album).latin for t in qs if t.album} - {""}
        self.titles = {normalize_title(t.title).latin for t in qs} - {""}
        self.version = version_kind(normalize_title(q.title).version) or version_kind(q.version)
        self.queries: list[str] = []
        for t in qs:  # the album, then the artist alone (discographies)
            if t.album and (album := normalize_title(t.album).text):
                self.queries.append(f"{normalize_artist(t.artist).text} {album}")
        self.queries += [normalize_artist(t.artist).text for t in qs]
        self.queries = list(dict.fromkeys(x.strip() for x in self.queries if x.strip()))

    def usable(self, r: Release) -> bool:
        """Seeded, by this artist, and files a browser plays."""
        return ((r.seeders or 0) > 0 and r.info.layout != "image" and r.info.codec not in UNPLAYABLE_CODECS
                and _has(_words(r.title), self.artists))

    def rank(self, releases: list[Release]) -> list[Release]:
        def key(r: Release) -> tuple[bool, bool, int]:
            return not _has(_words(r.title), self.albums), not r.info.discography, -(r.seeders or 0)

        return sorted(releases, key=key)

    def score(self, path: str) -> tuple[bool, bool] | None:
        """(the very title, in the album's folder) for a file of the track; None for any other file."""
        from app.torrents.core import parse_filename

        p = PurePosixPath(path)
        guess = parse_filename(p.name)
        if guess.get("artist") and normalize_artist(guess["artist"]).latin not in self.artists:
            return None  # "05. Другой - Песня.mp3": a song of the same name on a compilation
        t = normalize_title(guess.get("title"))
        if version_kind(t.version) != self.version:
            return None  # live, remix, instrumental… of the track
        if t.latin in self.titles:
            exact = True
        elif any(len(x) >= 6 and _has(f" {t.latin} ", {x}) for x in self.titles):
            exact = False  # "Иерусалим (Jerusalem)"
        else:
            return None
        return exact, _has(_words(" ".join(p.parts[:-1])), self.albums)


async def _search(query: str) -> list[Release]:
    now = time.monotonic()
    hit = _searches.get(query)
    if hit and now - hit[0] < SEARCH_TTL_S:
        return hit[1]
    assert svc.jackett is not None
    found = await svc.jackett.search(query, get_settings().cfg.torrents.categories, timeout_s=SEARCH_TIMEOUT_S)
    if len(_searches) >= SEARCHES_MAX:
        for k in [k for k, (at, _) in _searches.items() if now - at >= SEARCH_TTL_S] or list(_searches)[:50]:
            del _searches[k]
    _searches[query] = (now, found)
    return found


async def _files(r: Release, want: Want) -> list[Candidate]:
    """The track's files in this release, the best first (the release's file list may come from peers)."""
    from app.torrents import core, stream

    meta, _ = await core.release_meta(r.link, wait_s=META_WAIT_S)
    scored = []
    for i, f in enumerate(core.list_files(meta)):
        if f.kind == "audio" and not f.image and stream.media_type(f.path) and (s := want.score(f.path)):
            scored.append((s, i))
    scored.sort(key=lambda si: si[0], reverse=True)
    return [stream.candidate(meta, i, link=r.link, release_title=r.title, details_url=r.details_url)
            for _, i in scored[:FILES]]


async def find(q: TrackQuery, pq: TrackQuery) -> list[Candidate]:
    """The track's files in the first seeded release found to have it; [] when none has. Raises JackettError when
    no tracker answered."""
    want = Want(q, pq)
    results = await asyncio.gather(*(_search(x) for x in want.queries), return_exceptions=True)
    found: list[Release] = []
    errors: list[str] = []
    for res in results:
        if isinstance(res, BaseException):
            errors.append(str(res) or repr(res))
        else:
            found += res
    if not found and errors:
        raise JackettError(errors[0])
    releases = want.rank([r for r in merge_duplicates(found) if want.usable(r)])[:RELEASES]
    if not releases:
        return []
    tasks = [asyncio.create_task(_files(r, want)) for r in releases]
    try:
        for done in asyncio.as_completed(tasks):
            try:
                if cands := await done:
                    return cands
            except asyncio.CancelledError:
                raise
            except Exception as e:  # no peers for the file list yet, the tracker refused the .torrent…
                log.info("releases for %s - %s: %s", q.artist, q.title, str(e) or repr(e))
        return []
    finally:
        for t in tasks:
            t.cancel()
