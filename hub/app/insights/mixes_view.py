"""«Микс дня» for the admin (INSIGHTS.md 7.6): the funnel from `stats_json` as the build wrote it, the songs with
their score parts and what became of them. Old mixes without the stage-1 fields show `null`, never a recount."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.insights import launches, names
from app.insights.launches import History, Launch, MixInfo
from app.mixes.build import GENERATOR_TITLES, MODE_TITLES
from app.mixes.taste import artist_key

STATE_ORDER = {"active": 0, "reserve": 1, "failed": 2}


def _launches_of(h: History, mix_id: int) -> list[Launch]:
    return [x for x in h.launches if x.mix_id == mix_id]


def _top_generators(info: MixInfo) -> list[dict[str, Any]]:
    used = Counter(i.generator for i in info.items if i.state == "active")
    return [{"generator": g, "title": GENERATOR_TITLES.get(g, g), "songs": n}
            for g, n in sorted(used.items(), key=lambda x: (-x[1], x[0]))[:3]]


def row(h: History, info: MixInfo) -> dict[str, Any]:
    m = info.mix
    ls = _launches_of(h, m.id)
    attempts = [a for x in ls for a in x.attempts]
    stats = launches.attempt_stats(attempts)
    return {"id": m.id, "created_at": m.created_at, "closed_at": m.closed_at, "status": m.status, "title": m.title,
            "mode": m.mode, "mode_title": MODE_TITLES.get(m.mode, m.mode),
            "taste_model": (m.stats_json or {}).get("taste_model"),
            "size": sum(i.state == "active" for i in info.items),
            "downloaded": sum(i.downloaded_for_mix for i in info.items), "launches": len(ls),
            "started": stats["started"], "heard": stats["heard"], "finished": stats["finished"],
            "early_skips": stats["early_skips"], **launches.mark_stats([mk for x in ls for mk in x.marks]),
            "top_generators": _top_generators(info)}


def listing(h: History, limit: int) -> list[dict[str, Any]]:
    infos = sorted(h.mixes.values(), key=lambda i: (i.mix.created_at, i.mix.id), reverse=True)
    return [row(h, i) for i in infos[:limit]]


def _outside(h: History, info: MixInfo, aliases: set[str]) -> bool:
    """Heard the track outside this mix while the mix was in effect."""
    return any(a.heard_at and info.live_at(a.started_at) and (a.launch is None or a.launch.mix_id != info.mix.id)
               and a.aliases & aliases for a in h.attempts)


async def detail(s: AsyncSession, h: History, mix_id: int) -> dict[str, Any] | None:
    info = h.mixes.get(mix_id)
    if info is None:
        return None
    m, stats = info.mix, info.mix.stats_json or {}
    ls = _launches_of(h, m.id)
    attempts = [a for x in ls for a in x.attempts]
    marks = [mk for x in ls for mk in x.marks]
    items = sorted(info.items, key=lambda i: (STATE_ORDER.get(i.state, 9), i.position))
    seed_keys = stats.get("seed_keys") or []
    pairs = [(i.ref, i.nd_song_id) for i in items] + [
        (k, None) if not k.startswith("nd:") else (None, k[3:]) for k in seed_keys]
    known = {(i.ref, i.nd_song_id): (((i.features_json or {}).get("track") or {}).get("artist") or "",
                                     ((i.features_json or {}).get("track") or {}).get("title") or "") for i in items}
    name_of = await names.resolve(s, pairs, known)
    facets = stats.get("facets")
    facet_of = {key: f.get("id") for f in facets or () for key in f.get("members") or ()}
    attempt_stats, mark_stats = launches.attempt_stats(attempts), launches.mark_stats(marks)
    active = [i for i in items if i.state == "active"]
    gens = stats.get("generators")
    embedder = stats.get("embedder")
    funnel = {
        "generators": [{"generator": g, "title": GENERATOR_TITLES.get(g, g), "tracks": v.get("tracks"),
                        "used": v.get("used"), "seconds": v.get("seconds"), "error": v.get("error"),
                        "off": bool(v.get("off"))} for g, v in (gens or {}).items()] if gens is not None else None,
        "candidates": stats.get("candidates"), "rejected": stats.get("rejected"), "kept": stats.get("kept"),
        "sound_scored": embedder.get("scored") if isinstance(embedder, dict) else None,
        "active": len(active), "familiar": sum(i.familiar for i in active),
        "new": sum(not i.familiar for i in active), "reserve": sum(i.state == "reserve" for i in items),
        "failed": sum(i.state == "failed" for i in items), "launches": len(ls),
        "started": attempt_stats["started"], "heard": attempt_stats["heard"],
        "finished": attempt_stats["finished"], "early_skips": attempt_stats["early_skips"],
        "liked": mark_stats["likes"], "disliked": mark_stats["dislikes"]}

    def name(ref, nd):
        n = name_of.get(f"nd:{nd}" if nd else ref or "")
        return n.out() if n else {"key": f"nd:{nd}" if nd else ref, "ref": ref, "nd_song_id": nd, "artist": "",
                                  "title": names.UNKNOWN, "owned": False}

    out_items = []
    for i in items:
        f = i.features_json or {}
        aliases = h.aliases.of(i.ref, i.nd_song_id)
        out_items.append({**name(i.ref, i.nd_song_id), "position": i.position, "state": i.state,
                          "familiar": i.familiar, "generator": i.generator,
                          "generator_title": GENERATOR_TITLES.get(i.generator, i.generator),
                          "gens": f.get("generators"), "score": i.score, "score_parts": f.get("score_parts"),
                          "sim": f.get("sim"), "rank": f.get("rank"), "profile": f.get("profile"),
                          "downloaded_for_mix": i.downloaded_for_mix, "cleanup": f.get("cleanup"),
                          **launches.outcome(attempts, marks, aliases),
                          "heard_outside": _outside(h, info, aliases)})
    seeds = []
    for k in seed_keys:
        n = name(*((k, None) if not k.startswith("nd:") else (None, k[3:])))
        seeds.append({**n, "key": k, "facet_id": facet_of.get(k)})
    facets_out = None
    if facets is not None:
        facets_out = [{"id": f.get("id"), "weight": f.get("weight"), "artists": f.get("artists"),
                       "size": len(f.get("members") or ())} for f in facets]
    return {**row(h, info), "description": m.description, "nd_playlist_id": m.nd_playlist_id,
            "funnel": funnel, "seeds": seeds, "seed_labels": stats.get("seeds"), "facets": facets_out,
            "maturity": stats.get("maturity"), "embedder": embedder, "build_seconds": stats.get("build_seconds"),
            "items": out_items,
            "launch_list": [{"id": x.id, "started_at": x.started_at, "attribution": x.mix_attribution,
                             "origin": x.origin, "client": x.client, **x.stats()} for x in ls],
            "artists": len({artist_key(n["artist"]) for n in out_items if n["artist"]})}


__all__ = ["detail", "listing", "row"]
