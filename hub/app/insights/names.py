"""Names of tracks without the network (INSIGHTS.md 7.4): what the Hub already has locally, in order of trust."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CatalogCache, MixItem, PlayEvent, Track, WaveItem

UNKNOWN = "Неизвестный трек"
CHUNK = 500


@dataclass
class Name:
    ref: str | None
    nd_song_id: str | None
    artist: str
    title: str
    owned: bool  # the song is in the library
    source: str  # library/profile/event/catalog/mix/wave/unknown

    @property
    def key(self) -> str:
        return f"nd:{self.nd_song_id}" if self.nd_song_id else self.ref or ""

    def out(self) -> dict[str, Any]:
        return {"key": self.key, "ref": self.ref, "nd_song_id": self.nd_song_id, "artist": self.artist,
                "title": self.title, "owned": self.owned}


def _chunks(values: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(values), CHUNK):
        yield values[i:i + CHUNK]


def _metadata_name(data: dict[str, Any] | None) -> tuple[str, str] | None:
    data = data or {}
    title = data.get("title")
    artists = [a.get("name") for a in data.get("artists") or () if isinstance(a, dict) and a.get("name")]
    artist = ", ".join(artists) or data.get("artist_credit") or data.get("artist")
    return (artist, title) if title and artist else None


def _track_name(raw: Any) -> tuple[str, str] | None:
    if isinstance(raw, dict) and raw.get("title") and raw.get("artist"):
        return raw["artist"], raw["title"]
    return None


async def resolve(s: AsyncSession, pairs: Iterable[tuple[str | None, str | None]],
                  known: dict[tuple[str | None, str | None], tuple[str, str]] | None = None) -> dict[str, Name]:
    """(ref, nd_song_id) -> `Name`, keyed by `Name.key` of the pair as given (nd:<song>, else the ref). `known` —
    names the caller already has (the taste profile's), trusted after the library."""
    pairs = {(r or None, n or None) for r, n in pairs if r or n}
    songs = sorted({n for _, n in pairs if n})
    refs = sorted({r for r, _ in pairs if r})
    by_song: dict[str, Track] = {}
    by_dz: dict[str, Track] = {}
    for chunk in _chunks(songs):
        for tr in (await s.execute(select(Track).where(Track.nd_song_id.in_(chunk)).order_by(Track.id))).scalars():
            by_song[tr.nd_song_id] = tr  # type: ignore[index]  # the newest row wins
    dz = sorted({r[3:] for r in refs if r.startswith("dz:")})
    for chunk in _chunks(dz):
        for tr in (await s.execute(select(Track).where(Track.deezer_track_id.in_(chunk)).order_by(Track.id))).scalars():
            by_dz[f"dz:{tr.deezer_track_id}"] = tr

    out: dict[str, Name] = {}
    missing: list[tuple[str | None, str | None]] = []
    for ref, nd in sorted(pairs, key=lambda x: (x[0] or "", x[1] or "")):
        tr = by_song.get(nd) if nd else None
        tr = tr or (by_dz.get(ref) if ref else None)
        key = f"nd:{nd}" if nd else ref or ""
        if tr is not None:
            out[key] = Name(ref or (f"dz:{tr.deezer_track_id}" if tr.deezer_track_id else None), nd or tr.nd_song_id,
                            tr.artist, tr.title, bool(tr.nd_song_id), "library")
        elif known and (name := known.get((ref, nd))) and name[0] and name[1]:
            out[key] = Name(ref, nd, name[0], name[1], False, "profile")
        else:
            missing.append((ref, nd))
    if not missing:
        return out

    found: dict[tuple[str | None, str | None], tuple[str, str, str]] = {}
    want_refs = sorted({r for r, _ in missing if r})
    want_songs = sorted({n for _, n in missing if n})

    def take(pair, name, source):
        if name and pair in want and pair not in found:
            found[pair] = (*name, source)

    want = set(missing)
    by_ref = {r: [p for p in missing if p[0] == r] for r in want_refs}
    by_nd = {n: [p for p in missing if p[1] == n] for n in want_songs}

    def targets(ref, nd):
        return [*(by_ref.get(ref, []) if ref else []), *(by_nd.get(nd, []) if nd else [])]

    # the latest playback metadata of each track (the Hub completed it on write from the library and the catalog)
    for column, values in ((PlayEvent.ref, want_refs), (PlayEvent.nd_song_id, want_songs)):
        for chunk in _chunks(values):
            rows = (await s.execute(select(PlayEvent.ref, PlayEvent.nd_song_id, PlayEvent.track_metadata)
                                    .where(column.in_(chunk), PlayEvent.track_metadata.is_not(None))
                                    .order_by(PlayEvent.ts.desc(), PlayEvent.id.desc()))).all()
            for ref, nd, data in rows:
                for pair in targets(ref, nd):
                    take(pair, _metadata_name(data), "event")
    keys = [f"mix:track:{r}" for r in want_refs] + [f"dz:track:{r[3:]}" for r in want_refs if r.startswith("dz:")]
    for chunk in _chunks(keys):
        for row in (await s.execute(select(CatalogCache).where(CatalogCache.key.in_(chunk)))).scalars():
            raw = row.payload_json
            if isinstance(raw, dict) and raw.get("ref") in by_ref:
                for pair in by_ref[raw["ref"]]:
                    take(pair, _track_name(raw), "catalog")
    for column, values in ((MixItem.ref, want_refs), (MixItem.nd_song_id, want_songs)):
        for chunk in _chunks(values):
            for ref, nd, features in (await s.execute(select(MixItem.ref, MixItem.nd_song_id, MixItem.features_json)
                                                      .where(column.in_(chunk)).order_by(MixItem.id.desc()))).all():
                for pair in targets(ref, nd):
                    take(pair, _track_name((features or {}).get("track")), "mix")
    for column, values in ((WaveItem.ref, want_refs), (WaveItem.nd_song_id, want_songs)):
        for chunk in _chunks(values):
            for ref, nd, artist, title in (await s.execute(
                    select(WaveItem.ref, WaveItem.nd_song_id, WaveItem.artist, WaveItem.title)
                    .where(column.in_(chunk), func.length(WaveItem.title) > 0)
                    .order_by(WaveItem.id.desc()))).all():
                for pair in targets(ref, nd):
                    take(pair, (artist, title) if artist and title else None, "wave")
    for ref, nd in missing:
        artist, title, source = found.get((ref, nd), ("", UNKNOWN, "unknown"))
        out[f"nd:{nd}" if nd else ref or ""] = Name(ref, nd, artist, title, False, source)
    return out


async def library_songs(s: AsyncSession, songs: Iterable[str]) -> set[str]:
    """Which of these Navidrome songs the library has (`owned`)."""
    songs = sorted(set(songs))
    out: set[str] = set()
    for chunk in _chunks(songs):
        out.update(x for x in (await s.execute(select(Track.nd_song_id).where(
            Track.nd_song_id.in_(chunk)))).scalars() if x)
    return out


__all__ = ["UNKNOWN", "Name", "library_songs", "resolve"]
