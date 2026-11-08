"""The taste map for the admin (INSIGHTS.md 8.2): the user's songs, the songs of a mix and of a wave session in the
sound space (MuQ-MuLan), flattened by the embedder (`/v2/project`: no model load, no embedding). Two layouts: the
PCA coordinates `x, y` and the «Грани» layout `fx, fy` that the Hub draws from the cosines to the facets."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.insights import launches, names
from app.insights.taste_view import admin_profile, disliked_track, facet_index, facets
from app.mixes import embedder
from app.mixes.build import GENERATOR_TITLES
from app.mixes.taste import Profile, TasteTrack
from app.mixes.taste_math import V2Profile
from app.mixes.taste_select import facet_names, prepare, source_weights
from app.models import User

PROJECT_TIMEOUT_S = 12
DEFAULT_LIMIT = 600
# «Грани» layout: a point is pulled to the anchor of every facet it is close to. The anchors sit on the unit circle
# (the heaviest facet at 12 o'clock, then clockwise); the pull of a facet is the square of its positive cosine, so
# a close facet dominates; the distance from the center grows with the best cosine and reaches the circle at
# FACET_FULL (cosines of tracks of one facet to its center are about 0.5–0.7 in practice).
FACET_FULL = 0.6
LAYOUT_NOTE = ("x, y — главные оси звука (подпись: доля объяснённой дисперсии); fx, fy — раскладка «Грани» "
               "по близости к граням вкуса, как её видит рекомендательная система")


def anchors(n: int) -> list[tuple[float, float]]:
    """Facet anchors on the unit circle, x to the right, y up: facet 0 at 12 o'clock, the next ones clockwise."""
    return [(math.sin(2 * math.pi * i / n), math.cos(2 * math.pi * i / n)) for i in range(n)]


def facet_layout(cos: list[float]) -> tuple[float, float]:
    """`fx, fy` of a point by its cosines to the facets (in the order of `anchors`); no positive cosine — the center."""
    pull = [max(0.0, c) ** 2 for c in cos]
    total = sum(pull)
    if total <= 0:
        return 0.0, 0.0
    r = min(1.0, max(0.0, max(cos) / FACET_FULL))
    points = anchors(len(cos))
    return (sum(u * a[0] for u, a in zip(pull, points, strict=True)) / total * r,
            sum(u * a[1] for u, a in zip(pull, points, strict=True)) / total * r)


async def project(p: V2Profile, keys: list[str], *, basis: str = "global",
                  fit_keys: list[str] | None = None) -> tuple[embedder.GeometryProjection | None, str | None]:
    """`/v2/project` of `keys` against the profile's facets. The profile's geometry must be prepared; an expired space
    is rebuilt once (`/v2/profile`), then the answer is (None, reason)."""
    for attempt in range(2):
        if p.geometry is None:
            return None, "down"
        try:
            async with embedder.EmbedderClient() as client:
                return await asyncio.wait_for(client.project_v2(keys, p.geometry, basis=basis, fit_keys=fit_keys),
                                              PROJECT_TIMEOUT_S), None
        except embedder.EmbedderSpaceExpired:
            if attempt:
                return None, "stale_space"
            await prepare(p)
        except embedder.EmbedderDown:
            return None, "down"
        except TimeoutError:
            return None, "timeout"
    return None, "down"


async def track_facet_cos(p: Profile, key: str) -> dict[str, float] | None:
    """The cosine of one track to every facet of the profile (facet id -> cos), or None without sound or a v2 profile."""
    if not isinstance(p, V2Profile) or p.geometry is None or not p.geometry.facets:
        return None
    result, _ = await project(p, [key])
    got = result.facet_cos.get(key) if result else None
    return dict(zip((f.id for f in p.geometry.facets), got, strict=True)) if got else None


def _canonical(p: Profile, key: str) -> TasteTrack | None:
    return p.tracks.get(key) or p.tracks.get(p.by_id.get(key, ""))


def _profile_keys(p: Profile, limit: int) -> tuple[list[TasteTrack], bool]:
    """Songs with a weight, a ♥, a download or a 👎: the heaviest `limit`, then every seed and explicit 👎."""
    wanted = [t for t in p.tracks.values() if t.score != 0 or t.liked or t.downloaded or t.album_liked
              or disliked_track(p, t)]
    wanted.sort(key=lambda t: (-abs(t.score), t.key))
    chosen = {t.key: t for t in wanted[:limit]}
    for t in wanted[limit:]:
        if disliked_track(p, t):
            chosen[t.key] = t
    return list(chosen.values()), len(wanted) > limit


def _mix_layer(h: launches.History, mix_id: int | None) -> launches.MixInfo | None:
    """The chosen mix, else the current one (the newest active, else the newest)."""
    if mix_id is not None:
        if mix_id not in h.mixes:
            raise LookupError("mix")
        return h.mixes[mix_id]
    infos = sorted(h.mixes.values(), key=lambda i: (i.mix.created_at, i.mix.id), reverse=True)
    return next((i for i in infos if i.mix.status == "active"), infos[0] if infos else None)


def _wave_layer(h: launches.History, wave_id: str | None) -> str | None:
    """The chosen session, else the newest one that handed out tracks."""
    if wave_id is not None:
        if wave_id not in h.sessions:
            raise LookupError("wave")
        return wave_id
    handed = [w for w in h.sessions.values() if h.wave_items.get(w.id)]
    return max(handed, key=lambda w: (w.created_at, w.id)).id if handed else None


