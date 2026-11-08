import pytest

from app.matching.normalize import Norm, normalize_artist, normalize_title
from app.matching.score import artist_sim, score, title_sim


@pytest.mark.parametrize(
    ("qa", "qt", "ca", "ct", "min_total"),
    [
        ("Кино", "Группа крови", "KINO", "Gruppa krovi", 0.92),
        ("Скриптонит feat. Truwer", "Положение", "Скриптонит, Truwer", "Положение", 0.92),
        ("MACAN", "ASPHALT 8", "Macan", "Asphalt 8", 0.92),
        ("Тима Белорусских", "Мокрые крос…", "Тима Белорусских", "Мокрые кроссы", 0.92),
        ("Tima Belorusskih", "Мокрые крос…", "Тима Белорусских", "Мокрые кроссы", 0.92),
        ("Кино", "Пачка сигарет", "Кино", "Пачка сигарет (Remastered 2019)", 0.92),
        ("AC/DC", "Back in Black", "AC/DC", "Back In Black", 0.92),
        ("Скриптонит", "Положение", "Скриптонит", "Положение (feat. Truwer)", 0.92),
    ],
)
def test_score_good_matches(qa, qt, ca, ct, min_total):
    bd = score(qa, qt, ca, ct)
    assert bd.total >= min_total
    assert bd.version_penalty == 0


@pytest.mark.parametrize(
    ("qa", "qt", "ca", "ct", "max_total"),
    [
        ("Кино", "Группа крови", "Кино", "Звезда по имени Солнце", 0.65),
        ("Кино", "Группа крови", "Ария", "Группа крови", 0.75),
        ("Кино", "Пачка сигарет", "Кино", "Пачка сигарет (Remix)", 0.75),
        ("Кино", "Пачка сигарет", "Кино", "Пачка сигарет (Slowed + Reverb)", 0.75),
    ],
)
def test_score_bad_matches(qa, qt, ca, ct, max_total):
    assert score(qa, qt, ca, ct).total <= max_total


def test_formula_and_bonus():
    bd = score("Кино", "Группа крови", "Кино", "Группа крови")
    assert bd.title == 1.0 and bd.artist == 1.0
    assert bd.bonus == 0.5  # nothing known
    assert bd.total == pytest.approx(0.5 + 0.35 + 0.15 * 0.5)
    assert score("Кино", "Группа крови", "Кино", "Группа крови", query_duration=286, cand_duration=288).bonus == 1.0
    assert score("Кино", "Группа крови", "Кино", "Группа крови", query_duration=286, cand_duration=294).bonus == 0.5
    assert score("Кино", "Группа крови", "Кино", "Группа крови", query_duration=286, cand_duration=320).bonus == 0.0
    both = score(
        "Кино", "Группа крови", "Кино", "Группа крови",
        query_duration=286, cand_duration=294, query_album="Группа крови", cand_album="Группа крови",
    )  # fmt: skip
    assert both.bonus == 1.0
    assert score("Кино", "X", "Кино", "X", query_album="Группа крови", cand_album="Группа крови").bonus == 1.0
    assert score("Кино", "X", "Кино", "X", query_album="Группа крови", cand_album="Звезда").bonus == 0.5


def test_version_penalty():
    plain = score("Кино", "Пачка сигарет", "Кино", "Пачка сигарет")
    remix = score("Кино", "Пачка сигарет", "Кино", "Пачка сигарет (Remix)")
    assert remix.version_penalty == pytest.approx(0.2)
    assert plain.total - remix.total == pytest.approx(0.2)
    # the version was asked for: the remix is right, the plain one is penalized
    assert score("Кино", "Пачка сигарет", "Кино", "Пачка сигарет (Remix)", query_version="remix").version_penalty == 0
    assert score("Кино", "Пачка сигарет", "Кино", "Пачка сигарет", query_version="Remix").version_penalty > 0
    assert score("Кино", "Пачка сигарет (Remix)", "Кино", "Пачка сигарет [Remix]").version_penalty == 0


def test_title_sim_truncated_prefix():
    a = normalize_title("Мокрые крос…")
    assert a.truncated
    assert title_sim(a, normalize_title("Мокрые кроссы")) == 1.0
    assert title_sim(a, normalize_title("Незабудка")) < 0.5
    # the same text without truncation is a plain (lower) ratio
    assert title_sim(normalize_title("Мокрые крос"), normalize_title("Мокрые кроссы")) < 1.0
    # truncated flag passed explicitly
    assert score("Тима Белорусских", "Мокрые крос", "Тима Белорусских", "Мокрые кроссы", truncated=True).title == 1.0


def test_title_sim_short_truncated_uses_plain_ratio():
    a = Norm("ab", "ab", truncated=True)
    assert title_sim(a, normalize_title("abcdef")) < 1.0


@pytest.mark.parametrize(
    ("a", "b", "ok"),
    [
        ("Кино", "Kino", True),
        ("Скриптонит", "Skriptonit", True),
        ("Тима Белорусских", "Tima Belorusskih", True),
        ("Скриптонит feat. Truwer", "Скриптонит", True),
        ("Скриптонит", "Скриптонит, 104, Truwer", True),
        ("Скриптонит, Truwer", "Truwer, Скриптонит", True),  # same credits in another order
        ("AC/DC", "ACDC", True),
        ("Кино", "Ария", False),
        ("Скриптонит", "Truwer", False),
        ("Скриптонит", "Truwer feat. Скриптонит", False),  # main artist must match
    ],
)
def test_artist_sim(a, b, ok):
    assert (artist_sim(normalize_artist(a), normalize_artist(b)) >= 0.9) is ok
