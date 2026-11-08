from dataclasses import replace

import pytest

from .support import running, store_vectors


async def test_v2_profile_similarity_vectors_share_space_and_leave_v1_raw(settings):
    async with running(settings) as c:
        store_vectors(c.app.state.store, {"a": [10, 1, 0], "b": [10, -1, 0],
                                          "n": [10, 0, 1], "d": [10, 0, -1]})
        response = await c.post("/v2/profile", json={"positives": [{"key": "a", "weight": 2}],
                                                     "negatives": ["n", "missing"]})
        assert response.status_code == 200, response.text
        profile = response.json()
        assert profile["missing"] == ["missing"] and profile["negatives"] == ["n"]
        assert profile["facets"][0]["center"] == [0, 1, 0]
        space_id = profile["space_id"]
        response = await c.post("/v2/similarity", json={"space_id": space_id, "candidates": ["a", "n", "absent"],
            "facets": profile["facets"], "negatives": profile["negatives"]})
        assert response.status_code == 200, response.text
        scored = response.json()
        assert scored["space_id"] == space_id and scored["missing"] == ["absent"]
        assert {r["key"]: r["score"] for r in scored["results"]} == {"a": 1, "n": -.5}
        centered = (await c.post("/v2/vectors", json={"space_id": space_id, "keys": ["a"]})).json()
        assert centered["space_id"] == space_id and centered["vectors"]["a"] == [0, 1, 0]
        raw = (await c.post("/vectors", json={"keys": ["a"]})).json()
        assert raw["vectors"]["a"] == [10, 1, 0] and "space_id" not in raw
        assert not c.app.state.engine.model  # Geometry never loads the GPU model.
        store_vectors(c.app.state.store, {"new": [10, 1, 1]})
        for path, body in [("/v2/vectors", {"keys": ["a"]}),
                           ("/v2/similarity", {"candidates": ["a"], "facets": profile["facets"]})]:
            stale = await c.post(path, json={"space_id": space_id, **body})
            assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_space"


async def test_zero_residual_invalid_dimensions_and_empty_profile_are_finite(settings):
    async with running(settings) as c:
        store_vectors(c.app.state.store, {"a": [1, 0], "b": [1, 0], "bad": [1, 2, 3], "nan": [float("nan"), 0]})
        r = await c.post("/v2/profile", json={"positives": [{"key": "a", "weight": 1},
                                                               {"key": "bad", "weight": 1},
                                                               {"key": "nan", "weight": 1}]})
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["facets"] == [] and result["missing"] == ["a", "bad", "nan"]
        assert result["diagnostics"]["zero_residual"] == ["a"]
        assert result["diagnostics"]["invalid"] == ["bad", "nan"]
        assert (await c.post("/v2/profile", json={"positives": []})).json()["facets"] == []


@pytest.mark.parametrize("body", [
    {"positives": [{"key": "a", "weight": .5}]},
    {"positives": [{"key": "a", "weight": -1}]},
    {"positives": [{"key": "a", "weight": "NaN"}]},
    {"positives": [{"key": "a", "weight": "Infinity"}]},
    {"positives": [{"key": "", "weight": 1}]},
    {"positives": [{"key": "a", "weight": 1}] * 5001},
], ids=["small", "negative", "nan", "infinite", "empty-key", "oversize"])
async def test_profile_validation(settings, body):
    async with running(settings) as c:
        response = await c.post("/v2/profile", json=body)
        assert response.status_code == 422, response.text


async def test_all_geometry_endpoints_require_token(settings):
    async with running(replace(settings, token="test-token")) as c:
        for path, body in [("/v2/profile", {"positives": []}),
                           ("/v2/vectors", {"space_id": "old", "keys": []}),
                           ("/v2/similarity", {"space_id": "old", "candidates": [], "facets": []})]:
            response = await c.post(path, json=body)
            assert response.status_code == 401, response.text


@pytest.mark.parametrize("center", [[1], [0, 0], ["NaN", 0], [2, 0], [1] * 4097],
                         ids=["dimension", "zero", "nan", "out-of-range", "oversize"])
async def test_facet_validation(settings, center):
    async with running(settings) as c:
        store_vectors(c.app.state.store, {"a": [1, 0], "b": [-1, 0]})
        profile = (await c.post("/v2/profile", json={"positives": []})).json()
        response = await c.post("/v2/similarity", json={"space_id": profile["space_id"], "candidates": ["a"],
            "facets": [{"id": "x", "weight": 1, "members": [], "center": center}]})
        assert response.status_code == 422, response.text

async def test_shadow_compares_one_immutable_snapshot_without_queue_writes(settings):
    async with running(settings) as c:
        store = c.app.state.store
        store_vectors(store, {"a": [1, 0], "b": [0, 1], "c": [-1, 0]})
        before = store.counts()
        result = await c.post("/v2/shadow", json={"candidates": ["a", "b", "missing"],
            "baseline": [{"key": "a", "weight": 1}], "positives": [{"key": "a", "weight": 1}],
            "negatives": ["c"], "include_vectors": True})
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["model"] == "fake" and body["space_id"] and body["dim"] == 2
        assert body["raw_vectors"]["a"] == [1, 0]
        assert set(body["centered_vectors"]) == {"a", "b"}
        assert body["v1"]["a"] == 1 and body["v1"]["b"] == 0
        assert set(body["v2"]) == {"a", "b"}
        assert body["missing_v1"] == body["missing_v2"] == ["missing"]
        assert store.counts() == before
        profile = await c.post("/v2/profile", json={"positives": [{"key": "a", "weight": 1}], "negatives": ["c"]})
        assert body["space_id"] == profile.json()["space_id"]
        v1 = await c.post("/similarity", json={"candidates": ["a", "b"],
            "profile": [{"key": "a", "weight": 1}], "top": 0})
        v2 = await c.post("/v2/similarity", json={"space_id": body["space_id"],
            "candidates": ["a", "b"], "facets": profile.json()["facets"],
            "negatives": profile.json()["negatives"]})
        assert body["v1"] == {r["key"]: r["score"] for r in v1.json()["results"]}
        assert body["v2"] == {r["key"]: r["score"] for r in v2.json()["results"]}
        assert body["missing_baseline"] == body["missing_positive"] == body["missing_negative"] == []