async def build(s: AsyncSession, user: User, *, model: str | None, basis: str, mix_id: int | None,
                wave_id: str | None, limit: int, now: datetime) -> dict[str, Any]:
    p = await admin_profile(s, user.id, model=model, now=now)
    h = await launches.load(s, user.id, now)
    info, sid = _mix_layer(h, mix_id), _wave_layer(h, wave_id)
    out: dict[str, Any] = {"available": False, "reason": None, "space_id": None, "basis": basis, "explained": [],
                           "facets": [], "points": [], "missing": [], "truncated": False,
                           "mix_id": info.mix.id if info else None, "wave_id": sid, "layout_note": LAYOUT_NOTE}
    if not isinstance(p, V2Profile):
        return {**out, "reason": "model_v1"}
    found, state = await facets(p)
    if found is None or p.geometry is None:
        return {**out, "reason": f"embedder_{state}" if isinstance(state, str) else "embedder_down"}

    chosen, truncated = _profile_keys(p, limit)
    seeds = {t.key for t in p.seeds(min(8, max(0, get_settings().cfg.mixes.seeds)))}
    have = {t.key for t in chosen}
    chosen += [t for k, t in p.tracks.items() if k in seeds and k not in have]
    layers: dict[str, set[str]] = {}
    rows: dict[str, dict[str, Any]] = {}

    def add(key: str, layer: str, **extra: Any) -> None:
        t = _canonical(p, key)
        k = t.key if t else key
        layers.setdefault(k, set()).add(layer)
        row = rows.setdefault(k, {})
        for name, value in extra.items():  # the first layer to name a field keeps it
            if value is not None:
                row.setdefault(name, value)

    for t in chosen:
        add(t.key, "profile")
    if info:
        ls = [x for x in h.launches if x.mix_id == info.mix.id]
        attempts, marks = [a for x in ls for a in x.attempts], [m for x in ls for m in x.marks]
        for i in info.items:
            if i.state == "active" and (i.nd_song_id or i.ref):
                add(f"nd:{i.nd_song_id}" if i.nd_song_id else i.ref or "", "mix", generator=i.generator,
                          mix_position=i.position, familiar=i.familiar,
                          mix_outcome=launches.outcome(attempts, marks, h.aliases.of(i.ref, i.nd_song_id)))
    wave_items = h.wave_items.get(sid or "", [])
    if sid:
        launch = next((x for x in h.launches if x.wave_id == sid), None)
        for w in wave_items:
            add(w.key, "wave", wave_position=w.position, familiar=w.familiar, generator=w.generator,
                wave_outcome=launches.outcome(launch.attempts if launch else [], launch.marks if launch else [],
                                              h.aliases.of(w.ref, w.nd_song_id) | {w.key}))

    keys = sorted(layers)
    fit = [k for k in source_weights(p)[0] if k]
    own = basis == "user" and bool(fit)  # nothing to fit the personal axes on: the global ones
    result, reason = await project(p, keys, basis="keys" if own else "global", fit_keys=fit if own else None)
    if result is None:
        return {**out, "reason": f"embedder_{reason}"}

    found = facet_names(p)  # the profile may have been rebuilt after an expired space
    pairs, known = [], {}
    for k in keys:
        t = p.tracks.get(k)
        r, n = (t.ref, t.nd_song_id) if t else ((None, k[3:]) if k.startswith("nd:") else (k, None))
        pairs.append((r, n))
        if t:
            known[(r, n)] = (t.artist, t.title)
    for w in wave_items:
        known.setdefault((w.ref, w.nd_song_id), (w.artist, w.title))
    name_of = await names.resolve(s, pairs, known)
    order = [f.id for f in p.geometry.facets]
    members = facet_index(found)

    def name_row(k: str) -> dict[str, Any]:
        t = p.tracks.get(k)
        n = name_of.get(k)
        return {"key": k, "artist": (n.artist if n else None) or (t.artist if t else ""),
                "title": (n.title if n else None) or (t.title if t else names.UNKNOWN),
                "layers": [x for x in ("profile", "mix", "wave") if x in layers[k]]}

    points, missing = [], []
    for k in keys:
        base = name_row(k)
        if k not in result.points:
            missing.append(base)
            continue
        t, extra = p.tracks.get(k), rows.get(k, {})
        cos = result.facet_cos[k]
        fx, fy = facet_layout(cos)
        best = order[max(range(len(cos)), key=lambda i: (cos[i], -i))] if cos else None
        s_ = t.score if t else None
        points.append({
            **base, "x": result.points[k][0], "y": result.points[k][1], "fx": fx, "fy": fy,
            "s": s_, "p": t.positive_raw if t else None, "n": t.negative if t else None,
            "facet_id": members.get(k, best), "facet_cos": cos, "seed": k in seeds,
            "liked": bool(t and t.liked), "disliked": bool(t and disliked_track(p, t)),
            "negative": bool(t and (t.score < 0 or disliked_track(p, t))),
            "familiar": t.familiar if t else extra.get("familiar"),
            "generator": extra.get("generator"),
            "generator_title": GENERATOR_TITLES.get(extra.get("generator") or "", extra.get("generator")),
            "mix_position": extra.get("mix_position"), "wave_position": extra.get("wave_position"),
            "outcome": (extra.get("mix_outcome") or extra.get("wave_outcome") or {}).get("outcome")})
    count = {f_id: i for i, f_id in enumerate(order)}
    ring = anchors(len(order))
    xy = result.facets
    return {**out, "available": True, "space_id": result.space_id, "basis": "user" if result.basis == "keys" else "global",
            "explained": result.explained, "truncated": truncated,
            "facets": [{"id": f["id"], "weight": f["weight"], "artists": f["artists"][:2],
                        "x": xy[f["id"]][0], "y": xy[f["id"]][1], "fx": ring[count[f["id"]]][0],
                        "fy": ring[count[f["id"]]][1], "color_index": count[f["id"]]} for f in found],
            "points": points, "missing": missing}
