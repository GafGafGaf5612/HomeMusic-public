"""Pending playlist additions: manual review must not hold ready songs hostage."""

import asyncio
import uuid

import httpx
import pytest
import respx
from sqlalchemy import select

from app import db
from app.jobs.downloads import process_playlist_adds
from app.models import DownloadBatch, DownloadItem, PendingPlaylistAdd, Track, User
from app.security import encrypt


@pytest.fixture
async def playlist_env(hub_env):
    db.init_engine()
    async with db.session_scope() as s:
        user = User(nd_username="qa", nd_password_enc=encrypt("qa-password"))
        s.add(user)
        await s.flush()
        user_id = user.id
    state = {"songs": ["existing"], "fail": False, "writes": 0}

    def respond(request):
        if state["fail"]:
            return httpx.Response(503)
        ep = request.url.path.rsplit("/", 1)[-1]
        p = httpx.QueryParams(request.content.decode()) if request.method == "POST" else request.url.params
        body = {"status": "ok", "version": "1.16.1"}
        if ep == "getPlaylist":
            body["playlist"] = {"id": "pl", "entry": [{"id": x} for x in state["songs"]]}
        elif ep == "updatePlaylist":
            state["songs"] += p.get_list("songIdToAdd")
            state["writes"] += 1
        elif ep == "createPlaylist":
            assert request.method == "POST"
            assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
            assert p["playlistId"] == "pl"
            state["songs"] = p.get_list("songId")
            state["writes"] += 1
        else:
            raise AssertionError(ep)
        return httpx.Response(200, json={"subsonic-response": body})

    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(side_effect=respond)
            yield user_id, state
    finally:
        await db.dispose()


async def seed(user_id, statuses):
    async with db.session_scope() as s:
        batch = DownloadBatch(user_id=user_id, origin="assistant", target_playlist_id="pl")
        s.add(batch)
        await s.flush()
        ids = []
        for pos, status in enumerate(statuses):
            item = DownloadItem(batch_id=batch.id, user_id=user_id, status=status,
                                query_json={"query": {"artist": "Artist", "title": f"Song {pos}"}})
            s.add(item)
            await s.flush()
            ids.append(item.id)
            s.add(PendingPlaylistAdd(playlist_id="pl", user_id=user_id, download_item_id=item.id, position=pos))
            if status == "done":
                await finish(s, item)
        return ids


async def finish(s, item):
    track = Track(uid=str(uuid.uuid4()), path=f"{item.id}.mp3", artist="Artist", title=f"Song {item.id}",
                  fingerprint=str(item.id), nd_song_id=f"song{item.id}")
    s.add(track)
    await s.flush()
    item.track_id, item.status = track.id, "done"


async def sync():
    async with db.session_scope() as s:
        await process_playlist_adds(s)


async def pending():
    async with db.session_scope() as s:
        return list((await s.execute(select(PendingPlaylistAdd).order_by(PendingPlaylistAdd.id))).scalars())


async def test_review_releases_ready_songs_and_retry_inserts_in_requested_order(playlist_env):
    user_id, state = playlist_env
    ids = await seed(user_id, ["done", "needs_review", "done", "done"])
    await sync()
    assert state["songs"] == ["existing", f"song{ids[0]}", f"song{ids[2]}", f"song{ids[3]}"]
    assert [p.download_item_id for p in await pending()] == ids[1:]

    # Persisted anchors survive a restart; unrelated playlist edits survive the delayed insertion.
    await db.dispose()
    db.init_engine()
    state["songs"].append("manual")
    async with db.session_scope() as s:
        await finish(s, await s.get(DownloadItem, ids[1]))
    await sync()
    assert state["songs"] == ["existing", *(f"song{i}" for i in ids), "manual"]
    assert await pending() == []
    writes = state["writes"]
    await sync()
    assert state["writes"] == writes


async def test_active_download_still_preserves_order(playlist_env):
    user_id, state = playlist_env
    ids = await seed(user_id, ["done", "downloading", "done"])
    await sync()
    assert state["songs"] == ["existing", f"song{ids[0]}"]
    async with db.session_scope() as s:
        (await s.get(DownloadItem, ids[1])).status = "needs_review"
    await sync()
    assert state["songs"] == ["existing", f"song{ids[0]}", f"song{ids[2]}"]


async def test_batches_keep_submission_order_and_cancel_releases_anchors(playlist_env):
    user_id, state = playlist_env
    first = await seed(user_id, ["needs_review", "done"])
    second = await seed(user_id, ["done", "done"])
    await sync()
    assert state["songs"] == ["existing", *(f"song{i}" for i in first[1:] + second)]
    async with db.session_scope() as s:
        (await s.get(DownloadItem, first[0])).status = "cancelled"
    await sync()
    assert await pending() == []
    assert state["writes"] == 1


async def test_navidrome_failure_retries_without_losing_pending_songs(playlist_env):
    user_id, state = playlist_env
    ids = await seed(user_id, ["needs_review", "done"])
    state["fail"] = True
    await sync()
    assert len(await pending()) == 2
    state["fail"] = False
    await sync()
    assert state["songs"] == ["existing", f"song{ids[1]}"]
    await sync()
    assert state["writes"] == 1


async def test_concurrent_completions_do_not_duplicate_playlist_songs(playlist_env):
    user_id, state = playlist_env
    ids = await seed(user_id, ["needs_review", "done", "done"])
    await asyncio.gather(sync(), sync())
    assert state["songs"] == ["existing", *(f"song{i}" for i in ids[1:])]
    assert state["writes"] == 1


@pytest.mark.parametrize("current,entries,expected", [
    (["same", "other", "same"], [(None, "new"), ("same", "same")],
     ["same", "other", "new", "same"]),
    (["manual"], [(None, "new"), ("removed", "removed")], ["manual", "new"]),
    (["a", "manual", "c"], [("a", "a"), (None, "b"), ("c", "c")], ["a", "manual", "b", "c"]),
])
def test_insert_preserves_duplicate_and_manually_edited_entries(current, entries, expected):
    from app.jobs.downloads import insert_playlist_songs

    assert insert_playlist_songs(current, entries) == expected


async def test_delayed_insertion_preserves_a_large_playlist_in_one_request(playlist_env):
    user_id, state = playlist_env
    ids = await seed(user_id, ["needs_review", "done"])
    await sync()
    tail = [f"manual{i}" for i in range(650)]
    state["songs"] += tail
    async with db.session_scope() as s:
        await finish(s, await s.get(DownloadItem, ids[0]))
    await sync()
    assert state["songs"] == ["existing", *(f"song{i}" for i in ids), *tail]
    assert state["writes"] == 2
