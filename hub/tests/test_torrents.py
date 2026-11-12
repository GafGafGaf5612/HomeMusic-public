"""Releases from rutracker: .torrent parsing, Torznab, file choice, and the flow Jackett → qBittorrent → library
with both services mocked (README, «Раздачи с rutracker»)."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from tests.support import FakeDeezer, app_client

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
KEY = "jk-secret"
JACKETT = "http://jackett.test"
QBIT = "http://qbit.test"
DL_LINK = f"{JACKETT}/dl/rutracker/?jackett_apikey={KEY}&path=Q2lwaGVy&file=Kino"
RELEASE = "(Rock) [CD] Кино - Группа крови - 1988, FLAC (tracks+.cue), lossless"
NAME = "Кино - Группа крови (1988)"


def torrent_bytes(files: list[tuple[str, int]], name: str = NAME) -> bytes:
    from app.torrents.bencode import bencode

    info = {"name": name, "piece length": 16384, "pieces": b"\x00" * 20,
            "files": [{"length": size, "path": path.split("/")} for path, size in files]}
    return bencode({"announce": "http://bt.test/announce", "info": info})


# --------------------------------------------------------------------------- units


def test_parse_torrent_hash_files_and_padding():
    from app.torrents.bencode import BencodeError, bencode, parse_torrent

    info = {"name": "Альбом", "piece length": 16384, "pieces": b"\x00" * 20, "files": [
        {"length": 5, "path": ["CD1", "01 - Песня.flac"]},
        {"length": 3, "path": [".pad", "3"], "attr": "p"},
        {"length": 7, "path": ["cover.jpg"]},
    ]}
    data = bencode({"announce": "x", "info": info})
    meta = parse_torrent(data)
    assert meta.info_hash == hashlib.sha1(bencode(info)).hexdigest()
    assert [(f.path, f.size) for f in meta.files] == [("Альбом/CD1/01 - Песня.flac", 5), ("Альбом/cover.jpg", 7)]
    assert meta.size == 12

    single = parse_torrent(bencode({"info": {"name": "one.mp3", "length": 9, "piece length": 1, "pieces": b""}}))
    assert [(f.path, f.size) for f in single.files] == [("one.mp3", 9)]

    for bad in (b"", b"d4:infoi1ee", b"<html>", data + b"x",
                bencode({"info": {**info, "files": [{"length": 1, "path": ["..", "x"]}]}})):
        with pytest.raises(BencodeError):
            parse_torrent(bad)


@pytest.mark.parametrize(("title", "expect"), [
    ("Кино - Группа крови - 1988, FLAC (tracks+.cue), lossless",
     {"codec": "FLAC", "lossless": True, "layout": "tracks", "year": 1988, "hires": False}),
    ("(Rock) Кино - Дискография (1982-1990), APE (image+.cue), lossless",
     {"codec": "APE", "lossless": True, "layout": "image", "year": None, "discography": True}),
    ("Artist - Album - 2020, MP3, 320 kbps", {"codec": "MP3", "bitrate_kbps": 320, "layout": "tracks", "lossless": False}),
    ("Artist - Album - 2021, FLAC (tracks), 24 bit/96 kHz, lossless", {"codec": "FLAC", "hires": True}),
    ("Artist - Album - 2019, WavPack (image+.cue)", {"codec": "WavPack", "layout": "image"}),
])
def test_release_title(title, expect):
    from app.torrents.jackett import parse_release_title

    info = parse_release_title(title).model_dump()
    assert {k: info[k] for k in expect} == expect


TORZNAB = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>
<item><title>{RELEASE}</title><guid>https://rutracker.org/forum/viewtopic.php?t=1</guid>
 <link>{DL_LINK.replace("&", "&amp;")}</link><comments>https://rutracker.org/forum/viewtopic.php?t=1</comments>
 <pubDate>Mon, 03 Mar 2014 10:00:00 +0300</pubDate><size>500000000</size>
 <torznab:attr name="category" value="3040"/><torznab:attr name="seeders" value="5"/>
 <torznab:attr name="peers" value="7"/><torznab:attr name="grabs" value="900"/></item>
<item><title>Кино - Дискография, APE (image+.cue), lossless</title>
 <link>{JACKETT}/dl/rutracker/?jackett_apikey={KEY}&amp;path=2</link><size>9000000000</size>
 <torznab:attr name="seeders" value="50"/><torznab:attr name="peers" value="50"/></item>
<item><title>Кино - Группа крови - 1988, MP3, 320 kbps</title>
 <link>{JACKETT}/dl/rutracker/?jackett_apikey={KEY}&amp;path=3</link>
 <torznab:attr name="seeders" value="10"/></item>
<item><title>Magnet only</title><link>magnet:?xt=urn:btih:abc</link></item>
</channel></rss>"""


