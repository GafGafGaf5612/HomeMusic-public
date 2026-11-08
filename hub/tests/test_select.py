from datetime import date

import pytest

from app.catalog.models import CatalogTrack
from app.config import MatchingCfg
from app.matching.select import (
    classify,
    classify_scores,
    match_catalog,
    needs_uncensored,
    rank_candidates,
    trusted_original,
    uploaded_in_time,
)
from app.providers.base import Candidate, TrackQuery

CFG = MatchingCfg()


def cand(artist: str, title: str, pid: str = "1", **kw) -> Candidate:
    return Candidate(provider="hitmoz", provider_id=pid, artist=artist, title=title, page_url=f"/song/{pid}", **kw)


def ids(cands: list[Candidate]) -> list[str]:
    return [c.provider_id for c in cands]


@pytest.mark.parametrize(
    ("title", "kept"),
    [
        ("Группа крови", True),
        ("Группа крови (Remastered 2019)", True),
        ("Группа крови (караоке)", False),
        ("Группа крови (минусовка)", False),
        ("Группа крови (минус)", False),
        ("Группа крови (кавер)", False),
        ("Группа крови (Cover)", False),
        ("Группа крови (Instrumental)", False),
        ("Группа крови (Remix)", False),
        ("Группа крови (Slowed + Reverb)", False),
        ("Группа крови (Sped Up)", False),
        ("Группа крови (speed-up)", False),
        ("Группа крови (Nightcore)", False),
        ("Группа крови (8D Audio)", False),
    ],
)
def test_reject_words(title, kept):
    q = TrackQuery(artist="Кино", title="Группа крови")
    assert (len(rank_candidates(q, [cand("Кино", title)], CFG)) == 1) is kept


def test_reject_word_in_artist():
    q = TrackQuery(artist="Кино", title="Группа крови")
    assert rank_candidates(q, [cand("Кино (кавер)", "Группа крови")], CFG) == []


@pytest.mark.parametrize("version", ["slowed", "sped up", "remix"])
def test_requested_version_is_not_rejected(version):
    titles = {"slowed": "Song (Slowed + Reverb)", "sped up": "Song (Sped Up)", "remix": "Song (Ремикс)"}
    q = TrackQuery(artist="Artist", title="Song", version=version)
    ranked = rank_candidates(q, [cand("Artist", "Song", "plain"), cand("Artist", titles[version], "v")], CFG)
    assert ids(ranked)[0] == "v"
    assert "plain" in ids(ranked)  # kept, but penalized for the version mismatch


def test_version_in_query_title_allows_it():
    q = TrackQuery(artist="Artist", title="Song (Remix)")
    assert ids(rank_candidates(q, [cand("Artist", "Song (Remix)", "r")], CFG)) == ["r"]


def test_artist_filter():
    q = TrackQuery(artist="Кино", title="Группа крови")
    ranked = rank_candidates(q, [cand("Ария", "Группа крови", "a"), cand("KINO", "Gruppa krovi", "k")], CFG)
    assert ids(ranked) == ["k"]


def test_duration_filter():
    q = TrackQuery(artist="Кино", title="Группа крови", duration_s=286)
    cands = [
        cand("Кино", "Группа крови", "far", duration_s=300),
        cand("Кино", "Группа крови", "near", duration_s=288),
        cand("Кино", "Группа крови", "unknown"),
    ]
    assert ids(rank_candidates(q, cands, CFG)) == ["near", "unknown"]


def test_explicit_preferred_over_clean():
    q = TrackQuery(artist="Скриптонит", title="Положение")
    cands = [
        cand("Скриптонит", "Положение", "clean", explicit=False),
        cand("Скриптонит", "Положение", "unknown"),
        cand("Скриптонит", "Положение (Explicit)", "explicit"),
    ]
    assert ids(rank_candidates(q, cands, CFG)) == ["explicit", "unknown", "clean"]
    q_clean = TrackQuery(artist="Скриптонит", title="Положение", explicit=False)
    assert ids(rank_candidates(q_clean, cands, CFG))[0] == "clean"


def test_bitrate_preferred():
    q = TrackQuery(artist="Кино", title="Группа крови")
    cands = [
        cand("Кино", "Группа крови", "128", bitrate_kbps=128),
        cand("Кино", "Группа крови", "320", bitrate_kbps=320),
    ]
    assert ids(rank_candidates(q, cands, CFG)) == ["320", "128"]


def test_earlier_upload_preferred():
    q = TrackQuery(artist="Кино", title="Группа крови")
    cands = [
        cand("Кино", "Группа крови", "new", upload_date=date(2026, 5, 1)),
        cand("Кино", "Группа крови", "unknown"),
        cand("Кино", "Группа крови", "old", upload_date=date(2019, 1, 1)),
        cand("Кино", "Группа крови", "older", upload_date=date(2015, 6, 1)),
    ]
    assert ids(rank_candidates(q, cands, CFG)) == ["older", "old", "unknown", "new"]


def test_score_bucket_beats_tiebreakers():
    q = TrackQuery(artist="Кино", title="Группа крови", duration_s=286)
    cands = [
        cand("Кино", "Группа крови", "weak", explicit=True, bitrate_kbps=320, upload_date=date(2010, 1, 1)),
        cand("Кино", "Группа крови", "exact", duration_s=286),
    ]
    assert ids(rank_candidates(q, cands, CFG)) == ["exact", "weak"]


