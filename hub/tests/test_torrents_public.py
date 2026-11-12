"""Release search over public trackers without Jackett (`app.torrents.public`, TORRENT_SEARCH=public)."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from app.torrents import public
from app.torrents.jackett import JackettError, magnet_info_hash

H1 = "a4262e4201e3d8969a3068bce860d6f485b9d28d"
H2 = "2a9d977a608074468063e0f1deb59dd749d0f510"
H3 = "14a223ee69be1e6759a19d5c909d9030fa134010"

RUTOR_HTML = f"""<html><body><div id="index"><table>
<tr class="backgr"><td>Добавлен</td><td>Название</td><td>Размер</td><td>Пиры</td></tr>
<tr class="gai"><td>16&nbsp;Авг&nbsp;11</td><td ><a class="downgif" href="//d.rutor.info/download/132425"><img alt="D" /></a><a href="magnet:?xt=urn:btih:{H1}&dn=rutor.info&tr=udp://opentor.net:6969&tr=http://retracker.local/announce"><img alt="M" /></a>
<a href="/torrent/132425/kino-gruppa-krovi-1989-mp3">Кино - Группа крови (1989) MP3 </a></td> <td align="right">4<img alt="C" /></td>
<td align="right">109.40&nbsp;MB</td><td align="center"><span class="green"><img alt="S" />&nbsp;7</span>&nbsp;<img alt="L" /><span class="red">&nbsp;2</span></td></tr>
<tr class="tum"><td>01&nbsp;Мар&nbsp;24</td><td ><a href="/torrent/1/broken">Без магнита</a></td><td align="right">1.00&nbsp;GB</td><td></td></tr>
</table></div></body></html>"""

KNABEN = {"hits": [{
    "bytes": 1535450808, "cachedOrigin": "The Pirate Bay (proxy)", "categoryId": [1000000, 1002000],
    "date": "2026-10-12T17:13:00+00:00", "details": "https://knaben.xyz/thepiratebay/description.php?id=1", "grabs": 2507,
    "hash": H2.upper(), "magnetUrl": f"magnet:?xt=urn:btih:{H2.upper()}&dn=Pink+Floyd", "peers": 13, "seeders": 146,
    "title": "Pink Floyd - Wish You Were Here 50 3xCD (2025) FLAC", "tracker": "The Pirate Bay",
}, {"title": "no hash", "magnetUrl": None, "hash": None}]}

APIBAY = [{"id": "81470601", "name": "Pink Floyd - Wish You Were Here 50 (2025 Rock) [Flac 24-192]", "info_hash": H3.upper(),
           "leechers": "14", "seeders": "120", "size": "6811140089", "num_files": "33", "added": "1765589795",
           "category": "104"}]
APIBAY_EMPTY = [{"id": "0", "name": "No results returned", "info_hash": "0" * 40, "leechers": "0", "seeders": "0",
                 "num_files": "0", "size": "0", "added": "0", "category": "0"}]


def test_parse_rutor():
    [r] = public.parse_rutor(RUTOR_HTML)
    assert r.title == "Кино - Группа крови (1989) MP3"
    assert r.magnet and r.info_hash == H1 and magnet_info_hash(r.link) == H1
    assert "tracker.opentrackr.org" in r.link  # public announce URLs added
    assert r.link.count("opentor.net") == 1  # one already there is not repeated
    assert r.tracker == "RuTor" and r.details_url == "https://rutor.info/torrent/132425/kino-gruppa-krovi-1989-mp3"
    assert r.size == int(109.40 * 1024**2) and (r.seeders, r.leechers) == (7, 2)
    assert r.published == datetime(2011, 8, 16)
    assert r.info.codec == "MP3" and r.info.year == 1989


def test_parse_knaben_and_apibay():
    [k] = public.parse_knaben(KNABEN)
    assert (k.info_hash, k.tracker, k.seeders, k.leechers, k.grabs) == (H2, "The Pirate Bay", 146, 13, 2507)
    assert k.published == datetime(2026, 10, 12, 17, 13) and k.info.lossless
    [a] = public.parse_apibay(APIBAY)
    assert (a.info_hash, a.files, a.seeders, a.size) == (H3, 33, 120, 6811140089)
    assert a.details_url.endswith("id=81470601") and magnet_info_hash(a.link) == H3
    assert a.info.hires
    assert public.parse_apibay(APIBAY_EMPTY) == []


@respx.mock
async def test_search_asks_every_site():
    rutor = respx.get(url__regex=r"https://rutor\.info/search/0/2/000/2/.+").mock(
        return_value=httpx.Response(200, text=RUTOR_HTML))
    knaben = respx.post(public.KNABEN_URL).mock(return_value=httpx.Response(200, json=KNABEN))
    apibay = respx.get(public.APIBAY_URL).mock(return_value=httpx.Response(200, json=APIBAY))
    c = public.PublicSearchClient()
    try:
        found = await c.search("кино группа крови", [3000])
    finally:
        await c.aclose()
    assert {r.info_hash for r in found} == {H1, H2, H3}
    assert "%D0%BA%D0%B8%D0%BD%D0%BE" in str(rutor.calls.last.request.url)
    body = knaben.calls.last.request.read().decode()
    assert '"categories":[1000000]' in body.replace(" ", "") and '"search_type":"100%"' in body.replace(" ", "")
    assert apibay.calls.last.request.url.params["cat"] == "100"
    assert not c.own_link(found[0].link)
    with pytest.raises(JackettError):
        await c.fetch_torrent(found[0].link)


@respx.mock
async def test_search_survives_a_dead_site_but_not_all():
    respx.get(url__regex=r"https://rutor\.info/.*").mock(side_effect=httpx.ConnectError("blocked"))
    respx.post(public.KNABEN_URL).mock(return_value=httpx.Response(500))
    respx.get(public.APIBAY_URL).mock(return_value=httpx.Response(200, json=APIBAY))
    c = public.PublicSearchClient()
    try:
        assert [r.info_hash for r in await c.search("pink floyd", [3000])] == [H3]
        respx.get(public.APIBAY_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(JackettError, match="не ответили"):
            await c.search("pink floyd", [3000])
    finally:
        await c.aclose()


@respx.mock
async def test_a_slow_site_is_left_out_after_the_timeout():
    import asyncio

    async def hang(request):
        await asyncio.sleep(30)  # rutor behind a blocking ISP: the connection never completes

    respx.get(url__regex=r"https://rutor\.info/.*").mock(side_effect=hang)
    respx.post(public.KNABEN_URL).mock(return_value=httpx.Response(200, json=KNABEN))
    respx.get(public.APIBAY_URL).mock(return_value=httpx.Response(200, json=APIBAY))
    c = public.PublicSearchClient()
    try:
        found = await asyncio.wait_for(c.search("pink floyd", [3000], timeout_s=0.2), 5)
    finally:
        await c.aclose()
    assert {r.info_hash for r in found} == {H2, H3}


def test_public_search_turns_torrents_on(monkeypatch):
    from app import config

    monkeypatch.setenv("TORRENT_SEARCH", "public")
    monkeypatch.setenv("JACKETT_API_KEY", "")
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().torrents_enabled
    finally:
        config.get_settings.cache_clear()
