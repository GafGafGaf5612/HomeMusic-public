from copy import deepcopy
from datetime import datetime, timedelta

from app.catalog.models import CatalogTrack
from app.config import MixesCfg
from app.mixes.build import Cand
from app.mixes.shadow import compare
from app.mixes.taste_snapshot import TasteEvent, TasteSnapshot, TrackMetadata


def test_shadow_is_deterministic_and_does_not_mutate_candidates():
    now = datetime(2026, 10, 27, 12)
    event = TasteEvent(1, "one", now - timedelta(days=9), "finish", "dz:1", received_at=now)
    snapshot = TasteSnapshot(1, (event,), metadata=(TrackMetadata("dz:1", artist="Artist", title="Song"),))
    candidate = Cand(CatalogTrack(ref="dz:1", source="dz", id="1", title="Song", artist="Artist"),
                     gens={"favorites": 0}, sim=.9, score=42)
    before = deepcopy(candidate)
    first = compare(snapshot, [candidate], now=now, timezone="UTC", cfg=MixesCfg())
    assert first == compare(snapshot, [candidate], now=now, timezone="UTC", cfg=MixesCfg())
    assert candidate == before
    assert first["sound"] is None
    assert first["profiles"]["v2"]["ranking"][0]["score_without_sound"] == 1
    assert first["profiles"]["v2"]["favorites"] == ["dz:1"]


def test_rejected_candidates_and_score_contributions_explain_ranking_diff():
    now = datetime(2026, 10, 27, 12)
    events = (TasteEvent(1, "skip", now - timedelta(days=5), "skip", "dz:1",
                         received_at=now, position_s=5),)
    snapshot = TasteSnapshot(1, events, metadata=(TrackMetadata("dz:1", artist="Artist", title="Song"),))
    candidates = [Cand(CatalogTrack(ref="dz:1", source="dz", id="1", title="Song", artist="Artist"),
                       gens={"favorites": 0}),
                  Cand(CatalogTrack(ref="dz:2", source="dz", id="2", title="Other (Live)", artist="Artist"),
                       gens={"favorites": 1})]
    result = compare(snapshot, candidates, now=now, timezone="UTC", cfg=MixesCfg())
    v1 = {row["key"]: row for row in result["profiles"]["v1"]["candidate_decisions"]}
    v2 = {row["key"]: row for row in result["profiles"]["v2"]["candidate_decisions"]}
    assert v1["dz:1"]["rejection_reason"] == "negative_score"
    assert v2["dz:1"]["kept"] and v2["dz:1"]["rank"] == 1
    assert v2["dz:2"]["rejection_reason"] == "version"
    c = v2["dz:1"]["contributions"]
    assert v2["dz:1"]["score_without_sound"] == sum(c["generators"].values()) + c["popularity"] + \
        c["track_penalty"] + c["artist_penalty"]
    assert result["diff"]["added"] == ["dz:1"]


def test_local_seeds_do_not_claim_cold_or_metadata_enrichment():
    now = datetime(2026, 10, 27, 12)
    event = TasteEvent(1, "like", now, "like", "dz:1", received_at=now)
    snapshot = TasteSnapshot(1, (event,), picked_artists=({"name": "Picked", "ref": "dz:9"},))
    result = compare(snapshot, [], now=now, timezone="UTC", cfg=MixesCfg(seeds=20))
    assert result["profiles"]["v2"]["maturity"] < 1
    assert "local_profile_seeds" in result["profiles"]["v2"]
    assert "seeds" not in result["profiles"]["v2"]
    assert any("cold allocation" in item for item in result["limitations"])

async def test_shadow_sound_uses_frozen_scores_and_preserves_missing(monkeypatch):
    from app.mixes import embedder
    from app.mixes.shadow import sound_diff

    now = datetime(2026, 10, 27, 12)
    event = TasteEvent(1, "one", now - timedelta(days=9), "finish", "dz:1", received_at=now)
    snapshot = TasteSnapshot(1, (event,), metadata=(TrackMetadata("dz:1", artist="Artist", title="Song"),))
    candidates = [Cand(CatalogTrack(ref=f"dz:{n}", source="dz", id=str(n), title=f"Song {n}", artist="Artist"),
                       gens={"favorites": 0}) for n in (1, 2)]
    report = compare(snapshot, candidates, now=now, timezone="UTC", cfg=MixesCfg())
    calls = []

    async def post(self, path, body):
        calls.append(path)
        return {"model": "fake", "space_id": "snapshot", "dim": 2,
                "v1": {"dz:1": .2, "dz:2": .5}, "v2": {"dz:1": .8},
                "missing_v1": [], "missing_v2": ["dz:2"], "missing_baseline": [],
                "missing_positive": [], "missing_negative": []}

    monkeypatch.setattr(embedder, "configured", lambda: True)
    monkeypatch.setattr(embedder.EmbedderClient, "_post", post)
    await sound_diff(report, snapshot, candidates, now=now, timezone="UTC")
    assert calls == ["/v2/shadow"]
    assert report["sound"]["space_id"] == "snapshot"
    assert report["sound"]["common_count"] == 1
    assert report["sound"]["missing_v2"] == ["dz:2"]
    assert [row["key"] for row in report["sound"]["rankings"]["v1"]] == ["dz:1"]


