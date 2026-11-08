"""V2 consumer helpers: bounded geometry operations and deterministic facet-aware seed selection."""
from __future__ import annotations

import hashlib
import math
import random
from collections import Counter
from dataclasses import replace
from datetime import UTC
from zoneinfo import ZoneInfo

from app.mixes import embedder
from app.mixes.taste import TasteTrack, artist_key
from app.mixes.taste_math import V2Profile


def source_weights(p: V2Profile):
    # The geometry service has a 5000-item limit. Bound source inputs deterministically.
    positive = dict(sorted(p.positive_weights().items(), key=lambda x: (-x[1], x[0]))[:5000])
    return positive, sorted(p.negative_keys())[:5000]


async def prepare(p: V2Profile) -> None:
    p.geometry = None
    if not embedder.configured():
        return
    try:
        async with embedder.EmbedderClient() as client:
            p.geometry = await client.profile_v2(*source_weights(p))
    except embedder.EmbedderDown:
        pass


async def geometry_scores(p: V2Profile, candidates, extra_keys=()):
    """One operation, one mean. Any stale batch discards every old coordinate, then retries once."""
    for c in candidates:
        c.sim = None
    p.geometry = None
    if not embedder.configured():
        return {}
    positive, negative = source_weights(p)
    keys = list(dict.fromkeys([*(c.key for c in candidates), *extra_keys]))
    for attempt in range(2):
        try:
            async with embedder.EmbedderClient() as client:
                geometry = await client.profile_v2(positive, negative)
                similarity = await client.similarity_v2([c.key for c in candidates], geometry)
                vectors = await client.vectors_v2(keys, geometry)
            # Publish only after every batch succeeds, never partial results from a stale space.
            p.geometry = geometry
            for c in candidates:
                c.sim = similarity.scores.get(c.key)
            return vectors.vectors
        except embedder.EmbedderSpaceExpired:
            if attempt:
                break
        except embedder.EmbedderDown:
            break
    return {}


def facet_names(p: V2Profile) -> list[dict]:
    if p.geometry is None:
        return [{"id": "fallback", "weight": 1.0, "artists": [], "members": sorted(p.positive_weights())}]
    result = []
    for facet in p.geometry.facets:
        counts = Counter()
        for key in facet.members:
            if (t := p.tracks.get(key)) and t.artist:
                counts[artist_key(t.artist)] += max(t.score, 0)
        names = [p.artist_names.get(key, key) for key, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:2]]
        result.append({"id": facet.id, "weight": facet.weight, "artists": names, "members": facet.members})
    return result


def top_artists(p: V2Profile, n: int) -> list[str]:
    out = []
    for facet in sorted(facet_names(p), key=lambda x: (-x["weight"], x["id"])):
        if facet["weight"] < .15:
            continue
        for name in facet["artists"]:
            key = artist_key(name)
            if key not in out and key not in p.disliked_artists:
                out.append(key)
                break
    for key, score in sorted(p.artists.items(), key=lambda x: (-x[1], x[0])):
        if score > 0 and key not in out and key not in p.disliked_artists:
            out.append(key)
    return out[:n]


