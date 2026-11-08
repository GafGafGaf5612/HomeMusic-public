import pytest

from app.matching.normalize import (
    drop_markers,
    explicit_marker,
    fingerprint,
    has_cyrillic,
    is_russian_query,
    normalize_artist,
    normalize_title,
    split_version,
    uncensored_marker,
    version_kind,
)
from app.providers.base import TrackQuery


@pytest.mark.parametrize(
    ("raw", "text", "version", "featured", "truncated"),
    [
        ("Группа крови", "группа крови", None, (), False),
        ("Группа крови (Remastered 2019)", "группа крови", "remastered 2019", (), False),
        ("Пачка сигарет - Remastered 2019", "пачка сигарет", "remastered 2019", (), False),
        ("Пачка сигарет — Remastered 2019", "пачка сигарет", "remastered 2019", (), False),
        ("Ёлка", "елка", None, (), False),
        ("ASPHALT 8", "asphalt 8", None, (), False),
        ("Мокрые крос…", "мокрые крос", None, (), True),
        ("Мокрые крос...", "мокрые крос", None, (), True),
        ("Song (Soundtrack Version (Explicit))", "song", "soundtrack version", (), False),
        ("Song (Explicit)", "song", None, (), False),
        ("Song [Clean]", "song", None, (), False),
        ("Song (Radio Edit)", "song", None, (), False),
        ("Song [Live]", "song", "live", (), False),
        ("Song (Slowed + Reverb)", "song", "slowed reverb", (), False),
        ("Song (Sped Up)", "song", "sped up", (), False),
        ("Song (feat. Truwer)", "song", None, ("truwer",), False),
        ("Song (feat. A & B) [Remix]", "song", "remix", ("a", "b"), False),
        ("Song feat. Truwer", "song", None, ("truwer",), False),
        ("Song (при уч. Баста)", "song", None, ("баста",), False),
        ("Don’t Stop Me Now", "dont stop me now", None, (), False),
        ("Звезда по имени Солнце (из к/ф «Игла»)", "звезда по имени солнце из кф игла", None, (), False),
        ("Song (Acoustic Version)", "song", "acoustic version", (), False),
        ("Song (Караоке)", "song", "караоке", (), False),
        ("Live Forever", "live forever", None, (), False),
        ("Song (feat. X…", "song", None, ("x",), True),
    ],
)
def test_normalize_title(raw, text, version, featured, truncated):
    n = normalize_title(raw)
    assert n.text == text
    assert n.version == version
    assert n.featured == featured
    assert n.truncated is truncated


@pytest.mark.parametrize(
    ("raw", "text", "featured"),
    [
        ("Скриптонит feat. Truwer", "скриптонит", ("truwer",)),
        ("Скриптонит ft. Truwer", "скриптонит", ("truwer",)),
        ("Скриптонит, Truwer", "скриптонит", ("truwer",)),
        ("Скриптонит, 104, Truwer", "скриптонит", ("104", "truwer")),
        ("Баста при участии Гуф", "баста", ("гуф",)),
        ("AC/DC", "acdc", ()),
        ("Macan x Jakone", "macan", ("jakone",)),
        ("Macan × Jakone", "macan", ("jakone",)),
        ("Malcolm X", "malcolm x", ()),
        ("Simon & Garfunkel", "simon and garfunkel", ()),
        ("Simon and Garfunkel", "simon and garfunkel", ()),
        ("Macklemore & Ryan Lewis", "macklemore", ("ryan lewis",)),
        ("Florence + the Machine", "florence the machine", ()),
        ("Tyler, The Creator", "tyler the creator", ()),
        ("Король и Шут", "король и шут", ()),
        ("Тима Белорусских и Мари Краймбрери", "тима белорусских", ("мари краймбрери",)),
        ("Little Feat", "little feat", ()),
        ("MACAN", "macan", ()),
        ("Daft Punk", "daft punk", ()),
        ("Artist (feat. Guest)", "artist", ("guest",)),
    ],
)
def test_normalize_artist(raw, text, featured):
    n = normalize_artist(raw)
    assert n.text == text
    assert n.featured == featured


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Кино", "Kino"),
        ("Кино", "KINO"),
        ("Скриптонит", "Skriptonit"),
        ("Macan", "MACAN"),
        ("Тима Белорусских", "Tima Belorusskih"),
        ("Тима Белорусских", "Tima Belorusskikh"),
        ("Макс Корж", "Max Korzh"),
    ],
)
def test_transliteration_common_form(a, b):
    assert normalize_artist(a).latin == normalize_artist(b).latin