async def test_shadow_sound_old_service_keeps_profile_report(monkeypatch):
    from app.mixes import embedder
    from app.mixes.shadow import sound_diff

    async def post(self, path, body):
        raise embedder.EmbedderDown("old service")

    monkeypatch.setattr(embedder, "configured", lambda: True)
    monkeypatch.setattr(embedder.EmbedderClient, "_post", post)
    now = datetime(2026, 10, 27, 12)
    snapshot = TasteSnapshot(1)
    report = compare(snapshot, [], now=now, timezone="UTC", cfg=MixesCfg())
    await sound_diff(report, snapshot, [], now=now, timezone="UTC")
    assert report["sound"] is None and report["sound_status"] == "unavailable"
    assert report["profiles"]["v1"]["candidate_decisions"] == []


def test_replay_selection_preserves_pool_order_and_limits_sound_to_first_300():
    from app.mixes.embedder import ShadowSound
    from app.mixes.shadow import replay_selection
    from app.mixes.taste import Profile
    from app.mixes.taste_math import V2Profile

    now = datetime(2026, 10, 27, 12)
    candidates = [Cand(CatalogTrack(ref=f"dz:{i}", source="dz", id=str(i), title=f"Song {i}", artist=f"Artist {i}"),
                       gens={"favorites": 0}) for i in range(301)]
    keys = [c.key for c in candidates]
    scores = dict.fromkeys(keys, 0.0)
    scores[keys[-1]] = 100.0
    vectors = {key: [1.0, 0.0] for key in keys}
    sound = ShadowSound(model="fake", space_id="frozen", dim=2, v1=scores, v2=scores,
                        missing_v1=[], missing_v2=[], missing_baseline=[], missing_positive=[], missing_negative=[],
                        raw_vectors=vectors, centered_vectors=vectors)
    report = {"pool_complete": True}
    replay_selection(report, candidates, {"v1": Profile(1, now), "v2": V2Profile(1, now)}, sound,
                     MixesCfg(size=2, reserve=0), "normal")
    assert report["selection"]["models"]["v1"]["active"] == keys[:2]
    assert report["selection"]["models"]["v2"]["active"] == keys[:2]
    assert report["selection"]["models"]["v1"]["sound_considered"] == 300
    assert all(c.score == 0 and c.sim is None for c in candidates)


def test_replay_selection_refuses_incomplete_pool():
    from app.mixes.embedder import ShadowSound
    from app.mixes.shadow import replay_selection

    sound = ShadowSound(model="fake", space_id="frozen", dim=2, v1={}, v2={}, missing_v1=[], missing_v2=[],
                        missing_baseline=[], missing_positive=[], missing_negative=[], raw_vectors={}, centered_vectors={})
    report = {"pool_complete": False}
    replay_selection(report, [], {}, sound, MixesCfg(), "normal")
    assert report["selection"] == {"status": "incomplete_pool"}


def test_replay_selection_uses_frozen_pairwise_coordinates_for_order():
    from app.mixes.embedder import ShadowSound
    from app.mixes.shadow import replay_selection
    from app.mixes.taste import Profile
    from app.mixes.taste_math import V2Profile

    now = datetime(2026, 10, 27, 12)
    candidates = [Cand(CatalogTrack(ref=f"dz:{i}", source="dz", id=str(i), title=f"Song {i}", artist=f"Artist {i}"),
                       gens={"favorites": 0}) for i in range(3)]
    keys = [c.key for c in candidates]
    values = dict.fromkeys(keys, 0.0)
    vectors = {keys[0]: [1.0, 0.0], keys[1]: [0.0, 1.0], keys[2]: [1.0, 0.0]}
    sound = ShadowSound(model="fake", space_id="frozen", dim=2, v1=values, v2=values,
                        missing_v1=[], missing_v2=[], missing_baseline=[], missing_positive=[], missing_negative=[],
                        raw_vectors=vectors, centered_vectors=vectors)
    report = {"pool_complete": True}
    replay_selection(report, candidates, {"v1": Profile(1, now), "v2": V2Profile(1, now)}, sound,
                     MixesCfg(size=3, reserve=0), "normal")
    assert report["selection"]["models"]["v1"]["active"] == [keys[0], keys[2], keys[1]]
    assert report["selection"]["models"]["v2"]["active"] == [keys[0], keys[2], keys[1]]