def select_profile_seeds(p: V2Profile, n: int, max_per_artist: int = 2) -> list[TasteTrack]:
    """Weighted sampling without replacement, with significant facets represented before redistribution."""
    if n <= 0:
        return []
    pool = p.active_seed_pool()
    if not pool and not p.has_recent_positive:
        pool = sorted((t for t in p.tracks.values() if (t.liked or t.downloaded or t.album_liked) and
                       not p.disliked(ref=t.ref, nd_song_id=t.nd_song_id, artist=t.artist, artist_ref=None)),
                      key=lambda t: t.key)
    day = p.now.replace(tzinfo=UTC).astimezone(ZoneInfo(p.timezone)).date().isoformat()
    salt = hashlib.sha256(f"{p.user_id}|{day}|v2".encode()).digest()
    rng = random.Random(int.from_bytes(salt, "big"))
    # One random priority per canonical key keeps selection independent of input order.
    priorities = {t.key: -math.log(max(rng.random(), 1e-15)) / max(t.score, 1) for t in sorted(pool, key=lambda t: t.key)}
    ranked = sorted(pool, key=lambda t: (priorities[t.key], t.key))
    available = {t.key for t in pool}
    groups = []
    covered = set()
    for facet in sorted(facet_names(p), key=lambda f: (-f["weight"], f["id"])):
        members = set(facet["members"]) & available - covered
        if members:
            groups.append((facet["weight"], members, facet["id"]))
            covered.update(members)
    if available - covered:
        missing = available - covered
        weight = sum(max(t.score, 1) for t in pool if t.key in missing) / max(1, sum(max(t.score, 1) for t in pool))
        groups.append((weight, missing, "missing"))
    if not groups:
        return []
    counts, out, selected = Counter(), [], set()

    def take(members):
        for t in ranked:
            a = artist_key(t.artist) if t.artist else f"unknown:{t.key}"
            if t.key in members and t.key not in selected and counts[a] < max_per_artist:
                counts[a] += 1
                selected.add(t.key)
                out.append(t)
                return True
        return False

    allocations = [0] * len(groups)
    for i, (weight, members, _) in enumerate(groups):
        if weight >= .15 and len(out) < n and take(members):
            allocations[i] += 1
    while len(out) < n:
        choices = sorted(range(len(groups)), key=lambda i: (-groups[i][0] / (allocations[i] + 1), groups[i][2]))
        chosen = next((i for i in choices if take(groups[i][1])), None)
        if chosen is None:
            break
        allocations[chosen] += 1
    return out


async def enrich_selected(p: V2Profile, selected: list[TasteTrack]) -> list[TasteTrack]:
    from app.mixes.taste import _catalog_info

    need = [t.ref for t in selected if t.ref and (not t.artist or not t.title)]
    info = await _catalog_info(need) if need else {}
    out, counts = [], Counter()
    for t in selected:
        if t.ref and (ct := info.get(t.ref)):
            t.track, t.artist, t.title, t.duration_s = ct, ct.artist, ct.title, ct.duration_s
            p.artist_names[artist_key(t.artist)] = t.artist
        if t.artist and t.title and counts[artist_key(t.artist)] < 2 and not p.disliked(
                ref=t.ref, nd_song_id=t.nd_song_id, artist=t.artist,
                artist_ref=t.track.artist_ref if t.track else None):
            counts[artist_key(t.artist)] += 1
            out.append(t)
    return out


async def seeds(p: V2Profile, n: int) -> list[TasteTrack]:
    from app.mixes.build import cold_seeds

    n = min(8, max(0, n))
    reserve = round((1 - p.maturity) * n)
    selected = select_profile_seeds(p, n)
    # Only actually selected profile objects may trigger catalog enrichment.
    selected = await enrich_selected(p, selected)
    artists = list(p.picked_artists)
    if not p.has_recent_positive:
        # A typed own artist-like is an explicit saved fallback, just like an own
        # album's locally known songs. No track score/like is fabricated for it.
        artists += [{"ref": state.ref, "name": (state.data or {}).get("name", "")}
                    for state in sorted(p.favorite_states.values(), key=lambda x: (x.target_type, x.key))
                    if state.target_type == "artist" and state.liked and state.ref and state.ref.startswith("dz:")]
    artists = list({a["ref"]: a for a in artists if a.get("ref")}.values())[:n]
    cold = await cold_seeds(replace(p, picked_artists=artists), n) if artists else []
    out, seen, counts = [], set(), Counter()

    def append(t):
        a = artist_key(t.artist)
        if t.key not in seen and counts[a] < 2 and not p.disliked(ref=t.ref, nd_song_id=t.nd_song_id,
                artist=t.artist, artist_ref=t.track.artist_ref if t.track else None):
            seen.add(t.key)
            counts[a] += 1
            out.append(t)

    for t in cold:
        if len(out) < reserve:
            append(t)
    for t in selected:
        if len(out) < n:
            append(t)
    for t in cold:
        if len(out) < n:
            append(t)
    return out