def test_torznab_strips_the_api_key_and_skips_magnets():
    from app.torrents.jackett import JackettError, parse_torznab

    found = parse_torznab(TORZNAB.encode())
    assert next(r.title for r in found) == RELEASE and len(found) == 3
    r = found[0]
    assert KEY not in r.link and "path=Q2lwaGVy" in r.link
    assert (r.seeders, r.leechers, r.grabs, r.size, r.categories) == (5, 2, 900, 500000000, [3040])
    assert r.details_url.endswith("t=1") and r.published.year == 2014
    with pytest.raises(JackettError, match="Invalid API Key"):
        parse_torznab(b'<error code="100" description="Invalid API Key"/>')


def test_file_choice_images_formats_and_covers():
    from app.torrents.bencode import TorrentFile, TorrentMeta
    from app.torrents.core import SelectionError, choose_files, list_files

    meta = TorrentMeta("0" * 40, "R", [
        TorrentFile("R/Album/01 - A.flac", 10), TorrentFile("R/Album/02 - B.flac", 10),
        TorrentFile("R/Album/Album.cue", 1), TorrentFile("R/Album/cover.jpg", 2), TorrentFile("R/Album/back.jpg", 2),
        TorrentFile("R/Live/CDImage.flac", 100), TorrentFile("R/Live/CDImage.cue", 1),
        TorrentFile("R/Bonus/01 - C.ape", 10), TorrentFile("R/rip.log", 1),
    ])
    files = {f.path: f for f in list_files(meta)}
    assert files["R/Album/01 - A.flac"].importable and not files["R/Album/01 - A.flac"].image  # tracks+.cue
    assert files["R/Live/CDImage.flac"].image and not files["R/Live/CDImage.flac"].importable
    assert files["R/Bonus/01 - C.ape"].kind == "audio" and not files["R/Bonus/01 - C.ape"].importable
    assert files["R/rip.log"].kind == "other"

    chosen = [f.path for f in choose_files(list(files.values()), None)]
    assert chosen == ["R/Album/01 - A.flac", "R/Album/02 - B.flac", "R/Album/cover.jpg"]
    assert next(f.path for f in choose_files(list(files.values()), ["R/Album/02 - B.flac"])) == "R/Album/02 - B.flac"
    with pytest.raises(SelectionError, match="образ"):
        choose_files(list(files.values()), ["R/Live/CDImage.flac"])
    with pytest.raises(SelectionError, match="Нет такого файла"):
        choose_files(list(files.values()), ["R/other.flac"])
    only_image = [f for f in files.values() if f.path.startswith("R/Live")]
    with pytest.raises(SelectionError, match="нарезка"):
        choose_files(only_image, None)


def test_release_title_and_file_names_as_fallback_tags():
    from app.torrents.core import parse_filename, release_artist_album

    assert release_artist_album(RELEASE) == ("Кино", "Группа крови")
    assert release_artist_album("Artist - Album (2001) [Remaster], MP3") == ("Artist", "Album")
    assert release_artist_album("Сборник") == ("Сборник", None)
    assert parse_filename("01 - Первая.flac") == {"track_no": 1, "title": "Первая"}
    assert parse_filename("2-03. Кино - Вторая.mp3") == {"disc_no": 2, "track_no": 3, "artist": "Кино", "title": "Вторая"}
    assert parse_filename("Bonus.flac") == {"title": "Bonus"}


async def test_qbit_logs_in_again_when_its_session_expires():
    from app.torrents.qbit import QbitClient

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            assert request.headers["referer"] == QBIT
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=s2; path=/"})
        if "SID=s2" not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, text="v5.3.0")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 headers={"Referer": QBIT, "Origin": QBIT}) as http:
        q = QbitClient(QBIT, "admin", "pw", client=http)
        q._logged_in = True  # a session from before qBittorrent restarted
        http.cookies.set("SID", "old")
        assert await q.version() == "v5.3.0"
    assert calls == ["/api/v2/app/version", "/api/v2/auth/login", "/api/v2/app/version"]


# --------------------------------------------------------------------------- the flow through the API


def make_flac(path: Path, *, title: str, track: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-metadata", f"title={title}", "-metadata", "artist=Кино", "-metadata", "album=Группа крови",
         "-metadata", f"track={track}", "-c:a", "flac", str(path)],
        check=True,
    )
    return path


