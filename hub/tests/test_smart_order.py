"""The pure rules of the smart shuffle (app/mixes/smart_order.py): the ratio of recommendations, «Fewer repeats», the
spread of an artist's songs, the picking of a recommendation, the seeds. No database, no network."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from app.mixes import smart_order as so
from app.mixes.smart_order import OwnSong

NOW = datetime(2026, 11, 7, 12, 0)


def songs(n: int, artists: int | None = None) -> list[OwnSong]:
    return [OwnSong(f"s{i}", f"a{i % artists}" if artists else f"a{i}") for i in range(n)]


# --- where the recommendations go -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("n", "per", "recs", "after"), [
    (1, 2, 1, [0]), (2, 2, 1, [1]), (3, 2, 1, [1]), (4, 2, 2, [1, 3]), (15, 2, 7, [1, 3, 5, 7, 9, 11, 13]),
    (16, 3, 5, [2, 5, 8, 11, 14]), (30, 3, 10, [2, 5, 8, 11, 14, 17, 20, 23, 26, 29]),
], ids=lambda x: str(x) if isinstance(x, int) else "")
def test_one_recommendation_per_three_own_songs_over_fifteen_else_per_two(n, per, recs, after) -> None:
    assert so.own_per_rec(n) == per
    assert so.rec_count(n) == recs
    assert [so.slot_after(n, k) for k in range(recs)] == after


def test_slots_ahead_skip_the_places_behind_the_player() -> None:
    assert so.slots_ahead(30, 0, 3) == [0, 1, 2]
    assert so.slots_ahead(30, 2, 3) == [0, 1, 2]  # the one right after the song counts
    assert so.slots_ahead(30, 3, 3) == [1, 2, 3]
    assert so.slots_ahead(30, 29, 3) == [9]
    assert so.slots_ahead(30, 29, 0) == []
    assert so.slots_ahead(0, 0, 3) == []


# --- «Fewer repeats» ------------------------------------------------------------------------------------------------


def test_staleness_is_larger_for_recent_songs_and_earlier_places() -> None:
    age = {"x": 0.1}

    assert so.staleness(["x", "a", "b"], age) > so.staleness(["a", "b", "x"], age) > 0
    assert so.staleness(["x"], {"x": 30.0}) < so.staleness(["x"], {"x": 1.0})
    assert so.staleness(["a", "b"], age) == 0  # never heard: nothing to repeat


def test_the_shuffle_is_a_permutation_with_the_chosen_song_first() -> None:
    src = songs(25, artists=5)

    got = so.fewer_repeats(src, {}, NOW, random.Random(3), first="s7")

    assert got[0].id == "s7" and sorted(s.id for s in got) == sorted(s.id for s in src)


def test_duplicates_in_the_list_play_once_and_an_unknown_first_is_ignored() -> None:
    got = so.fewer_repeats([*songs(4), *songs(4)], {}, NOW, random.Random(1), first="nope")

    assert sorted(s.id for s in got) == ["s0", "s1", "s2", "s3"]


def test_no_songs_no_order_one_song_itself() -> None:
    assert so.fewer_repeats([], {}, NOW, random.Random(1)) == []
    assert [s.id for s in so.fewer_repeats(songs(1), {}, NOW, random.Random(1), first="s0")] == ["s0"]


@pytest.mark.parametrize("seed", range(30))
def test_songs_of_one_artist_do_not_follow_one_another(seed: int) -> None:
    src = songs(24, artists=6)  # four songs of each of six artists

    got = so.fewer_repeats(src, {}, NOW, random.Random(seed), first="s0")

    assert all(a.artist != b.artist for a, b in zip(got, got[1:], strict=False))


def test_an_artist_with_most_of_the_list_cannot_be_spread_but_nothing_breaks() -> None:
    src = [OwnSong(f"x{i}", "same") for i in range(6)] + [OwnSong("y", "other")]

    got = so.fewer_repeats(src, {}, NOW, random.Random(1))

    assert sorted(s.id for s in got) == sorted(s.id for s in src)


def test_songs_without_an_artist_are_never_repeats() -> None:
    src = [OwnSong(f"s{i}", "") for i in range(5)]

    assert len(so.fewer_repeats(src, {}, NOW, random.Random(1))) == 5


def test_recently_heard_songs_stand_later_than_the_ones_not_heard() -> None:
    src = songs(40)
    heard = {f"s{i}": NOW - timedelta(hours=1 + i) for i in range(1, 20)}  # half of them, in the last day
    heard_pos, fresh_pos = [], []
    for seed in range(40):
        got = [s.id for s in so.fewer_repeats(src, heard, NOW, random.Random(seed), first="s0")]
        heard_pos += [got.index(i) for i in heard]
        fresh_pos += [got.index(f"s{i}") for i in range(20, 40)]

    assert sum(heard_pos) / len(heard_pos) > sum(fresh_pos) / len(fresh_pos) + 3


def test_the_freshest_of_the_orders_is_taken() -> None:
    src = songs(30)
    heard = {f"s{i}": NOW - timedelta(days=i % 3) for i in range(15)}

    def cost(orders: int) -> float:
        total = 0.0
        for seed in range(60):
            got = so.fewer_repeats(src, heard, NOW, random.Random(seed), orders=orders)
            total += so.staleness([s.id for s in got], {k: (NOW - v).total_seconds() / 86400 for k, v in heard.items()})
        return total

    assert cost(20) < cost(1)


# --- picking a recommendation ---------------------------------------------------------------------------------------


def test_the_best_recommendation_whose_artist_is_not_near() -> None:
    ranked = [("r1", "a"), ("r2", "b"), ("r3", "c"), ("r4", "d")]

    assert so.pick_rec(ranked, recent_artists=["a"], neighbors=["b"]) == 2  # a: recently; b: next to its own song
    assert so.pick_rec(ranked, recent_artists=[], neighbors=[]) == 0
    assert so.pick_rec([], [], []) is None


def test_an_artist_may_come_back_after_seven_other_recommendations() -> None:
    ranked = [("r1", "a"), ("r2", "b"), ("r3", "z")]
    seven = list("abcdefg")  # the last seven recommendations: `a` and `b` are among them

    assert so.pick_rec(ranked, seven, []) == 2
    assert so.pick_rec(ranked, [*seven, "h"], []) == 0  # a is the eighth back: it may come again


def test_the_gap_gives_way_but_the_neighbours_artist_never_does() -> None:
    ranked = [("r1", "a"), ("r2", "b")]

    assert so.pick_rec(ranked, ["a", "b"], []) == 0  # everyone is recent: the best
    assert so.pick_rec(ranked, ["a", "b"], ["a"]) == 1  # ... but not the artist of the song next to it
    assert so.pick_rec([("r1", "a")], [], ["a"]) == 0  # nothing else at all: the best of all


# --- seeds ----------------------------------------------------------------------------------------------------------


def test_seeds_are_ten_songs_of_different_artists_with_the_newest_first() -> None:
    src = songs(40)

    new_last = so.choose_seeds(src, random.Random(1), newest_last=True)
    new_first = so.choose_seeds(src, random.Random(1), newest_last=False)

    assert len(new_last) == len(set(new_last)) == so.SEEDS and new_last[:3] == ["s39", "s38", "s37"]
    assert new_first[:3] == ["s0", "s1", "s2"]
    assert len({s for s in new_last}) == 10


def test_seeds_repeat_an_artist_only_when_the_artists_run_out() -> None:
    src = songs(40, artists=4)

    got = so.choose_seeds(src, random.Random(2), newest_last=True)
    artists = [s.artist for s in src if s.id in got]

    assert len(got) == 10 and max(artists.count(a) for a in set(artists)) <= 3


def test_a_short_list_gives_all_its_songs() -> None:
    assert sorted(so.choose_seeds(songs(3), random.Random(1), newest_last=True)) == ["s0", "s1", "s2"]
