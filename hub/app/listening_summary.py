"""Read listening evidence without interpreting a playback position as measured time."""
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlayEvent, utcnow


def listened_threshold_ms(duration_s: float | None) -> int:
    """A measured listen: 30 seconds, or half the track for tracks shorter than a minute."""
    return min(30_000, max(1, round(duration_s * 500))) if duration_s and duration_s > 0 else 30_000


async def summary(s: AsyncSession, user_id: int, start: date, end: date, timezone: str) -> dict:
    zone = ZoneInfo(timezone)
    begin = datetime.combine(start, time.min, zone).astimezone(UTC).replace(tzinfo=None)
    stop = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(UTC).replace(tzinfo=None)
    rows = list((await s.execute(select(PlayEvent).where(
        PlayEvent.user_id == user_id, PlayEvent.ts <= utcnow(), PlayEvent.kind.in_(("start", "progress", "interrupted", "error", "skip", "finish")),
        PlayEvent.superseded_scrobble.is_(False)).order_by(PlayEvent.ts, PlayEvent.id))).scalars())
    aliases = defaultdict(set)
    for row in rows:
        if row.ref and row.nd_song_id:
            aliases[row.ref].add(row.nd_song_id)

    def canonical(ref, nd):
        if nd:
            return f"nd:{nd}"
        known = aliases.get(ref, set())
        return f"nd:{next(iter(known))}" if len(known) == 1 else ref

    def merge_metadata(target, incoming):
        provenance = incoming.get("provenance", {})
        current_provenance = target.setdefault("provenance", {})
        for field, value in incoming.items():
            if field == "provenance" or value is None:
                continue
            if field == "artists" and field in target:
                existing = target[field]
                incoming_names = [artist.get("name") for artist in value]
                existing_names = [artist.get("name") for artist in existing]
                changed = False
                for artist in value:
                    name = artist.get("name")
                    if name and incoming_names.count(name) == 1 and existing_names.count(name) == 1:
                        match = existing[existing_names.index(name)]
                        conflict = any(match.get(key) and artist.get(key) and match[key] != artist[key]
                                       for key in ("ref", "nd_artist_id"))
                        if not conflict:
                            for key in ("ref", "nd_artist_id"):
                                if artist.get(key) and not match.get(key):
                                    match[key] = artist[key]
                                    changed = True
                if len(value) > len(existing):
                    for artist in value:
                        if artist.get("name") not in existing_names:
                            existing.append(artist.copy())
                            changed = True
                if changed and field in provenance:
                    sources = set(current_provenance.get(field, "").split("+")) | {provenance[field]}
                    current_provenance[field] = "+".join(sorted(s for s in sources if s))
            elif field not in target:
                target[field] = value
                if field in provenance:
                    current_provenance[field] = provenance[field]

    groups = defaultdict(list)
    historical = []
    for row in rows:
        if row.playback_id and row.device_id:
            groups[(row.device_id, row.playback_id)].append(row)
        elif row.kind == "finish" and begin <= row.ts < stop:
            historical.append(row)
    tracks = {}
    measured_attempts = measured_listens = measured_ms = 0
    for events in groups.values():
        first = events[0]
        values = [e.listened_ms for e in events if e.listened_ms is not None]
        if not values:
            continue
        duration = next((e.duration_s for e in reversed(events) if e.duration_s), None)
        metadata = {}
        for event in events:
            merge_metadata(metadata, event.track_metadata or {})
        if not duration:
            duration = metadata.get("duration_s")
        threshold = listened_threshold_ms(duration)
        previous = elapsed = 0
        counted = False
        for event in events:
            if event.listened_ms is None:
                continue
            current = max(previous, event.listened_ms)
            if begin <= event.ts < stop:
                elapsed += current - previous
                counted |= previous < threshold <= current
            previous = current
        if not elapsed and not counted and not begin <= first.ts < stop:
            continue
        measured_attempts += 1
        measured_listens += int(counted)
        measured_ms += elapsed
        nd = next((e.nd_song_id for e in events if e.nd_song_id), None)
        ref = next((e.ref for e in events if e.ref), None)
        key = canonical(ref, nd)
        if key:
            resolved_nd = nd or (next(iter(aliases[ref])) if ref and len(aliases.get(ref, ())) == 1 else None)
            track = tracks.setdefault(key, {"ref": ref, "nd_song_id": resolved_nd, "measured_ms": 0, "listens": 0, "metadata": {}})
            track["ref"] = track["ref"] or ref
            track["nd_song_id"] = track["nd_song_id"] or resolved_nd
            track["measured_ms"] += elapsed
            track["listens"] += int(counted)
            merge_metadata(track["metadata"], metadata)
    scrobbles = sum(1 for row in historical if row.client == "navidrome")
    legacy = len(historical) - scrobbles
    for row in historical:
        key = canonical(row.ref, row.nd_song_id)
        if not key:
            continue
        track = tracks.setdefault(key, {"ref": row.ref, "nd_song_id": row.nd_song_id, "measured_ms": 0,
                                        "listens": 0, "metadata": {}})
        if row.client == "navidrome":
            track["listens"] += 1
        track["ref"] = track["ref"] or row.ref
        track["nd_song_id"] = track["nd_song_id"] or row.nd_song_id
        merge_metadata(track["metadata"], row.track_metadata or {})
    observed = min((row.ts for row in rows), default=None)
    return {"start": start.isoformat(), "end": end.isoformat(), "timezone": timezone,
            "counted_listens": measured_listens + scrobbles, "measured_listens": measured_listens,
            "measured_attempts": measured_attempts, "measured_ms": measured_ms,
            "scrobble_evidence": scrobbles, "legacy_finish_evidence": legacy,
            "first_observed_at": observed.replace(tzinfo=UTC).isoformat() if observed else None,
            "tracks": sorted(tracks.values(), key=lambda x: (-x["measured_ms"], x["ref"] or x["nd_song_id"] or "")),
            "quality": {"tracks_with_title": sum(bool(t["metadata"].get("title")) for t in tracks.values()),
                        "tracks_with_artists": sum(bool(t["metadata"].get("artists")) for t in tracks.values()),
                        "tracks_total": len(tracks),
                        "measured": "cumulative player checkpoints", "scrobble": "threshold evidence; duration unknown",
                        "legacy": "unmeasured; not counted", "period_assignment": "checkpoint deltas attributed to checkpoint time; offline compaction may delay attribution"}}