class FakeQbit:
    """qBittorrent's Web API over a dict of torrents (added from the test's .torrent)."""

    def __init__(self, data: bytes):
        from app.torrents.bencode import parse_torrent

        self.data = data
        self.meta = parse_torrent(data)
        self.states = [2] * (self.meta.size // self.meta.piece_length + 1)  # every piece done (a test may change it)
        self.torrents: dict[str, dict] = {}
        self.added: list[bytes] = []
        self.deleted: list[tuple[str, str]] = []
        self.now = 1_000_000  # added_on of the next torrent

    def magnet(self) -> str:
        return f"magnet:?xt=urn:btih:{self.meta.info_hash.upper()}&dn=Kino&tr=http%3A%2F%2Fbt.test%2Fannounce"

    def handler(self, request: httpx.Request) -> httpx.Response:
        op = request.url.path.removeprefix("/api/v2/")
        multipart = request.headers.get("content-type", "").startswith("multipart/")
        form = parse_qs(request.content.decode(errors="replace")) if request.method == "POST" and not multipart else {}
        if op == "torrents/add" and "urls" in form:  # a magnet: no metadata until peers send it (`metadata()`)
            from app.torrents.jackett import magnet_info_hash

            assert form["stopCondition"] == ["MetadataReceived"] and form["savepath"] == ["/downloads"]
            self.added.append(request.content)
            self.torrents[magnet_info_hash(form["urls"][0])] = {
                "state": "metaDL", "progress": 0.0, "amount_left": self.meta.size, "meta": False,
                "prio": {i: 1 for i in range(len(self.meta.files))}, "tags": set(form["tags"][0].split(",")),
                "added_on": self.now,
            }
            return httpx.Response(200, text="Ok.")
        if op == "torrents/add":
            self.added.append(request.content)
            assert b'name="stopped"' in request.content and b"/downloads" in request.content
            tags = {"homemusic"} | ({"homemusic-preview"} if b"homemusic-preview" in request.content else set())
            self.torrents[self.meta.info_hash] = {
                "state": "stoppedDL", "progress": 0.0, "amount_left": self.meta.size, "meta": True,
                "prio": {i: 1 for i in range(len(self.meta.files))}, "tags": tags, "added_on": self.now,
            }
            return httpx.Response(200, text="Ok.")
        if op == "torrents/info":
            hashes = request.url.params.get("hashes")
            return httpx.Response(200, json=[
                {"hash": h, "state": t["state"], "progress": t["progress"], "amount_left": t["amount_left"],
                 "num_seeds": 4, "dlspeed": 1000, "eta": 60, "tags": ", ".join(sorted(t["tags"])),
                 "added_on": t["added_on"], "category": "homemusic", "seq_dl": t.get("seq", False),
                 "f_l_piece_prio": t.get("fl", False), "last_activity": t.get("active", 0)}
                for h, t in self.torrents.items() if hashes is None or h in hashes.split("|")])
        if op == "torrents/files":
            t = self.torrents.get(request.url.params["hash"])
            if t is None:
                return httpx.Response(404)
            if not t["meta"]:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"index": i, "name": f.path, "size": f.size, "priority": t["prio"][i]}
                                             for i, f in enumerate(self.meta.files)])
        if op == "torrents/filePrio":
            t = self.torrents[form["hash"][0]]
            for i in form["id"][0].split("|"):
                t["prio"][int(i)] = int(form["priority"][0])
            return httpx.Response(200)
        if op == "torrents/export":
            t = self.torrents.get(request.url.params["hash"])
            return httpx.Response(200, content=self.data) if t and t["meta"] else httpx.Response(409)
        if op == "torrents/pieceStates":
            return httpx.Response(200, json=self.states)
        if op in ("torrents/toggleSequentialDownload", "torrents/toggleFirstLastPiecePrio"):
            t = self.torrents[form["hashes"][0]]
            key = "seq" if op.endswith("SequentialDownload") else "fl"
            t[key] = not t.get(key, False)
            return httpx.Response(200)
        if op == "torrents/removeTags":
            self.torrents[form["hashes"][0]]["tags"] -= set(form["tags"][0].split(","))
            return httpx.Response(200)
        if op == "torrents/start":
            self.torrents[form["hashes"][0]]["state"] = "downloading"
            return httpx.Response(200)
        if op == "torrents/delete":
            self.torrents.pop(form["hashes"][0], None)
            self.deleted.append((form["hashes"][0], form["deleteFiles"][0]))
            return httpx.Response(200)
        return httpx.Response(404)

    def metadata(self) -> None:
        """Peers sent the magnet's metadata: qBittorrent lists the files and stops (stopCondition)."""
        for t in self.torrents.values():
            t.update(meta=True, state="stoppedDL")

    def finish(self) -> None:
        for t in self.torrents.values():
            t.update(state="stalledUP", progress=1.0, amount_left=0)

    def wanted(self) -> list[str]:
        t = self.torrents[self.meta.info_hash]
        return [f.path for i, f in enumerate(self.meta.files) if t["prio"][i] > 0]


# inside the release folder; qBittorrent and the Hub name them "<NAME>/<file>" (`FILES`)
IN_RELEASE = [("01 - Первая.flac", 3000), ("02 - Вторая.flac", 3000), (f"{NAME}.cue", 300), ("cover.jpg", 100),
              ("rip.log", 50)]
FILES = [(f"{NAME}/{path}", size) for path, size in IN_RELEASE]


