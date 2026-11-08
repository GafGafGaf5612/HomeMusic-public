from dataclasses import replace

import numpy as np
import pytest

from app.geometry import fit_axes

from .support import running, store_vectors


def cloud(n=24, seed=3) -> dict[str, list[float]]:
    rng = np.random.default_rng(seed)
    scale = np.array([6.0, 3.0, 1.0, 0.3])
    return {f"k{i:02d}": (rng.normal(size=4) * scale + [10, 0, 0, 0]).tolist() for i in range(n)}


async def seed(c, vectors=None):
    store_vectors(c.app.state.store, vectors or cloud())
    return (await c.post("/v2/profile", json={"positives": []})).json()["space_id"]


async def project(c, space_id, **body):
    return await c.post("/v2/project", json={"space_id": space_id, **body})


async def test_global_projection_is_deterministic_and_independent_of_keys(settings):
    async with running(settings) as c:
        space_id = await seed(c)
        keys = sorted(cloud())
        one = (await project(c, space_id, keys=keys)).json()
        again = (await project(c, space_id, keys=list(reversed(keys)))).json()
        assert one["points"] == again["points"] and one["explained"] == again["explained"]
        assert one["basis"] == "global" and one["dim"] == 4 and one["missing"] == []
        subset = (await project(c, space_id, keys=keys[:5])).json()
        assert subset["explained"] == one["explained"]
        assert subset["points"] == {k: one["points"][k] for k in keys[:5]}
        assert not c.app.state.engine.model  # no model load


async def test_explained_is_a_descending_share_and_axes_are_orthogonal(settings):
    async with running(settings) as c:
        space_id = await seed(c)
        body = (await project(c, space_id, keys=sorted(cloud()))).json()
        first, second = body["explained"]
        assert 0 < second <= first <= 1 and first + second <= 1 + 1e-9
        xy = np.array(list(body["points"].values()))
        assert abs(float(np.corrcoef(xy[:, 0], xy[:, 1])[0, 1])) < 1e-6
        assert xy[:, 0].var() >= xy[:, 1].var()


def test_axis_sign_makes_the_largest_coordinate_positive():
    rng = np.random.default_rng(5)
    data = {f"k{i}": rng.normal(size=4) * [6, 3, 1, .3] for i in range(30)}
    mirrored = {k: -v for k, v in data.items()}
    for vectors in (data, mirrored):
        fitted = fit_axes(vectors)
        for slot in (0, 1):
            axis = fitted.axes[:, slot]
            assert axis[int(np.argmax(np.abs(axis)))] > 0
    # mirrored data gives the same axes, so its points are the mirror image
    a, b = fit_axes(data), fit_axes(mirrored)
    assert np.allclose(a.axes, b.axes) and a.explained == pytest.approx(b.explained)


async def test_keys_basis_follows_fit_keys(settings):
    async with running(settings) as c:
        space_id = await seed(c)
        keys = sorted(cloud())
        left = (await project(c, space_id, keys=keys, basis="keys", fit_keys=keys[:8])).json()
        right = (await project(c, space_id, keys=keys, basis="keys", fit_keys=keys[8:])).json()
        whole = (await project(c, space_id, keys=keys, basis="keys", fit_keys=keys)).json()
        glob = (await project(c, space_id, keys=keys)).json()
        assert left["points"] != right["points"] and left["explained"] != right["explained"]
        # fitting on every vector is the global basis
        for k in keys:
            assert np.allclose(whole["points"][k], glob["points"][k], atol=1e-9)


async def test_facets_project_like_a_point_and_report_cosines(settings):
    async with running(settings) as c:
        space_id = await seed(c)
        keys = sorted(cloud())
        centers = (await c.post("/v2/vectors", json={"space_id": space_id, "keys": keys[:2]})).json()["vectors"]
        facets = [{"id": f"f{i}", "center": v} for i, v in enumerate(centers.values())]
        body = (await project(c, space_id, keys=keys, facets=facets)).json()
        # a facet center equal to a point's vector lands on that point
        for i, k in enumerate(keys[:2]):
            assert np.allclose(body["facets"][f"f{i}"], body["points"][k])
            assert body["facet_cos"][k][i] == pytest.approx(1.0)
        vectors = (await c.post("/v2/vectors", json={"space_id": space_id, "keys": keys})).json()["vectors"]
        for k in keys:
            expected = [float(np.dot(vectors[k], v) / np.linalg.norm(v)) for v in centers.values()]
            assert body["facet_cos"][k] == pytest.approx(expected)


async def test_missing_keys_and_degenerate_spaces(settings):
    async with running(settings) as c:
        space_id = await seed(c, {"a": [1, 0], "b": [1, 0]})  # identical vectors: zero residuals
        body = (await project(c, space_id, keys=["a", "nope"])).json()
        assert body["points"] == {} and body["missing"] == ["a", "nope"]
        assert body["explained"] == [0, 0]
    async with running(settings) as c:
        space_id = await seed(c, {"a": [1, 0], "b": [-1, 0], "c": [0, 1]})
        body = (await project(c, space_id, keys=["a", "b", "zzz"])).json()
        assert set(body["points"]) == {"a", "b"} and body["missing"] == ["zzz"]
        assert (await project(c, space_id, keys=[], basis="keys", fit_keys=["zzz"])).json()["explained"] == [0, 0]


async def test_stale_space_and_limits(settings):
    async with running(settings) as c:
        space_id = await seed(c)
        store_vectors(c.app.state.store, {"new": [1, 2, 3, 4]})
        stale = await project(c, space_id, keys=["k00"])
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_space"
        fresh = (await c.post("/v2/profile", json={"positives": []})).json()["space_id"]
        key = "k" * 3
        bad = [
            {"keys": [key] * 5001},
            {"keys": [key], "basis": "keys"},
            {"keys": [key], "basis": "keys", "fit_keys": [key] * 5001},
            {"keys": [key], "basis": "other"},
            {"keys": [key], "facets": [{"id": str(i), "center": [1, 0, 0, 0]} for i in range(6)]},
            {"keys": [key], "facets": [{"id": "x", "center": [1, 0]}]},
            {"keys": [key], "facets": [{"id": "x", "center": [0, 0, 0, 0]}]},
        ]
        for body in bad:
            assert (await project(c, fresh, **body)).status_code == 422, body


async def test_project_requires_token(settings):
    async with running(replace(settings, token="test-token")) as c:
        response = await c.post("/v2/project", json={"space_id": "old", "keys": []})
        assert response.status_code == 401