def test_title_latin():
    assert normalize_title("Группа крови").latin == normalize_title("Gruppa krovi").latin == "gruppa krovi"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Song (Explicit)", True),
        ("Song [18+]", True),
        ("Song (Soundtrack Version (Explicit))", True),
        ("Song - Explicit", True),
        ("Song (Clean)", False),
        ("Song (Radio Edit)", False),
        ("Song", None),
        ("Clean", None),  # a song called "Clean" is not a clean marker
        ("Explicit", True),
        ("Song 18+", True),
        ("", None),
        ("Бумер (правильная версия)", True),
        ("Кайфоград (до цензуры)", True),
        ("G-SHOKK (БЕЗ МАТА)", False),
        ("Song (Radio Version)", False),
    ],
)
def test_explicit_marker(raw, expected):
    assert explicit_marker(raw) is expected


@pytest.mark.parametrize(
    ("raw", "title", "version"),
    [
        ("Группа крови (Remastered 2019)", "Группа крови", "remastered 2019"),
        ("Song (feat. X) [Live] (Explicit)", "Song (feat. X)", "live"),
        ("Пачка сигарет - Remastered 2019", "Пачка сигарет", "remastered 2019"),
        ("Song (Part 2)", "Song (Part 2)", None),
        ("Song", "Song", None),
    ],
)
def test_split_version(raw, title, version):
    assert split_version(raw) == (title, version)


@pytest.mark.parametrize(
    ("version", "kind"),
    [
        ("remastered 2019", None),
        ("soundtrack version", None),
        ("original mix", None),
        ("remix", "remix"),
        ("Club Mix", "remix"),
        ("live at wembley", "live"),
        ("slowed reverb", "slowed"),
        ("speed up", "sped up"),
        ("минусовка", "instrumental"),
        (None, None),
    ],
)
def test_version_kind(version, kind):
    assert version_kind(version) == kind


def test_fingerprint_is_script_and_version_independent():
    fp = fingerprint("Кино", "Группа крови (Remastered 2019)")
    assert fp == "kino|gruppa krovi"
    assert fingerprint("KINO", "Gruppa krovi") == fp
    assert fingerprint("Кино feat. Someone", "Группа крови [Explicit]") == fp
    assert fingerprint("Кино", "Пачка сигарет") != fp


def test_cyrillic_and_russian_query():
    assert has_cyrillic("Кино")
    assert not has_cyrillic("Kino")
    assert not has_cyrillic(None)
    assert is_russian_query(TrackQuery(artist="Кино", title="Группа крови"))
    assert is_russian_query(TrackQuery(artist="Macan", title="Asphalt 8", lang="ru"))
    assert not is_russian_query(TrackQuery(artist="AC/DC", title="Back in Black"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Кайфоград – Слава КПСС (без цензуры)", True),
        ("Платина - Бумер (правильная версия)", True),
        ("G-Shock (Без Цензуры)", True),
        ("Song (Uncensored)", True),
        ("Song [до цензуры]", True),
        ("G-SHOKK (БЕЗ МАТА)", False),
        ("Song (Clean)", False),
        ("Song (Explicit)", None),  # a label's Explicit survived the edits of 2026
        ("Кайфоград", None),
    ],
)
def test_uncensored_marker(raw, expected):
    assert uncensored_marker(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Кайфоград (без цензуры)", "Кайфоград"),
        ("Слава КПСС - Кайфоград [Без Цензуры]", "Слава КПСС - Кайфоград"),
        ("Бумер - правильная версия", "Бумер"),
        ("Кайфоград (Summer Music Video 2024)", "Кайфоград (Summer Music Video 2024)"),
    ],
)
def test_drop_markers(raw, expected):
    assert drop_markers(raw) == expected