@pytest.fixture
async def env(hub_env, monkeypatch):
    from app import config
    from app.main import create_app
    from app.services import svc

    monkeypatch.setenv("JACKETT_API_KEY", KEY)
    monkeypatch.setenv("JACKETT_URL", JACKETT)
    monkeypatch.setenv("QBIT_URL", QBIT)
    monkeypatch.setenv("QBIT_PASSWORD", "")
    config.get_settings.cache_clear()  # read before these variables were set
    data = torrent_bytes(IN_RELEASE)
    qbit = FakeQbit(data)
    state = {"dl": 0}

    def nd(request: httpx.Request) -> httpx.Response:
        ep = request.url.path.rsplit("/", 1)[-1]
        params = httpx.QueryParams(request.content.decode()) if request.method == "POST" else request.url.params
        ok = {"status": "ok", "version": "1.16.1"}
        if ep == "getUser":
            return httpx.Response(200, json={"subsonic-response": {**ok, "user": {"adminRole": params["u"] == "admin"}}})
        return httpx.Response(200, json={"subsonic-response": ok})

    def jackett(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/torznab/api"):
            assert request.url.params["apikey"] == KEY and request.url.params["cat"] == "3000"
            return httpx.Response(200, content=TORZNAB.encode(), headers={"content-type": "application/rss+xml"})
        if request.url.path == "/dl/magnetonly/":  # the indexer has no .torrent: Jackett redirects to the magnet
            return httpx.Response(302, headers={"location": qbit.magnet()})
        if request.url.path == "/dl/broken/":  # Jackett failed to get the .torrent from the tracker
            return httpx.Response(302, headers={"location": "magnet:?null"})
        if request.url.path == "/dl/rutracker/":
            assert request.url.params["jackett_apikey"] == KEY
            state["dl"] += 1
            return httpx.Response(200, content=data, headers={"content-type": "application/x-bittorrent"})
        return httpx.Response(404)

    app = create_app()
    async with app.router.lifespan_context(app):
        svc.deezer = FakeDeezer(duration_s=2)
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(side_effect=nd)
            mock.route(host="jackett.test").mock(side_effect=jackett)
            mock.route(host="qbit.test").mock(side_effect=qbit.handler)
            async with app_client(app) as client:
                yield {"client": client, "qbit": qbit, "music": hub_env, "app": app, "state": state}


def release_link() -> str:
    from app.torrents.jackett import strip_apikey

    return strip_apikey(DL_LINK)


async def run_job() -> None:
    from app.jobs.queue import _claim, run_job

    job = await _claim("test")
    assert job is not None and job.kind == "torrent_import"
    await run_job(job)


async def test_search_lists_importable_seeded_releases_first(env):
    c = env["client"]
    assert (await c.get("/api/torrents/config")).json() == {"enabled": True, "sub": "rutracker через Jackett → qBittorrent"}
    r = await c.get("/api/torrents/search", params={"q": "Кино Группа крови"})
    assert r.status_code == 200, r.text
    rel = r.json()["releases"]
    assert [x["seeders"] for x in rel] == [10, 5, 50]  # the APE image is last despite its 50 seeders
    assert rel[2]["importable"] is False and rel[1]["importable"] is True and rel[1]["lossless"] is True
    assert all(KEY not in x["link"] for x in rel)

    files = (await c.get("/api/torrents/files", params={"link": rel[1]["link"]})).json()
    assert files["name"] == NAME
    assert [f["path"].rsplit("/", 1)[-1] for f in files["files"] if f["importable"]] == ["01 - Первая.flac", "02 - Вторая.flac"]

    r = await c.get("/api/torrents/files", params={"link": "http://evil.test/dl/x?path=1"})
    assert r.status_code == 502 and "не на этот Jackett" in r.json()["detail"]


@pytest.mark.skipif(shutil.which(FFMPEG) is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("keep_seeding", [True, False])
async def test_download_chosen_files_then_import_by_the_catalog_album(env, keep_seeding):
    from sqlalchemy import select

    from app import config
    from app.db import session_scope
    from app.library.tagger import read_tags
    from app.models import Track
    from app.torrents.core import poll_once

    config.get_settings().cfg.torrents.keep_seeding = keep_seeding
    c, qbit, music = env["client"], env["qbit"], env["music"]
    r = await c.post("/api/torrents", json={"link": release_link(), "title": RELEASE, "album_ref": "dz:10",
                                            "details_url": "https://rutracker.org/forum/viewtopic.php?t=1"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "downloading" and t["files"] == 2
    assert env["state"]["dl"] == 1  # the .torrent is fetched once
    # only the tracks and the cover are wanted: no .cue, no log
    assert qbit.wanted() == [FILES[0][0], FILES[1][0], FILES[3][0]]
    assert qbit.torrents[qbit.meta.info_hash]["state"] == "downloading"

    r = await c.post("/api/torrents", json={"link": release_link(), "title": RELEASE})
    assert r.status_code == 409

    assert await poll_once() == 0
    assert (await c.get("/api/torrents")).json()[0]["seeds"] == 4

    root = music / "staging" / "torrents"
    make_flac(root / FILES[0][0], title="Первая", track=1)
    make_flac(root / FILES[1][0], title="Вторая", track=2)
    qbit.finish()
    assert await poll_once() == 1
    assert (await c.get("/api/torrents")).json()[0]["status"] == "importing"
    await run_job()

    t = (await c.get("/api/torrents")).json()[0]
    assert t["status"] == "done", t
    assert (t["imported"], t["warnings"]) == (2, [])
    async with session_scope() as s:
        tracks = list((await s.execute(select(Track).order_by(Track.track_no))).scalars())
    assert [(x.artist, x.title, x.track_no, x.source) for x in tracks] == [
        ("Кино", "Группа крови", 1, "torrent"),  # track 1 is in the catalog album: its title and ids
        ("Кино", "Вторая", 2, "torrent"),  # track 2 is not: the file's own tags
    ]
    assert tracks[0].deezer_track_id == "100" and tracks[0].album_id == tracks[1].album_id
    lib = music / "library"
    assert read_tags(lib / tracks[0].path)["title"] == "Группа крови"
    assert (root / FILES[0][0]).is_file() is keep_seeding  # the original seeds on, or goes with the torrent
    assert not (music / "staging" / "torrent_imports" / str(t["id"])).exists()
    assert qbit.deleted == ([] if keep_seeding else [(qbit.meta.info_hash, "true")])


async def test_cancel_and_a_torrent_removed_in_qbittorrent(env):
    from app.torrents.core import poll_once

    c, qbit = env["client"], env["qbit"]
    files = (await c.get("/api/torrents/files", params={"link": release_link()})).json()["files"]
    one = [f["path"] for f in files if f["importable"]][1:]
    r = await c.post("/api/torrents", json={"link": release_link(), "title": RELEASE, "files": one})
    assert r.status_code == 200, r.text
    assert qbit.wanted() == [FILES[1][0], FILES[3][0]]

    tid = r.json()["id"]
    assert (await c.delete(f"/api/torrents/{tid}")).status_code == 200
    assert qbit.deleted == [(qbit.meta.info_hash, "true")]
    assert (await c.get("/api/torrents")).json()[0]["status"] == "cancelled"

    r = await c.post("/api/torrents", json={"link": release_link(), "title": RELEASE})
    assert r.status_code == 200
    qbit.torrents.clear()  # removed by hand in qBittorrent's UI
    await poll_once()
    t = (await c.get("/api/torrents")).json()[0]
    assert t["status"] == "failed" and "qBittorrent" in t["error"]

    r = await c.post("/api/torrents", json={"link": release_link(), "title": RELEASE, "files": [f"{NAME}/rip.log"]})
    assert r.status_code == 400


async def test_only_the_admin_and_only_when_set_up(env, hub_env):
    async with app_client(env["app"], login=("user", "pw")) as other:
        assert (await other.get("/api/torrents/config")).json()["enabled"] is False
        assert (await other.get("/api/torrents/search", params={"q": "x"})).status_code == 403


async def test_off_without_the_jackett_key(hub_env):
    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(return_value=httpx.Response(200, json={
                "subsonic-response": {"status": "ok", "version": "1.16.1", "user": {"adminRole": True}}}))
            async with app_client(app) as client:
                assert (await client.get("/api/torrents/config")).json()["enabled"] is False
                assert (await client.get("/api/torrents/search", params={"q": "x"})).status_code == 404


# --------------------------------------------------------------------------- magnets (public trackers: no .torrent)


def test_magnet_info_hash_hex_and_base32():
    from app.torrents.jackett import magnet_info_hash

    hx = "c653db60109b1a79332ca47a29a81ac55edf6eb7"
    b32 = base64.b32encode(bytes.fromhex(hx)).decode()
    assert magnet_info_hash(f"magnet:?xt=urn:btih:{hx.upper()}&dn=x") == hx
    assert magnet_info_hash(f"magnet:?dn=x&xt=urn:btih:{b32}") == hx
    for bad in ("magnet:?xt=urn:btih:abc", "magnet:?dn=x", "http://x/?xt=urn:btih:" + hx):
        with pytest.raises(ValueError):
            magnet_info_hash(bad)


def test_torznab_magnet_only_release():
    from app.torrents.jackett import parse_torznab

    hx = "c653db60109b1a79332ca47a29a81ac55edf6eb7"
    magnet = f"magnet:?xt=urn:btih:{hx}&amp;tr=http%3A%2F%2Frutracker.ru%2Fbt%2Fannounce.php"
    xml = f"""<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
      <title>(Thrash Metal) Artist - Album - 2008, FLAC (tracks+.cue), lossless</title><guid>{magnet}</guid>
      <comments>http://rutracker.ru/viewtopic.php?t=198062</comments><size>538554595</size><link>{magnet}</link>
      <torznab:attr name="seeders" value="2"/><torznab:attr name="peers" value="2"/>
      <torznab:attr name="infohash" value="{hx}"/></item></channel></rss>"""
    [r] = parse_torznab(xml)
    assert r.magnet and r.info_hash == hx and r.link.startswith("magnet:?xt=urn:btih:")
    assert r.details_url == "http://rutracker.ru/viewtopic.php?t=198062" and r.seeders == 2


async def test_magnet_files_come_from_peers_then_download_only_the_chosen(env, monkeypatch):
    from app.torrents import core

    monkeypatch.setattr(core, "MAGNET_WAIT_S", 0)
    c, qbit = env["client"], env["qbit"]
    magnet = qbit.magnet()

    r = await c.get("/api/torrents/files", params={"link": magnet})
    assert r.status_code == 200 and r.json()["pending"] is True and r.json()["seeds"] == 4
    assert qbit.torrents[qbit.meta.info_hash]["tags"] == {"homemusic", core.PREVIEW_TAG}
    r = await c.post("/api/torrents", json={"link": magnet, "title": RELEASE})
    assert r.status_code == 409 and "Список файлов" in r.json()["detail"]

    qbit.metadata()
    files = (await c.get("/api/torrents/files", params={"link": magnet})).json()
    assert not files["pending"] and files["name"] == NAME
    assert [f["path"] for f in files["files"] if f["importable"]] == [FILES[0][0], FILES[1][0]]
    assert len(qbit.added) == 1  # the preview is reused, not added again

    r = await c.post("/api/torrents", json={"link": magnet, "title": RELEASE, "files": [FILES[1][0]]})
    assert r.status_code == 200, r.text
    tq = qbit.torrents[qbit.meta.info_hash]
    assert qbit.wanted() == [FILES[1][0], FILES[3][0]]  # the preview wanted every file: now only the chosen
    assert tq["state"] == "downloading" and tq["tags"] == {"homemusic"}
    assert env["state"]["dl"] == 0  # no .torrent from Jackett

    r = await c.get("/api/torrents/files", params={"link": "magnet:?dn=no-hash"})
    assert r.status_code == 400


async def test_stale_magnet_previews_are_removed(env, monkeypatch):
    from app.torrents import core

    monkeypatch.setattr(core, "MAGNET_WAIT_S", 0)
    c, qbit = env["client"], env["qbit"]
    await c.get("/api/torrents/files", params={"link": qbit.magnet()})
    h = qbit.meta.info_hash
    assert await core.cleanup_previews(now=qbit.now + 60) == 0  # fresh: the PWA may still be asking
    assert await core.cleanup_previews(now=qbit.now + core.PREVIEW_TTL_S + 1) == 1
    assert qbit.deleted == [(h, "true")] and h not in qbit.torrents

    qbit.metadata()  # a preview that became a download is kept
    await c.get("/api/torrents/files", params={"link": qbit.magnet()})
    qbit.metadata()
    assert (await c.post("/api/torrents", json={"link": qbit.magnet(), "title": RELEASE})).status_code == 200
    assert await core.cleanup_previews(now=qbit.now + core.PREVIEW_TTL_S + 1) == 0



def test_one_release_per_info_hash_the_most_seeded_torrent_first():
    from app.torrents.jackett import Release, merge_duplicates

    h = "c653db60109b1a79332ca47a29a81ac55edf6eb7"
    a = Release(title="A", link=f"magnet:?xt=urn:btih:{h}", magnet=True, info_hash=h, seeders=5, tracker="X")
    b = Release(title="A (copy)", link="http://jackett.test/dl/y/?path=1", info_hash=h, seeders=5, tracker="Y")
    c = Release(title="A", link=f"magnet:?xt=urn:btih:{h}", magnet=True, info_hash=h, seeders=9, tracker="Z")
    d = Release(title="Other", link="http://jackett.test/dl/y/?path=2", seeders=1)
    merged = merge_duplicates([a, b, d])
    assert [r.tracker for r in merged] == ["Y", None]  # same seeders: the .torrent wins over the magnet
    assert [r.tracker for r in merge_duplicates([a, b, c, d])] == ["Z", None]


async def test_a_torrent_link_that_redirects_to_a_magnet_goes_the_magnet_way(env, monkeypatch):
    from app.torrents import core

    monkeypatch.setattr(core, "MAGNET_WAIT_S", 0)
    c, qbit = env["client"], env["qbit"]
    link = f"{JACKETT}/dl/magnetonly/?path=7"
    r = await c.get("/api/torrents/files", params={"link": link})
    assert r.status_code == 200 and r.json()["pending"] is True
    qbit.metadata()
    files = (await c.get("/api/torrents/files", params={"link": link})).json()
    assert [f["path"] for f in files["files"] if f["importable"]] == [FILES[0][0], FILES[1][0]]
    r = await c.post("/api/torrents", json={"link": link, "title": RELEASE})
    assert r.status_code == 200, r.text
    assert qbit.torrents[qbit.meta.info_hash]["state"] == "downloading"

    r = await c.get("/api/torrents/files", params={"link": f"{JACKETT}/dl/broken/?path=8"})
    assert r.status_code == 502 and "не смог скачать .torrent" in r.json()["detail"]



# --------------------------------------------------------------------------- listening before downloading


def test_range_header():
    from app.torrents.stream import parse_range

    assert parse_range(None, 100) == (0, 99, False)
    assert parse_range("bytes=0-", 100) == (0, 99, True)
    assert parse_range("bytes=10-19", 100) == (10, 19, True)
    assert parse_range("bytes=90-500", 100) == (90, 99, True)
    assert parse_range("bytes=-10", 100) == (90, 99, True)
    assert parse_range("bytes=100-", 100) is None
    assert parse_range("items=1-2", 100) == (0, 99, False)


def test_neighbours_share_the_edge_pieces():
    from app.torrents.bencode import TorrentFile, TorrentMeta
    from app.torrents.stream import neighbours

    # pieces of 100 bytes: a [0..249] b [250..349] c [350..399] d [400..999]
    meta = TorrentMeta("a" * 40, "R", [TorrentFile("R/a", 250, 0), TorrentFile("R/b", 100, 250),
                                       TorrentFile("R/c", 50, 350), TorrentFile("R/d", 600, 400)], 100)
    assert neighbours(meta, 0) == [1]  # a ends in piece 2, where b starts
    assert neighbours(meta, 1) == [0, 2]  # b: pieces 2..3, shared with a and c
    assert neighbours(meta, 3) == []  # d starts on a piece boundary


def test_file_offsets_count_padding():
    from app.torrents.bencode import bencode, parse_torrent

    info = {"name": "R", "piece length": 1024, "pieces": b"\x00" * 40, "files": [
        {"length": 1000, "path": ["01.flac"]}, {"length": 24, "path": [".pad", "24"], "attr": "p"},
        {"length": 500, "path": ["02.flac"]},
    ]}
    meta = parse_torrent(bencode({"info": info}))
    assert [(f.path, f.offset) for f in meta.files] == [("R/01.flac", 0), ("R/02.flac", 1024)]
    assert meta.piece_length == 1024


async def test_bytes_come_as_their_pieces_are_downloaded(tmp_path, monkeypatch):
    from app.torrents import stream
    from app.torrents.bencode import TorrentFile, TorrentMeta

    data = bytes(range(256)) * 20  # 5120 bytes: the file starts 100 bytes into piece 0 of 1024-byte pieces
    (tmp_path / "staging" / "torrents" / "R").mkdir(parents=True)
    (tmp_path / "staging" / "torrents" / "R" / "01.flac").write_bytes(data)
    monkeypatch.setattr(stream, "get_settings", lambda: type("S", (), {"torrents_dir": tmp_path / "staging" / "torrents"})())
    meta = TorrentMeta("a" * 40, "R", [TorrentFile("R/00.txt", 100, 0), TorrentFile("R/01.flac", 5120, 100)], 1024)
    states = [2, 0, 0, 0, 0, 0]
    polls = []

    async def piece_states(_h):
        polls.append(len(polls))
        if len(polls) == 3:
            states[1:] = [2] * 5  # the rest arrives while the reader waits
        return states

    monkeypatch.setattr(stream, "_piece_states", piece_states)
    monkeypatch.setattr(stream, "POLL_S", 0)
    got = b"".join([c async for c in stream.read_range(meta, 1, 0, 5119)])
    assert got == data and len(polls) >= 3
    got = b"".join([c async for c in stream.read_range(meta, 1, 2000, 2099)])
    assert got == data[2000:2100]

    states[:] = [2, 0, 0, 0, 0, 0]
    monkeypatch.setattr(stream, "NO_PROGRESS_S", 0)
    with pytest.raises(httpx.ReadTimeout):
        _ = [c async for c in stream.read_range(meta, 1, 0, 5119)]


async def test_listen_to_a_release_file_before_downloading(env):
    c, qbit, music = env["client"], env["qbit"], env["music"]
    files = (await c.get("/api/torrents/files", params={"link": release_link(), "title": RELEASE})).json()["files"]
    playable = [f for f in files if f["ref"]]
    assert [f["title"] for f in playable] == ["Первая", "Вторая"] and playable[0]["artist"] == "Кино"
    assert all(f["ref"] is None for f in files if f["kind"] != "audio")
    assert qbit.torrents == {}  # listing the files adds nothing to qBittorrent

    body = bytes(range(256)) * 11 + b"tail"  # FILES[1]: 3000 bytes... the test writes its own size
    second = playable[1]
    path = music / "staging" / "torrents" / FILES[1][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((body * 2)[:3000])

    r = await c.get("/api/listen", params={"ref": second["ref"]})
    assert r.status_code == 200 and r.json()["kind"] == "full", r.text
    r = await c.get("/api/listen/stream", params={"ref": second["ref"]})
    assert r.status_code == 200, r.text
    assert r.content == (body * 2)[:3000] and r.headers["content-type"].startswith("audio/flac")

    tq = qbit.torrents[qbit.meta.info_hash]  # added as a preview, only the listened file wanted, in order
    assert "homemusic-preview" in tq["tags"] and tq["state"] == "downloading"
    # one 16 KB piece holds the whole release: every other file shares it, so they are wanted at the lowest priority
    assert [tq["prio"][i] for i in range(len(FILES))] == [1, 7, 1, 1, 1]
    assert tq["seq"] and tq["fl"]

    # downloading the release afterwards takes the preview over: the chosen files, not in order any more
    r = await c.post("/api/torrents", json={"link": release_link(), "title": RELEASE})
    assert r.status_code == 200, r.text
    assert qbit.wanted() == [FILES[0][0], FILES[1][0], FILES[3][0]]
    assert tq["tags"] == {"homemusic"} and not tq["seq"]


# --------------------------------------------------------------------------- a catalog track from a release


def test_releases_for_a_track_seeded_by_the_artist_album_first():
    from app.providers.base import TrackQuery
    from app.torrents.jackett import Release, parse_release_title
    from app.torrents.tracks import Want

    q = TrackQuery(artist="Кино", title="Группа крови", album="Группа крови")
    want = Want(q, q)
    assert want.queries == ["кино группа крови", "кино"]

    def rel(title: str, seeders: int) -> Release:
        return Release(title=title, link=f"magnet:?dn={seeders}", seeders=seeders, info=parse_release_title(title))

    album = rel("Кино - Группа крови (1988) [MP3 320]", 3)
    disco = rel("Kino - Discography 1982-1990 (FLAC)", 40)
    other = rel("Кино - Звезда по имени Солнце - 1989, MP3", 90)
    found = [other, disco, album,
             rel("Кино - Группа крови - 1988, MP3", 0),  # nobody seeds it
             rel("Кино - Группа крови - 1988, FLAC (image+.cue)", 50),  # one file per disc
             rel("Кино - Группа крови - 1988, APE (tracks+.cue)", 50),  # a browser doesn't play APE
             rel("Кинокомпозиторы - Группа крови (tribute), MP3", 70)]  # another artist
    assert want.rank([r for r in found if want.usable(r)]) == [album, disco, other]

    assert want.score("Кино - Группа крови (1988)/01 - Группа крови.flac") == (True, True)
    assert want.score("Kino - Discography/1988 - Gruppa krovi/01. Kino - Gruppa krovi.mp3") == (True, True)
    assert want.score("Разное/Группа крови.mp3") == (True, False)
    assert want.score("Концерты/05 - Группа крови (live).mp3") is None
    assert want.score("Ремиксы/02 - Группа крови (remix).mp3") is None
    assert want.score("Сборник/03. Би-2 - Группа крови.mp3") is None
    assert want.score("Кино - Группа крови (1988)/02 - Закрой за мной дверь.flac") is None


def _no_source_has_it(svc, title: str = "Вторая") -> None:
    from app.torrents import stream
    from tests.support import FakeProvider

    stream._asked.clear()  # files streamed by the tests before (module state: one app process in production)
    svc.providers = {"hitmoz": FakeProvider("hitmoz", []), "torrent": svc.providers["torrent"]}
    svc.deezer.track = svc.deezer.track.model_copy(update={"title": title})


async def test_a_track_no_source_has_plays_from_a_seeded_release(env, monkeypatch):
    from app.services import svc

    c, qbit, music = env["client"], env["qbit"], env["music"]
    _no_source_has_it(svc)
    fetched: list[str] = []
    fetch = svc.jackett.fetch_torrent

    async def fetch_torrent(link: str) -> bytes:
        fetched.append(parse_qs(link.split("?", 1)[1])["path"][0])
        return await fetch(link)

    monkeypatch.setattr(svc.jackett, "fetch_torrent", fetch_torrent)
    path = music / "staging" / "torrents" / FILES[1][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x01" * 3000)

    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "full" and out["provider"] == "torrent" and out["duration_s"] == 2
    assert "2" not in fetched  # the disc image with the most seeders is not even looked into
    r = await c.get(out["stream_url"])
    assert r.status_code == 200 and r.content == b"\x01" * 3000
    tq = qbit.torrents[qbit.meta.info_hash]  # a preview with only the track's file wanted, first
    assert "homemusic-preview" in tq["tags"] and tq["prio"][1] == 7 and tq["seq"]

    from app import listen

    await listen.reset()  # the next listen (another day of the app): the remembered pick, no search
    searched = 0
    search = svc.jackett.search

    async def count(*a, **kw):
        nonlocal searched
        searched += 1
        return await search(*a, **kw)

    monkeypatch.setattr(svc.jackett, "search", count)
    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["provider"] == "torrent" and searched == 0


async def test_a_track_no_release_has_falls_back_to_the_preview(env):
    from app.services import svc

    _no_source_has_it(svc, "Кукушка")
    r = await env["client"].get("/api/listen", params={"ref": "dz:100"})
    assert r.status_code == 200 and r.json()["kind"] == "preview", r.text


async def test_a_prefetch_leaves_the_releases_to_the_tap(env, monkeypatch):
    from app import listen
    from app.services import svc

    c, qbit = env["client"], env["qbit"]
    _no_source_has_it(svc)
    searched: list[str] = []
    search = svc.jackett.search

    async def count(q, categories, **kw):
        searched.append(q)
        return await search(q, categories, **kw)

    monkeypatch.setattr(svc.jackett, "search", count)

    async def opens(choice) -> bool:  # Deezer's preview link (a prefetch opens what it picks)
        return True

    monkeypatch.setattr(listen, "_opens_bounded", opens)
    pre = await listen.choose("dz:100", background=True)
    assert pre.kind == "preview" and pre.untried_releases
    assert searched == [] and qbit.torrents == {} and env["state"]["dl"] == 0  # a prefetch adds no torrents

    r = await c.get("/api/listen", params={"ref": "dz:100"})
    assert r.json()["kind"] == "full" and r.json()["provider"] == "torrent", r.text
    assert searched
    assert (await listen.choose("dz:100", background=True)).kind == "full"
