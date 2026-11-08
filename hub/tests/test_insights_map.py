"""The taste map (INSIGHTS.md 8.2): layers, the «Грани» layout and the embedder's projection. The embedder is mocked
with respx; the data is the synthetic users of test_insights.py."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import get_settings
from app.insights.maps import anchors, facet_layout
from app.mixes import embedder
from tests.test_insights import add, listeners, hub, wave_item  # noqa: F401

BASE = "http://embedder.test"
SPACE = {"model": "fake", "space_id": "snap", "dim": 2}
DIAGNOSTICS = {"available": 3, "zero_residual": [], "invalid": []}
FACETS = [{"id": "fa", "weight": .6, "members": ["nd:ndK"], "center": [1, 0]},
          {"id": "fb", "weight": .4, "members": ["nd:ndM"], "center": [0, 1]}]
COS = {"nd:ndK": [.9, .1], "nd:ndM": [.1, .8], "dz:300": [-.2, -.1]}
XY = {"nd:ndK": [1.0, 0.5], "nd:ndM": [-1.0, 0.25], "dz:300": [0.0, -1.0]}


def profile_body(space_id="snap"):
    return {**SPACE, "space_id": space_id, "facets": FACETS, "negatives": [], "missing": [],
            "diagnostics": DIAGNOSTICS}


def project_reply(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    keys = [k for k in body["keys"] if k in COS]
    return httpx.Response(200, json={
        **SPACE, "space_id": body["space_id"], "basis": body["basis"], "explained": [.4, .2],
        "points": {k: XY[k] for k in keys}, "facets": {"fa": [1.0, 0.0], "fb": [-1.0, 0.0]},
        "facet_cos": {k: COS[k] for k in keys}, "missing": [k for k in body["keys"] if k not in COS],
        "diagnostics": DIAGNOSTICS})


@pytest.fixture
def embedder_up(hub, monkeypatch, respx_mock):  # noqa: F811
    monkeypatch.setenv("EMBEDDER_URL", BASE)
    get_settings.cache_clear()
    profile = respx_mock.post(f"{BASE}/v2/profile").respond(json=profile_body())
    project = respx_mock.post(f"{BASE}/v2/project").mock(side_effect=project_reply)
    return SimpleNamespace(profile=profile, project=project)


async def get_map(hub, **params):  # noqa: F811
    u = hub["ids"]["anna"]
    return await hub["admin"].get(f"/api/admin/users/{u}/taste/map", params=params)


async def test_map_layers_layout_and_missing(hub, embedder_up):  # noqa: F811
    await listeners(hub["ids"])
    await add(wave_item(hub["ids"]["anna"], "w1", 2, ref="dz:lost", generator="lastfm_similar",
                        artist="Нет", title="Без звука"))
    r = await get_map(hub)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] and body["reason"] is None and body["space_id"] == "snap"
    assert body["basis"] == "global" and body["explained"] == [.4, .2]
    assert body["mix_id"] == 1 and body["wave_id"] == "w1"
    assert [(f["id"], f["color_index"], (f["x"], f["y"])) for f in body["facets"]] == [
        ("fa", 0, (1.0, 0.0)), ("fb", 1, (-1.0, 0.0))]
    assert (body["facets"][0]["fx"], body["facets"][0]["fy"]) == pytest.approx((0, 1))
    points = {x["key"]: x for x in body["points"]}
    assert set(points) == {"nd:ndK", "nd:ndM", "dz:300"}
    k = points["nd:ndK"]
    assert k["layers"] == ["profile", "mix"] and (k["x"], k["y"]) == (1.0, 0.5)
    assert k["facet_id"] == "fa" and k["facet_cos"] == [.9, .1] and k["liked"] and k["s"] > 0
    assert k["mix_position"] == 0 and k["generator"] == "ytm_radio" and k["outcome"] == "liked"
    assert k["fy"] > 0 and abs(k["fx"]) < k["fy"]  # pulled to the 12 o'clock anchor
    m = points["nd:ndM"]
    assert m["layers"] == ["profile", "wave"] and m["wave_position"] == 0 and m["facet_id"] == "fb"
    a = points["dz:300"]  # a catalog track skipped early: negative, in the mix and the wave, far from both facets
    assert a["layers"] == ["profile", "mix", "wave"] and a["facet_cos"] == [-.2, -.1]
    assert (a["fx"], a["fy"]) == (0, 0) and a["artist"] == "Ария" and a["negative"] is True and a["s"] < 0
    assert body["missing"] == [{"key": "dz:lost", "artist": "Нет", "title": "Без звука", "layers": ["wave"]}]
    sent = json.loads(embedder_up.project.calls.last.request.content)
    assert sent["basis"] == "global" and "fit_keys" not in sent and [f["id"] for f in sent["facets"]] == ["fa", "fb"]


async def test_map_personal_basis_fits_on_the_profile_and_picks_the_requested_lists(hub, embedder_up):  # noqa: F811
    await listeners(hub["ids"])
    r = await get_map(hub, basis="user", mix=1, wave="w1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] and body["basis"] == "user"
    sent = json.loads(embedder_up.project.calls.last.request.content)
    assert sent["basis"] == "keys" and "nd:ndK" in sent["fit_keys"]


async def test_map_limit_truncates_the_profile_layer(hub, embedder_up):  # noqa: F811
    await listeners(hub["ids"])
    full = (await get_map(hub, limit=2000)).json()
    cut = (await get_map(hub, limit=1)).json()
    assert full["truncated"] is False and cut["truncated"] is True
    assert {x["key"] for x in cut["points"]} <= {x["key"] for x in full["points"]}
    assert any("profile" in x["layers"] for x in cut["points"])


async def test_map_is_unavailable_without_the_embedder_or_on_v1(hub):  # noqa: F811
    await listeners(hub["ids"])
    body = (await get_map(hub)).json()
    assert body["available"] is False and body["reason"] == "embedder_off" and body["points"] == []
    assert body["basis"] == "global" and body["layout_note"]
    assert (await get_map(hub, model="v1")).json()["reason"] == "model_v1"


@pytest.mark.parametrize("status", [404, 503])
async def test_map_is_unavailable_when_the_projection_fails(hub, embedder_up, status):  # noqa: F811
    await listeners(hub["ids"])
    embedder_up.project.mock(return_value=httpx.Response(status))
    body = (await get_map(hub)).json()
    assert body["available"] is False and body["reason"] == "embedder_down" and body["points"] == []


async def test_map_retries_once_with_a_new_profile_after_a_stale_space(hub, embedder_up):  # noqa: F811
    await listeners(hub["ids"])
    stale = httpx.Response(409, json={"detail": {"code": "stale_space", "space_id": "new"}})
    embedder_up.profile.mock(side_effect=[httpx.Response(200, json=profile_body("old")),
                                          httpx.Response(200, json=profile_body("new"))])
    embedder_up.project.mock(side_effect=[stale, project_reply])
    body = (await get_map(hub)).json()
    assert body["available"] and body["space_id"] == "new"
    assert embedder_up.profile.call_count == 2 and embedder_up.project.call_count == 2
    # twice stale: the map is unavailable, no third try
    embedder_up.profile.mock(side_effect=[httpx.Response(200, json=profile_body("a")),
                                          httpx.Response(200, json=profile_body("b"))])
    embedder_up.project.mock(return_value=stale)
    again = (await get_map(hub)).json()
    assert again["available"] is False and again["reason"] == "embedder_stale_space"


async def test_map_unknown_lists_users_and_non_admins(hub, embedder_up):  # noqa: F811
    await listeners(hub["ids"])
    assert (await get_map(hub, mix=999)).status_code == 404
    assert (await get_map(hub, wave="nope")).status_code == 404
    boris = hub["ids"]["boris"]
    assert (await hub["admin"].get(f"/api/admin/users/{boris}/taste/map", params={"mix": 1})).status_code == 404
    assert (await hub["admin"].get("/api/admin/users/9999/taste/map")).status_code == 404
    assert (await hub["user"].get(f"/api/admin/users/{boris}/taste/map")).status_code == 403


async def test_track_breakdown_gets_facet_cos_from_the_projection(hub, embedder_up):  # noqa: F811
    await listeners(hub["ids"])
    u = hub["ids"]["anna"]
    r = await hub["admin"].get(f"/api/admin/users/{u}/taste/track", params={"key": "nd:ndK"})
    assert r.status_code == 200, r.text
    assert r.json()["facet_cos"] == {"fa": .9, "fb": .1}
    assert json.loads(embedder_up.project.calls.last.request.content)["keys"] == ["nd:ndK"]
    embedder_up.project.mock(return_value=httpx.Response(404))
    r = await hub["admin"].get(f"/api/admin/users/{u}/taste/track", params={"key": "nd:ndK"})
    assert r.status_code == 200 and r.json()["facet_cos"] is None


def test_facet_layout_anchors_and_center():
    ring = anchors(3)
    assert ring[0] == pytest.approx((0, 1)) and ring[1][0] > 0 > ring[1][1]  # clockwise from 12 o'clock
    # a point close to one facet only sits on its anchor
    assert facet_layout([.9, 0, 0]) == pytest.approx(ring[0])
    assert facet_layout([0, 0, .6]) == pytest.approx(ring[2])
    # no positive cosine: the center; a weak match stays near it
    assert facet_layout([-.2, -.1, 0]) == (0, 0) and facet_layout([]) == (0, 0)
    near = facet_layout([.15, 0, 0])
    assert 0 < near[1] < ring[0][1] / 2
    # two equally close facets: halfway between their anchors
    mid = facet_layout([.7, .7, 0])
    assert mid == pytest.approx(((ring[0][0] + ring[1][0]) / 2, (ring[0][1] + ring[1][1]) / 2))


# --- the client ---------------------------------------------------------------------------------------------


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(embedder, "get_settings", lambda: SimpleNamespace(embedder_url=BASE, embedder_token=""))
    async with httpx.AsyncClient(timeout=1) as http:
        yield embedder.EmbedderClient(http)


def geometry():
    return embedder.GeometryProfile(**profile_body())


async def test_client_sends_the_space_facets_and_basis(client, respx_mock):
    route = respx_mock.post(f"{BASE}/v2/project").mock(side_effect=project_reply)
    out = await client.project_v2(["nd:ndK", "dz:lost", "nd:ndK"], geometry(), basis="keys", fit_keys=["nd:ndM"])
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"space_id": "snap", "keys": ["nd:ndK", "dz:lost"], "basis": "keys", "fit_keys": ["nd:ndM"],
                    "facets": [{"id": "fa", "center": [1, 0]}, {"id": "fb", "center": [0, 1]}]}
    assert out.points == {"nd:ndK": [1.0, 0.5]} and out.missing == ["dz:lost"] and out.facet_cos["nd:ndK"] == [.9, .1]


@pytest.mark.parametrize("response,error", [
    (httpx.Response(404), embedder.EmbedderDown),
    (httpx.Response(503), embedder.EmbedderDown),
    (httpx.Response(409, json={"detail": {"code": "stale_space"}}), embedder.EmbedderSpaceExpired),
    (httpx.Response(200, json={"nonsense": 1}), embedder.EmbedderDown),
], ids=["old-service", "unavailable", "stale", "malformed"])
async def test_client_failures_map_to_down_or_expired(client, respx_mock, response, error):
    respx_mock.post(f"{BASE}/v2/project").mock(return_value=response)
    with pytest.raises(error):
        await client.project_v2(["nd:ndK"], geometry())


async def test_client_rejects_a_response_that_does_not_cover_the_request(client, respx_mock):
    def partial(request):
        reply = json.loads(project_reply(request).content)
        reply["points"], reply["facet_cos"], reply["missing"] = {}, {}, []
        return httpx.Response(200, json=reply)
    respx_mock.post(f"{BASE}/v2/project").mock(side_effect=partial)
    with pytest.raises(embedder.EmbedderDown):
        await client.project_v2(["nd:ndK"], geometry())
    with pytest.raises(embedder.EmbedderDown):
        await client.project_v2([f"k{i}" for i in range(5001)], geometry())
