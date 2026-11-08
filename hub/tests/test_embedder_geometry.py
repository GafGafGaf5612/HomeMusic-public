"""The additive v2 client contract; HTTP mocks, no Hub app or database."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.mixes import embedder

BASE = "http://embedder.test"
SPACE = {"model": "fake", "space_id": "snapshot", "dim": 2}
DIAGNOSTICS = {"available": 2, "zero_residual": [], "invalid": []}


def profile_body():
    return {**SPACE, "facets": [{"id": "facet", "weight": 1, "members": ["a"], "center": [1, 0]}],
            "negatives": ["n"], "missing": [], "diagnostics": DIAGNOSTICS}


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(embedder, "get_settings", lambda: SimpleNamespace(embedder_url=BASE, embedder_token=""))
    async with httpx.AsyncClient(timeout=1) as http:
        yield embedder.EmbedderClient(http)


async def test_profile_and_vectors_use_same_space(client, respx_mock):
    route = respx_mock.post(f"{BASE}/v2/profile").respond(json=profile_body())
    sim = respx_mock.post(f"{BASE}/v2/similarity").respond(json={**SPACE, "results": [
        {"key": "x", "pos": .8, "neg": .2, "score": .7}], "missing": ["missing"], "diagnostics": DIAGNOSTICS})
    vectors = respx_mock.post(f"{BASE}/v2/vectors").respond(json={**SPACE, "vectors": {"x": [1, 0]},
                                                              "missing": [], "diagnostics": DIAGNOSTICS})
    profile = await client.profile_v2({"a": 2}, ["n"])
    scores = await client.similarity_v2(["x", "missing"], profile)
    found = await client.vectors_v2(["x"], profile)
    assert json.loads(route.calls.last.request.content)["positives"] == [{"key": "a", "weight": 2}]
    assert json.loads(sim.calls.last.request.content)["space_id"] == profile.space_id
    assert json.loads(vectors.calls.last.request.content)["space_id"] == profile.space_id
    assert scores.scores == {"x": .7} and found.vectors == {"x": [1, 0]}


@pytest.mark.parametrize("status", [404, 503], ids=["old-service", "unavailable"])
async def test_v2_unavailable_uses_existing_down_fallback(client, respx_mock, status):
    route = respx_mock.post(f"{BASE}/v2/profile").respond(status)
    with pytest.raises(embedder.EmbedderDown):
        await client.profile_v2({"a": 1}, [])
    assert route.call_count == 1


async def test_stale_space_requires_refresh_without_partial_batch_result(client, respx_mock, monkeypatch):
    monkeypatch.setattr(embedder, "MAX_ITEMS", 1)
    route = respx_mock.post(f"{BASE}/v2/vectors").mock(side_effect=[
        httpx.Response(200, json={**SPACE, "vectors": {"a": [1, 0]}, "missing": [], "diagnostics": DIAGNOSTICS}),
        httpx.Response(409, json={"detail": {"code": "stale_space", "space_id": "new"}}),
    ])
    with pytest.raises(embedder.EmbedderSpaceExpired):
        await client.vectors_v2(["a", "b"], embedder.GeometrySpace(**SPACE))
    assert route.call_count == 2


@pytest.mark.parametrize("change", [{"space_id": "other"}, {"model": "other"}, {"dim": 3}],
                         ids=["id", "model", "dimension"])
async def test_silent_space_change_is_rejected(client, respx_mock, change):
    respx_mock.post(f"{BASE}/v2/vectors").respond(json={**SPACE, **change, "vectors": {},
                                                    "missing": [], "diagnostics": DIAGNOSTICS})
    with pytest.raises(embedder.EmbedderSpaceExpired):
        await client.vectors_v2(["a"], embedder.GeometrySpace(**SPACE))


@pytest.mark.parametrize("change", [{"facets": [{"id": "x", "weight": 1, "members": [], "center": [1]}]},
                                    {"dim": "invalid"}, {"space_id": ""}], ids=["dimension", "malformed", "empty-space"])
async def test_malformed_geometry_uses_down_fallback(client, respx_mock, change):
    respx_mock.post(f"{BASE}/v2/profile").respond(json={**profile_body(), **change})
    with pytest.raises(embedder.EmbedderDown):
        await client.profile_v2({"a": 1}, [])


async def test_empty_requests_do_not_call_service(client):
    profile = embedder.GeometryProfile(**profile_body())
    assert (await client.similarity_v2([], profile)).scores == {}
    assert (await client.vectors_v2([], profile)).vectors == {}


def batch_body(endpoint, keys, missing=()):
    payload = {"vectors": {k: [1, 0] for k in keys}} if endpoint == "vectors" else {
        "results": [{"key": k, "pos": .8, "neg": .2, "score": .7} for k in keys]}
    return {**SPACE, **payload, "missing": list(missing), "diagnostics": DIAGNOSTICS}


async def request_batch(client, endpoint, keys):
    profile = embedder.GeometryProfile(**profile_body())
    if endpoint == "vectors":
        return await client.vectors_v2(keys, profile)
    return await client.similarity_v2(keys, profile)


@pytest.mark.parametrize("endpoint", ["similarity", "vectors"])
async def test_incomplete_late_batch_raises_without_partial_result(client, respx_mock, monkeypatch, endpoint):
    monkeypatch.setattr(embedder, "MAX_ITEMS", 1)
    route = respx_mock.post(f"{BASE}/v2/{endpoint}").mock(side_effect=[
        httpx.Response(200, json=batch_body(endpoint, ["a"])),
        httpx.Response(200, json=batch_body(endpoint, [])),
    ])
    with pytest.raises(embedder.EmbedderDown):
        await request_batch(client, endpoint, ["a", "b"])
    assert route.call_count == 2


@pytest.mark.parametrize("endpoint", ["similarity", "vectors"])
async def test_explicit_missing_and_duplicate_input_keys_are_valid(client, respx_mock, monkeypatch, endpoint):
    monkeypatch.setattr(embedder, "MAX_ITEMS", 2)
    route = respx_mock.post(f"{BASE}/v2/{endpoint}").mock(side_effect=[
        httpx.Response(200, json=batch_body(endpoint, ["a"])),
        httpx.Response(200, json=batch_body(endpoint, [], ["b"])),
    ])
    result = await request_batch(client, endpoint, ["a", "a", "b", "b"])
    assert (result.vectors if endpoint == "vectors" else result.scores) == (
        {"a": [1, 0]} if endpoint == "vectors" else {"a": .7})
    assert result.missing == ["b"]
    assert route.call_count == 2


@pytest.mark.parametrize("endpoint", ["similarity", "vectors"])
@pytest.mark.parametrize("keys,missing", [(["a", "unexpected"], []), (["a"], ["unexpected"]),
                                         (["a"], ["a"]), ([], ["a", "a"])],
                         ids=["unexpected-result", "unexpected-missing", "found-and-missing", "duplicate-missing"])
async def test_unexpected_or_contradictory_batch_keys_are_rejected(client, respx_mock, endpoint, keys, missing):
    respx_mock.post(f"{BASE}/v2/{endpoint}").respond(json=batch_body(endpoint, keys, missing))
    with pytest.raises(embedder.EmbedderDown):
        await request_batch(client, endpoint, ["a"])


async def test_duplicate_similarity_results_are_rejected(client, respx_mock):
    respx_mock.post(f"{BASE}/v2/similarity").respond(json=batch_body("similarity", ["a", "a"]))
    with pytest.raises(embedder.EmbedderDown):
        await request_batch(client, "similarity", ["a", "a"])


async def test_similarity_missing_can_include_requested_negative_keys(client, respx_mock):
    respx_mock.post(f"{BASE}/v2/similarity").respond(json=batch_body("similarity", ["a"], ["n"]))
    result = await request_batch(client, "similarity", ["a"])
    assert result.scores == {"a": .7}
    assert result.missing == ["n"]
