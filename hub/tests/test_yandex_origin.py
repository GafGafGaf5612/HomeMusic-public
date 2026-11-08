"""Russian tracks listed in Latin by the catalog get Cyrillic originals and the Russian source order."""

import pytest

from app.catalog import yandex
from app.matching.normalize import fingerprint, is_russian_query, to_latin
from app.providers.base import TrackQuery


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    async def passthrough(key, ttl_s, producer):
        return await producer()

    monkeypatch.setattr(yandex, "cached", passthrough)


def fake_search(rows):
    def search(text):
        search.queries.append(text)
        return rows

    search.queries = []
    return search


def test_gost_translit_folds_to_cyrillic_form() -> None:
    assert to_latin("Что-то ещё") == to_latin("Chto-to eshhyo") == to_latin("Chto-to eshchyo")
    assert fingerprint("ЛСП", "Что-то ещё") == fingerprint("LSP", "Chto-to eshhyo")
    assert to_latin("Ёлка") == to_latin("Yolka")


def test_latin_to_cyrillic() -> None:
    assert yandex.latin_to_cyrillic("Gde nas net") == "где нас нет"
    assert yandex.latin_to_cyrillic("Kazhdyy raz") == "каждый раз"
    assert yandex.latin_to_cyrillic("Chto-to eshhyo") == "что-то ещё"
    assert yandex.latin_to_cyrillic("Moy rok-n-roll") == "мой рок-н-ролл"


async def test_transliterated_title_is_searched_in_cyrillic(monkeypatch) -> None:
    def search(text):
        search.queries.append(text)
        return [{"artist": "Oxxxymiron", "title": "Где нас нет", "duration_s": 266}] if "нас" in text else []

    search.queries = []
    monkeypatch.setattr(yandex, "_search_tracks", search)
    tag_q, pq = await yandex.russian_original(TrackQuery(artist="Oxxxymiron", title="Gde nas net", duration_s=265))
    assert search.queries == ["Oxxxymiron Gde nas net", "Oxxxymiron где нас нет"]
    assert (pq.artist, pq.title, pq.lang, tag_q.title) == ("Oxxxymiron", "Где нас нет", "ru", "Где нас нет")


def test_split_suffix() -> None:
    assert yandex.split_suffix("Bezumie (Remix)") == ("Bezumie", " (Remix)")
    assert yandex.split_suffix("Chto-to eshhyo") == ("Chto-to eshhyo", "")
    assert yandex.split_suffix("Gorod - Live") == ("Gorod", " - Live")


async def test_remix_with_guests_searches_main_artist_and_catalog_suffix(monkeypatch) -> None:
    monkeypatch.setattr(yandex, "_search_tracks", fake_search([
        {"artist": "ЛСП, Oxxxymiron", "artists": ["ЛСП", "Oxxxymiron"], "title": "Безумие (Remix; feat. Oxxxymiron)",
         "base": "Безумие", "duration_s": 225},
    ]))
    q = TrackQuery(artist="LSP", title="Bezumie (Remix)", duration_s=225)
    tag_q, pq = await yandex.russian_original(q)
    assert (pq.artist, pq.title, pq.lang) == ("ЛСП", "Безумие (Remix)", "ru")
    assert (tag_q.artist, tag_q.title) == ("LSP", "Безумие (Remix)")


def test_cis_release() -> None:
    assert yandex.cis_release("4680017661805", None)  # Russia 460-469
    assert yandex.cis_release("4810000000000", None)  # Belarus
    assert yandex.cis_release(None, "RUA1D8800001")
    assert not yandex.cis_release("0602537439054", "USUM71304287")
    assert not yandex.cis_release("468001766180", "QMPU51501893")  # 12 digits = UPC-A, not an EAN prefix


async def test_cyrillic_original_switches_to_russian_order(monkeypatch) -> None:
    search = fake_search([{"artist": "ЛСП", "title": "Что-то ещё", "duration_s": 240}])
    monkeypatch.setattr(yandex, "_search_tracks", search)
    q = TrackQuery(artist="LSP", title="Chto-to eshhyo", album="Magic City", duration_s=240, isrc="QMPU51501893")
    tag_q, pq = await yandex.russian_original(q, upc=None)
    assert search.queries == ["LSP Chto-to eshhyo"]
    assert (pq.artist, pq.title, pq.lang) == ("ЛСП", "Что-то ещё", "ru") and is_russian_query(pq)
    # tags: the transliterated title is restored, the artist stays as in the catalog (consistent albums)
    assert (tag_q.artist, tag_q.title, tag_q.album) == ("LSP", "Что-то ещё", "Magic City")


async def test_other_song_or_duration_is_not_taken(monkeypatch) -> None:
    monkeypatch.setattr(yandex, "_search_tracks", fake_search([
        {"artist": "ЛСП", "title": "Монетка", "duration_s": 240},
        {"artist": "ЛСП", "title": "Что-то ещё", "duration_s": 300},
    ]))
    q = TrackQuery(artist="LSP", title="Chto-to eshhyo", duration_s=240)
    assert await yandex.russian_original(q) == (q, q)


async def test_international_track_keeps_default_order(monkeypatch) -> None:
    monkeypatch.setattr(yandex, "_search_tracks", fake_search([
        {"artist": "Daft Punk, Pharrell Williams, Nile Rodgers", "title": "Get Lucky", "duration_s": 248},
    ]))
    q = TrackQuery(artist="Daft Punk", title="Get Lucky", duration_s=248, isrc="USQX91300809")
    tag_q, pq = await yandex.russian_original(q, upc="886443927087")
    assert tag_q == q and pq == q and not is_russian_query(pq)


async def test_cis_barcode_alone_gives_russian_order(monkeypatch) -> None:
    def broken(text):
        raise ConnectionError("yandex is down")

    monkeypatch.setattr(yandex, "_search_tracks", broken)
    q = TrackQuery(artist="LSP", title="Chto-to eshhyo", duration_s=240)
    tag_q, pq = await yandex.russian_original(q, upc="4680017661805")
    assert tag_q == q and (pq.artist, pq.title, pq.lang) == ("LSP", "Chto-to eshhyo", "ru")


async def test_cyrillic_query_is_not_looked_up(monkeypatch) -> None:
    search = fake_search([])
    monkeypatch.setattr(yandex, "_search_tracks", search)
    q = TrackQuery(artist="Кино", title="Группа крови")
    assert await yandex.russian_original(q) == (q, q) and search.queries == []
