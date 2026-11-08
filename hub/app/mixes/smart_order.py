"""The pure rules of the smart shuffle (MIXES.md 8): the order of the playlist's own songs and where the
recommendations go. No database, no network — everything here is a function of its arguments.

- «Fewer repeats» (Spotify's engineering blog, 2025): several random orders are made, each is given a staleness
  (a penalty for songs the user heard lately: the later the song was heard and the earlier it stands, the larger),
  the freshest one is taken. Songs of one artist are spread out first (the idea Spotify had in 2014), so that they
  do not follow one another.
- One recommendation after every `own_per_rec(n)` own songs: 3 in a playlist of more than 15 songs (Spotify's
  figure), 2 in a shorter one (ours; Spotify gives no figure); a playlist of one or two songs gets one.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

BIG_PLAYLIST = 15  # more own songs than this: a recommendation after every 3, else after every 2
PER_REC_BIG, PER_REC_SMALL = 3, 2
ORDERS = 20  # random orders compared by their staleness
POSITION_SCALE = 12.0  # the penalty of a song falls to a half at this position
ARTIST_GAP = 8  # an artist at most once in this many recommendations


@dataclass(frozen=True)
class OwnSong:
    id: str  # the id the client sent: a Navidrome song id, or a catalog ref (has a colon) for a ♥ not downloaded yet
    artist: str = ""  # artist key (`taste.artist_key`); "" — unknown, never treated as a repeat


def own_per_rec(n_own: int) -> int:
    return PER_REC_BIG if n_own > BIG_PLAYLIST else PER_REC_SMALL


def rec_count(n_own: int) -> int:
    """How many recommendations the whole playlist gets (at least one: even a single song is followed by one)."""
    return max(1, n_own // own_per_rec(n_own)) if n_own else 0


def slot_after(n_own: int, k: int) -> int:
    """The 0-based index of the own song recommendation `k` (0-based) goes after: own, own, own, rec..."""
    return min(n_own, own_per_rec(n_own) * (k + 1)) - 1


def slots_ahead(n_own: int, index: int, ahead: int) -> list[int]:
    """The slots to fill while the player is at own song `index`: the next `ahead` ones that are not behind it
    (the one right after that song counts)."""
    return [k for k in range(rec_count(n_own)) if slot_after(n_own, k) >= index][:max(0, ahead)]


# --- the order of own songs ------------------------------------------------------------------------------------------


def staleness(order: Sequence[str], age_days: dict[str, float]) -> float:
    """The penalty of an order: Σ over the songs heard before of `recency / (1 + position / POSITION_SCALE)`;
    `recency` is 1 for a song heard just now and falls as 1 / (1 + days). Songs never heard cost nothing."""
    total = 0.0
    for pos, sid in enumerate(order):
        age = age_days.get(sid)
        if age is not None:
            total += (1.0 / (1.0 + max(age, 0.0))) / (1.0 + pos / POSITION_SCALE)
    return total


def spread(songs: Sequence[OwnSong], rng: random.Random) -> list[OwnSong]:
    """A random order in which the songs of one artist are spaced evenly (each artist's k songs sit at random
    phases of 1/k of the list), repaired afterwards where two of one artist still touch."""
    groups: dict[str, list[OwnSong]] = {}
    for i, s in enumerate(songs):
        groups.setdefault(s.artist or f"\0{i}", []).append(s)
    keyed: list[tuple[float, float, OwnSong]] = []
    for members in groups.values():
        rng.shuffle(members)
        k = len(members)
        phase = rng.random()
        for j, s in enumerate(members):
            keyed.append(((j + phase) / k if k > 1 else rng.random(), rng.random(), s))
    keyed.sort(key=lambda x: (x[0], x[1]))
    return unstick([s for _, _, s in keyed], same_artist)


def unstick(order: list[OwnSong], touching: Callable[[OwnSong, OwnSong], bool],
            start: OwnSong | None = None) -> list[OwnSong]:
    """Where two neighbours `touch`, the second one is swapped with the nearest later song, if that leaves fewer
    touching pairs around the two places. `start` — the song that stands before the first one."""
    out = list(order)

    def bad(p: int) -> int:
        if not 0 <= p < len(out):
            return 0
        prev = out[p - 1] if p else start
        return int(prev is not None and touching(prev, out[p]))

    def around(*places: int) -> int:
        return sum(bad(p) for p in {q for i in places for q in (i, i + 1)})

    for i in range(len(out)):
        if not bad(i):
            continue
        for j in range(i + 1, len(out)):
            before = around(i, j)
            out[i], out[j] = out[j], out[i]
            if around(i, j) < before:
                break
            out[i], out[j] = out[j], out[i]
    return out


def same_artist(a: OwnSong, b: OwnSong) -> bool:
    return bool(a.artist) and a.artist == b.artist


def fewer_repeats(songs: Sequence[OwnSong], last_heard: dict[str, datetime], now: datetime, rng: random.Random,
                  first: str | None = None, orders: int = ORDERS) -> list[OwnSong]:
    """The shuffle of the own songs: the freshest of `orders` random, artist-spread orders. `first` (the song the
    user tapped or is playing) goes first. `last_heard` — song id → when the user last heard it, in any context."""
    unique = list({s.id: s for s in songs}.values())
    head = next((s for s in unique if s.id == first), None) if first else None
    rest = [s for s in unique if s is not head]
    if not rest:
        return [head] if head else []
    ages = {sid: max(0.0, (now - ts).total_seconds() / 86400) for sid, ts in last_heard.items()}
    best: list[OwnSong] = []
    best_cost = float("inf")
    for _ in range(max(1, orders)):
        order = unstick(spread(rest, rng), same_artist, start=head)
        full = ([head] if head else []) + order
        cost = staleness([s.id for s in full], ages)
        if cost < best_cost:
            best, best_cost = full, cost
    return best


# --- recommendations --------------------------------------------------------------------------------------------------


def pick_rec(ranked: Sequence[tuple[str, str]], recent_artists: Sequence[str], neighbors: Sequence[str]) -> int | None:
    """The index in `ranked` (candidates as `(id, artist key)`, best first) of the recommendation to hand out: the
    best whose artist did not appear in the last `ARTIST_GAP − 1` recommendations and is not the artist of the own
    songs on either side. Nothing fits — the gap gives way (the neighbours' artists never do); then the best of
    all. `None` — no candidates."""
    if not ranked:
        return None
    gap = {a for a in list(recent_artists)[-(ARTIST_GAP - 1):] if a}
    near = {a for a in neighbors if a}
    for i, (_, artist) in enumerate(ranked):
        if artist not in gap and artist not in near:
            return i
    for i, (_, artist) in enumerate(ranked):
        if artist not in near:
            return i
    return 0


# --- seeds ---------------------------------------------------------------------------------------------------------------

SEEDS = 10  # songs of the playlist the recommendations are asked by
SEEDS_RECENT = 3  # ... of them the newest added ones
RECENT_WINDOW = 10  # the newest added songs the recent seeds come from
SEED_PER_ARTIST = 2  # when the artists run out


def choose_seeds(songs: Sequence[OwnSong], rng: random.Random, newest_last: bool, n: int = SEEDS) -> list[str]:
    """Up to `n` songs of the playlist to ask for similar ones: the newest few first (the playlist's mood moves
    with what was added lately), then random ones, of different artists while there are any. `songs` — in the
    playlist's order; `newest_last` — new songs are added at the end (a playlist), not put first («Понравившиеся»)."""
    ordered = list(songs)
    newest = (ordered[-RECENT_WINDOW:][::-1] if newest_last else ordered[:RECENT_WINDOW])
    chosen: list[OwnSong] = []
    used: dict[str, int] = {}

    def take(s: OwnSong, cap: int) -> bool:
        if s in chosen or (s.artist and used.get(s.artist, 0) >= cap):
            return False
        chosen.append(s)
        used[s.artist] = used.get(s.artist, 0) + 1
        return True

    for s in newest:
        if len(chosen) >= min(SEEDS_RECENT, n):
            break
        take(s, 1)
    rest = [s for s in ordered if s not in chosen]
    rng.shuffle(rest)
    for cap in (1, SEED_PER_ARTIST, len(ordered)):
        for s in rest:
            if len(chosen) >= n:
                break
            take(s, cap)
    return [s.id for s in chosen]
