import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI

from app.api import system


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "_apk_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(system.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_historical_manifest_with_bom_and_legacy_download(client, tmp_path):
    (tmp_path / "HomeMusic-1.0.0-2.apk").write_bytes(b"old")
    (tmp_path / "latest.json").write_text(json.dumps({"file": "HomeMusic-1.0.0-2.apk", "versionCode": 2}), encoding="utf-8-sig")
    async with client:
        response = await client.get("/app/latest.json")
        assert response.json() == {"url": "/app/latest.apk", "versionCode": 2}
        assert (await client.get("/app/latest.apk")).content == b"old"


@pytest.mark.asyncio
async def test_immutable_artifacts_and_invalid_patch_are_filtered(client, tmp_path):
    directory = tmp_path / "artifacts"
    directory.mkdir()
    apk = b"new apk"
    digest = hashlib.sha256(apk).hexdigest()
    name = f"apk-{digest}.apk"
    (directory / name).write_bytes(apk)
    full = {"url": f"/app/artifacts/{name}", "size": len(apk), "sha256": digest}
    patch_bytes = b"delta"
    patch_hash = hashlib.sha256(patch_bytes).hexdigest()
    patch_name = f"patch-{patch_hash}.bsdiff"
    (directory / patch_name).write_bytes(patch_bytes)
    patch = {"url": f"/app/artifacts/{patch_name}", "size": len(patch_bytes), "sha256": patch_hash,
             "baseVersionCode": 2, "baseSha256": "a" * 64, "format": "bsdiff40-v1"}
    (tmp_path / "latest.json").write_text(json.dumps({"file": "HomeMusic-1.0.1-3.apk", "versionCode": 3, "size": len(apk), "sha256": digest, "full": full,
                                                      "patches": [patch, {"url": "/app/artifacts/../../secret"}]}))
    async with client:
        data = (await client.get("/app/latest.json")).json()
        assert data["full"] == full
        assert data["patches"] == [patch]
        result = await client.get(full["url"])
        assert result.content == apk
        assert "immutable" in result.headers["cache-control"]
        (directory / name).write_bytes(b"corrupt")
        assert (await client.get(full["url"])).status_code == 404
        (directory / name).write_bytes(b"short")
        assert "full" not in (await client.get("/app/latest.json")).json()


def _publish(tmp_path, *, patch_overrides=None, manifest_overrides=None, apk=b"new apk", patch_bytes=b"delta"):
    """Writes a release the way scripts/release-android.ps1 does and returns (manifest dict, full, patch)."""
    directory = tmp_path / "artifacts"
    directory.mkdir(exist_ok=True)
    digest = hashlib.sha256(apk).hexdigest()
    (directory / f"apk-{digest}.apk").write_bytes(apk)
    full = {"url": f"/app/artifacts/apk-{digest}.apk", "size": len(apk), "sha256": digest}
    patch_hash = hashlib.sha256(patch_bytes).hexdigest()
    (directory / f"patch-{patch_hash}.bsdiff").write_bytes(patch_bytes)
    patch = {"url": f"/app/artifacts/patch-{patch_hash}.bsdiff", "size": len(patch_bytes), "sha256": patch_hash,
             "baseVersionCode": 2, "baseSha256": "a" * 64, "format": "bsdiff40-v1", **(patch_overrides or {})}
    (tmp_path / "HomeMusic-1.0.1-3.apk").write_bytes(apk)
    manifest = {"file": "HomeMusic-1.0.1-3.apk", "versionCode": 3, "versionName": "1.0.1", "size": len(apk), "sha256": digest,
                "full": full, "patches": [patch], **(manifest_overrides or {})}
    (tmp_path / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, full, patch


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"baseVersionCode": 3},                 # not older than the release
    {"baseVersionCode": 0},
    {"baseSha256": "A" * 64},               # not lowercase hex
    {"baseSha256": "abc"},
    {"format": "bsdiff40-v2"},              # a format no client knows
    {"size": 4},                            # size differs from the file on disk
], ids=["base-equals-release", "base-zero", "base-uppercase", "base-short", "unknown-format", "size-mismatch"])
async def test_patch_that_clients_must_not_see_is_dropped(client, tmp_path, override):
    _publish(tmp_path, patch_overrides=override)
    async with client:
        data = (await client.get("/app/latest.json")).json()
    assert data["patches"] == []
    assert "full" in data    # the full APK stays offered


@pytest.mark.asyncio
async def test_patch_not_smaller_than_the_apk_is_dropped(client, tmp_path):
    _publish(tmp_path, apk=b"tiny", patch_bytes=b"not smaller!")
    async with client:
        assert (await client.get("/app/latest.json")).json()["patches"] == []


@pytest.mark.asyncio
async def test_full_that_disagrees_with_the_legacy_fields_hides_all_new_metadata(client, tmp_path):
    _, full, _ = _publish(tmp_path, manifest_overrides={"sha256": "b" * 64})
    async with client:
        data = (await client.get("/app/latest.json")).json()
        legacy = await client.get("/app/latest.apk")
    assert "full" not in data and "patches" not in data
    assert data["url"] == "/app/latest.apk" and data["versionCode"] == 3   # old clients still get a complete answer
    assert legacy.status_code == 200


@pytest.mark.asyncio
async def test_old_client_fields_are_unchanged_by_new_metadata(client, tmp_path):
    manifest, _, _ = _publish(tmp_path)
    async with client:
        data = (await client.get("/app/latest.json")).json()
        assert (await client.get("/app/latest.apk")).content == b"new apk"
    for key in ("versionCode", "versionName", "size", "sha256"):
        assert data[key] == manifest[key]
    assert data["url"] == "/app/latest.apk" and "file" not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "apk-" + "a" * 64 + ".bsdiff",          # kind and extension must agree
    "patch-" + "a" * 64 + ".apk",
    "apk-" + "A" * 64 + ".apk",
    "apk-" + "a" * 63 + ".apk",
    "..%2Flatest.json",
    "latest.json",
], ids=["kind-ext", "kind-ext-2", "uppercase", "short", "traversal", "plain-file"])
async def test_artifact_route_serves_only_content_addressed_names(client, tmp_path, name):
    (tmp_path / "latest.json").write_text("{}")
    async with client:
        assert (await client.get(f"/app/artifacts/{name}")).status_code == 404


@pytest.mark.asyncio
async def test_artifact_must_match_its_own_name_hash(client, tmp_path):
    directory = tmp_path / "artifacts"
    directory.mkdir()
    digest = hashlib.sha256(b"expected").hexdigest()
    (directory / f"patch-{digest}.bsdiff").write_bytes(b"other bytes")
    async with client:
        assert (await client.get(f"/app/artifacts/patch-{digest}.bsdiff")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "not json", "[]"], ids=["missing", "garbage", "not-an-object"])
async def test_missing_or_broken_manifest_is_a_404(client, tmp_path, content):
    if content is not None:
        (tmp_path / "latest.json").write_text(content)
    async with client:
        assert (await client.get("/app/latest.json")).status_code == 404
        assert (await client.get("/app/latest.apk")).status_code == 404


@pytest.mark.asyncio
async def test_manifest_cannot_point_the_legacy_download_outside_the_release_folder(client, tmp_path):
    (tmp_path.parent / "secret.apk").write_bytes(b"secret")
    (tmp_path / "latest.json").write_text(json.dumps({"file": "../secret.apk", "versionCode": 1}))
    async with client:
        assert (await client.get("/app/latest.apk")).status_code == 404