def test_rank_returns_scored_copies():
    q = TrackQuery(artist="Кино", title="Группа крови")
    original = cand("Кино", "Группа крови")
    ranked = rank_candidates(q, [original], CFG)
    assert ranked[0].score == pytest.approx(0.925)
    assert original.score == 0
    assert ranked[0] is not original


def test_original_order_is_last_tiebreaker():
    q = TrackQuery(artist="Кино", title="Группа крови")
    assert ids(rank_candidates(q, [cand("Кино", "Группа крови", str(i)) for i in range(3)], CFG)) == ["0", "1", "2"]


@pytest.mark.parametrize(
    ("scores", "verdict"),
    [
        ([0.95], "auto"),
        ([0.95, 0.80], "auto"),
        ([0.95, 0.90], "auto"),
        ([0.95, 0.93], "confirm"),
        ([0.80, 0.50], "confirm"),
        ([0.65], "confirm"),
        ([0.64], "not_found"),
        ([], "not_found"),
    ],
)
def test_classify_scores(scores, verdict):
    assert classify_scores(scores, CFG) == verdict


def test_classify_ignores_duplicate_uploads():
    q = TrackQuery(artist="Кино", title="Группа крови")
    dupes = [cand("Кино", "Группа крови", "1"), cand("KINO", "Группа крови (Remastered 2019)", "2")]
    assert classify(rank_candidates(q, dupes, CFG), CFG) == "auto"
    rival = [cand("Кино", "Группа крови", "1"), cand("Кино", "Группа крови (Live)", "2")]
    q_live = TrackQuery(artist="Кино", title="Группа крови", duration_s=None)
    ranked = rank_candidates(q_live, rival, CFG)
    assert classify(ranked, CFG) == "auto"  # live is 0.2 lower: enough margin
    assert classify([], CFG) == "not_found"


def ct(id_: str, title: str, artist: str = "Тима Белорусских", **kw) -> CatalogTrack:
    return CatalogTrack(ref=f"dz:{id_}", source="dz", id=id_, title=title, artist=artist, **kw)


def test_match_catalog_tracklist_truncated():
    tracks = [ct("1", "Незабудка"), ct("2", "Мокрые кроссы"), ct("3", "Витаминка")]
    res = match_catalog("Тима Белорусских", "Мокрые крос…", tracks)
    assert res[0][0].id == "2"
    assert res[0][1].total >= 0.9
    assert "2" not in [t.id for t, _ in res[1:]]
    assert res[0][1].total > res[1][1].total


def test_match_catalog_uses_duration_and_album():
    tracks = [
        ct("a", "Группа крови", "Кино", duration_s=200, album_title="Сборник"),
        ct("b", "Группа крови (Remastered 2019)", "Кино", duration_s=286, album_title="Группа крови"),
    ]
    res = match_catalog("Kino", "Gruppa krovi", tracks, album="Группа крови", duration=287)
    assert [t.id for t, _ in res] == ["b", "a"]
    assert res[0][1].bonus == 1.0


@pytest.mark.parametrize(
    ("q", "needed"),
    [
        (TrackQuery(artist="Слава КПСС", title="Кайфоград", explicit=True), True),
        (TrackQuery(artist="Слава КПСС", title="Кайфоград"), False),  # not known to be explicit
        (TrackQuery(artist="Eminem", title="Stan", explicit=True), False),
        (TrackQuery(artist="Pharaoh", title="Black Siemens", explicit=True, lang="ru"), True),
    ],
    ids=["explicit-ru", "not-explicit", "not-russian", "latin-ru"],
)
def test_needs_uncensored_only_for_explicit_russian_tracks(q, needed):
    assert needs_uncensored(q) is needed


def kaifograd(**kw) -> Candidate:
    return cand("Слава КПСС", "Кайфоград", **kw)


@pytest.mark.parametrize(
    ("c", "trusted"),
    [
        (kaifograd(upload_date=date(2024, 1, 5)), True),
        (kaifograd(upload_date=date(2024, 1, 5), explicit=True), True),
        (kaifograd(upload_date=date(2026, 4, 1), explicit=True), False),  # after the law: may be edited
        (kaifograd(), False),  # date unknown
        (kaifograd(upload_date=date(2024, 1, 5), explicit=False), False),  # a clean version
        (kaifograd(upload_date=date(2021, 4, 1), extra={"source": "ytmusic"}), False),
        (kaifograd(upload_date=date(2021, 4, 1), extra={"source": "ytsearch"}), True),
        (kaifograd(upload_date=date(2026, 6, 1), extra={"uncensored": True}), True),
        (kaifograd(upload_date=date(2024, 6, 1), extra={"uncensored": False}), False),
    ],
    ids=["old", "old-explicit", "new", "no-date", "old-clean", "ytmusic", "old-youtube", "titled-uncensored",
         "titled-clean"],
)
def test_trusted_original(c, trusted):
    assert trusted_original(c, CFG) is trusted


SC_CUTOFF = {"soundcloud": date(2026, 5, 1)}


@pytest.mark.parametrize(
    ("c", "kept"),
    [
        (kaifograd(upload_date=date(2026, 4, 30)).model_copy(update={"provider": "soundcloud"}), True),
        (kaifograd(upload_date=date(2026, 5, 1)).model_copy(update={"provider": "soundcloud"}), False),
        (kaifograd().model_copy(update={"provider": "soundcloud"}), True),
        (kaifograd(upload_date=date(2026, 6, 1)), True),
    ],
    ids=["before", "on-cutoff", "no-date", "other-source"],
)
def test_uploaded_in_time_drops_files_uploaded_after_the_source_cutoff(c, kept):
    assert uploaded_in_time(c, SC_CUTOFF) is kept
