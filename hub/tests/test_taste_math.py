"""Active time and M4 math use explicit events/clock only: no DB, network or sleeps."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.favorites import effective_decisions, fold
from app.mixes.taste import artist_key
from app.mixes.taste_math import ActiveClock, calculate_offline, calculate_profile, saturation
from app.mixes.taste_snapshot import (
    DislikedTarget,
    SavedDownload,
    Subject,
    TasteEvent,
    TasteSnapshot,
    TrackMetadata,
    calendar_cutoff,
)

NOW = datetime(2026, 10, 27, 12)


def event(eid="e", kind="finish", at=NOW, ref="dz:1", **kwargs):
    return TasteEvent(1, eid, at, kind, ref, received_at=kwargs.pop("received_at", at), **kwargs)


def profile(*events, now=NOW, timezone="Europe/Moscow", **snapshot):
    return calculate_profile(TasteSnapshot(1, tuple(events), **snapshot), now=now, timezone=timezone)


@pytest.mark.parametrize("spacing,count,tracks,age", [
    (1, 30, 5, 30), (7, 8, 5, 8), (1, 30, 2, 12), (7, 8, 2, 3.2), (1, 1, 60, 1),
], ids=["daily", "weekly", "two_per_day", "weekly_fraction", "binge_capped"])
def test_only_distinct_heard_tracks_advance_active_days(spacing, count, tracks, age):
    origin = NOW - timedelta(days=spacing * count)
    rows = [event("old", at=origin, ref="dz:old")]
    rows += [event(f"{day}-{n}", at=origin + timedelta(days=spacing * day), ref=f"dz:{n}")
             for day in range(1, count + 1) for n in range(tracks)]
    p = profile(*rows)
    assert p.clock.age(origin) == pytest.approx(age)
    assert p.tracks["dz:old"].score == pytest.approx(2 * .5 ** (age / 30))


def test_weekends_same_day_repeats_and_current_day_age_zero():
    origin = NOW - timedelta(days=28)
    rows = [event("old", at=origin)]
    rows += [event(f"{day}-{n}-{repeat}", at=origin + timedelta(days=day), ref=f"dz:{n}")
             for day in (5, 6, 12, 13, 19, 20, 26, 27) for n in range(5) for repeat in range(4)]
    p = profile(*rows)
    assert p.clock.age(origin) == 8
    assert p.clock.age(NOW) == 0


def test_45_silent_days_preserve_score_and_active_pool():
    snap = TasteSnapshot(1, (event(), event("like", "like")))
    first = calculate_profile(snap, now=NOW, timezone="Europe/Moscow")
    later = calculate_profile(snap, now=NOW + timedelta(days=45), timezone="Europe/Moscow")
    assert first.tracks["dz:1"].score == later.tracks["dz:1"].score
    assert [t.key for t in first.active_seed_pool()] == [t.key for t in later.active_seed_pool()] == ["dz:1"]


def test_alias_repeats_and_typed_actions_are_counted_once_per_local_day():
    rows = [event("old", at=NOW - timedelta(days=1), ref="dz:old"),
            event("ref", ref="dz:1"), event("nd", ref=None, nd_song_id="song"),
            event("l", "like"), event("duplicate", "like"), event("u", "unlike"),
            event("al", "like", target_type="album"), event("ar", "like", target_type="artist")]
    p = profile(*rows, subjects=(Subject("track", ("ref:dz:1", "nd:song"), "dz:1", "song"),))
    assert p.clock.units[-1] == .8  # one heard + three typed action objects
    assert len(p.tracks) == 2
    assert p.find(ref="dz:1") is p.find(nd_song_id="song")


@pytest.mark.parametrize("zone,start,end,age", [
    ("Europe/Moscow", datetime(2026, 10, 26, 20, 59), datetime(2026, 10, 26, 21, 1), .2),
    ("Europe/Berlin", datetime(2026, 10, 25, 0, 30), datetime(2026, 10, 25, 1, 30), 0),
    ("UTC", datetime(2026, 10, 26, 23, 59), datetime(2026, 10, 27, 0, 1), .2),
], ids=["local_midnight", "dst_same_local_day", "utc_midnight"])
def test_local_days_are_explicit_and_dst_safe(zone, start, end, age):
    p = profile(event("before", at=start), event("after", at=end, ref="dz:2"), now=end, timezone=zone)
    assert p.clock.age(start) == pytest.approx(age)


def test_future_delayed_debug_import_and_other_users_are_not_accumulated():
    rows = (event("future", at=NOW + timedelta(minutes=1)),
            event("delayed", at=NOW - timedelta(days=1), received_at=NOW + timedelta(days=1)),
            event("debug", client="app/1.5-debug"), event("import", "like", client="import"),
            replace(event("other"), user_id=2))
    snapshot = TasteSnapshot(1, rows)
    assert not calculate_profile(snapshot, now=NOW, timezone="UTC", mode="available-at").tracks
    p = calculate_profile(snapshot, now=NOW, timezone="UTC", mode="occurred-at")
    assert p.tracks["dz:1"].score == 2
    assert not calculate_profile(replace(snapshot, excluded_user_ids=frozenset({1})),
                                 now=NOW, timezone="UTC").tracks


def test_three_calendar_year_boundary_and_old_permanent_like():
    cutoff = calendar_cutoff(NOW)
    old = event("old", "like", cutoff - timedelta(microseconds=1), "dz:old")
    p = profile(old, event("edge", at=cutoff, ref="dz:edge"),
                event("too_old", at=cutoff - timedelta(microseconds=1), ref="dz:outside"))
    assert p.tracks["dz:old"].liked and p.tracks["dz:old"].score == 0
    assert p.tracks["dz:edge"].score == 2
    assert "dz:outside" not in p.tracks
    assert calendar_cutoff(datetime(2024, 2, 29)) == datetime(2021, 2, 28)


def test_weight_cutoff_is_inclusive_and_activity_is_not_cut_by_weight(monkeypatch):
    # Exact threshold through a clock input, rather than 1000 synthetic full activity days.
    clock = ActiveClock.from_counts({}, "UTC")
    assert clock.weight(NOW) == 1
    monkeypatch.setattr(ActiveClock, "weight", lambda self, at: .01)
    p = profile(event())
    assert p.tracks["dz:1"].score == .02 and p.clock.units == (.2,)
    monkeypatch.setattr(ActiveClock, "weight", lambda self, at: .009999)
    p = profile(event())
    assert p.tracks["dz:1"].score == 0 and p.clock.units == (.2,)


def test_saturation_and_negative_weights_are_separate():
    assert saturation(60) == pytest.approx(8.8023947633)
    p = profile(*(event(str(i)) for i in range(30)), event("skip", "skip", position_s=10))
    t = p.tracks["dz:1"]
    assert t.positive_raw == 60 and t.negative == 1
    assert t.score == pytest.approx(saturation(60) - 1)
    assert t.last_positive == t.last_heard == t.last_played == NOW


def test_current_like_is_one_contribution_and_unlike_is_neutral():
    p = profile(event("finish"), event("like", "like"), event("duplicate", "like"))
    assert p.tracks["dz:1"].positive_raw == 3
    p = profile(event("finish"), event("like", "like"),
                event("unlike", "unlike", at=NOW + timedelta(seconds=1)), now=NOW + timedelta(seconds=1))
    assert p.tracks["dz:1"].score == 2 and not p.tracks["dz:1"].liked
    assert not p.negative_keys()


def test_playback_alias_pair_also_unifies_favorite_state_and_current_vector_key():
    p = profile(event("finish", nd_song_id="song"),
                event("like-ref", "like", at=NOW - timedelta(seconds=2)),
                event("like-nd", "like", ref=None, nd_song_id="song", at=NOW - timedelta(seconds=1)))
    assert len(p.tracks) == 1 and p.tracks["nd:song"].positive_raw == 3


@pytest.mark.parametrize("hypothesis,rows,P,N", [
    ("P1", (event("first", "skip", NOW - timedelta(seconds=10), position_s=1, context="album:1"),
             event("second", "skip", position_s=1, context="album:1")), 0, 1),
    ("P2", (event("skip", "skip", position_s=5, context="search"),), 0, .5),
    ("P4", (event("skip", "skip", position_s=20),), 0, .5),
    ("P5", (event("finish", context="wave:1"),), 1.5, 0),
])
def test_offline_hypotheses_have_explicit_weights(hypothesis, rows, P, N):
    p = calculate_offline(TasteSnapshot(1, rows), now=NOW, timezone="UTC", experiment=hypothesis)
    assert p.tracks["dz:1"].positive_raw == P and p.tracks["dz:1"].negative == N


def test_offline_p3_threshold_uses_pre_decision_score_including_own_like():
    rows = (*(event(str(i), at=NOW - timedelta(seconds=2)) for i in range(3)),
        event("like", "like", NOW - timedelta(seconds=1)), event("skip", "skip", position_s=5))
    p = calculate_offline(TasteSnapshot(1, rows), now=NOW, timezone="UTC", experiment="P3")
    assert p.tracks["dz:1"].negative == .5


def test_negative_tracks_are_not_explicitly_filtered_or_familiar():
    p = profile(event("skip", "skip", position_s=10), event("start", "start", ref="dz:start"),
                event("skip2", "skip", ref="dz:negative", position_s=10),
                event("skip3", "skip", ref="dz:negative", position_s=10))
    assert not p.known(ref="dz:1") and not p.known(ref="dz:start")
    assert p.tracks["dz:1"].last_heard is None
    assert not p.disliked(ref="dz:1", nd_song_id=None, artist="", artist_ref=None)
    assert p.negative_keys() == {"dz:negative"}


def test_explicit_dislike_survives_age_and_unjournalled_state_is_supported():
    old = calendar_cutoff(NOW) - timedelta(days=1)
    p = profile(event("dislike", "dislike", old),
                dislikes=(DislikedTarget(ref="dz:legacy", at=old),))
    assert p.negative_keys() == {"dz:1", "dz:legacy"}
    assert p.disliked(ref="dz:1", nd_song_id=None, artist="", artist_ref=None)
    assert not p.tracks["dz:1"].familiar
    p = profile(event("dislike", "dislike"), event("undo", "undislike", NOW + timedelta(seconds=1)),
                now=NOW + timedelta(seconds=1), dislikes=(DislikedTarget(ref="dz:1", at=NOW),))
    assert not p.negative_keys() and p.tracks["dz:1"].score == 0


def test_debug_dislike_does_not_leak_back_through_legacy_current_row():
    p = profile(event("debug", "dislike", client="app/2-debug"),
                dislikes=(DislikedTarget(ref="dz:1", at=NOW),))
    assert not p.tracks and not p.disliked_ids and not p.negative_keys()


def test_short_completed_track_is_heard_but_incomplete_short_finish_is_not():
    p = profile(event("short", position_s=20, duration_s=20),
                event("bad", ref="dz:bad", position_s=10, duration_s=20))
    assert p.tracks["dz:1"].heard30 and not p.tracks["dz:bad"].heard30


def test_album_and_artist_likes_contribute_only_to_artist_and_album_stash():
    p = profile(event("album", "like", ref="dz:album", target_type="album", target_data={"artist": "Artist"}),
                event("artist", "like", ref="dz:artist", target_type="artist", target_data={"name": "Artist"}),
                metadata=(TrackMetadata("dz:track", "song", "Artist", "Title", 200, "dz:album"),))
    t = p.tracks["nd:song"]
    assert t.album_liked and t.familiar and not t.liked and t.score == t.positive_raw == 0
    assert p.artists[artist_key("Artist")] == 1.5
    assert p.maturity == 0 and p.active_seed_pool() == []
    assert not p.empty and {st.target_type for st in p.favorite_states.values() if st.liked} == {"album", "artist"}


def test_download_familiarity_maturity_and_recent_positive_are_independent():
    p = profile(event("weak", "skip", position_s=31, duration_s=200),
                downloads=(SavedDownload("dz:saved", None, NOW),))
    assert p.known(ref="dz:saved") and p.has_recent_positive and not p.active_seed_pool()
    assert 0 < p.maturity < .01
    p = profile(*(event(str(i), ref=f"dz:{i}") for i in range(10)))
    assert p.evidence == 20 and p.maturity == 1


def test_artist_score_saturates_positive_and_negative_groups_separately():
    p = profile(*(event(str(i), ref=f"dz:{i}", target_data={"artist": "A", "title": str(i)}) for i in range(3)),
                event("n1", "skip", ref="dz:n", position_s=5, target_data={"artist": "A"}),
                event("n2", "skip", ref="dz:n", position_s=5))
    assert p.artists[artist_key("A")] == pytest.approx(saturation(6) - .5 * saturation(2))


def test_effective_decisions_share_compensation_chain_and_redundancy_with_fold():
    a = event("A", "like", NOW - timedelta(days=2))
    duplicate = event("dup", "like", NOW - timedelta(days=1))
    c = event("C", "unlike", reverts_event_id="A")
    r = event("R", "like", NOW + timedelta(seconds=1), reverts_event_id="C")
    dangling = event("dangling", "like", reverts_event_id="missing")
    x = event("X", "like", reverts_event_id="Y")
    y = event("Y", "unlike", reverts_event_id="X")
    rows = [duplicate, r, c, a, dangling, x, y]
    assert [e.event_id for e in effective_decisions(rows)] == ["A"]
    st = next(iter(fold(rows).values()))
    assert st.liked and st.since == a.ts
    p = profile(*rows, now=r.ts)
    assert p.tracks["dz:1"].positive_raw == 1
    assert p.clock.units == (.2,)  # restored original day, not retry/compensation days


@pytest.mark.parametrize("hypothesis", ["P1", "P2", "P3", "P4", "P5"])
def test_offline_hypotheses_do_not_change_online_m4(hypothesis):
    rows = (event("first", "skip", NOW - timedelta(seconds=10), position_s=1, context="album:1"),
            event("second", "skip", position_s=1, context="album:1"),
            event("f", context="wave:x", ref="dz:2"))
    snap = TasteSnapshot(1, rows)
    before = calculate_profile(snap, now=NOW, timezone="UTC")
    calculate_offline(snap, now=NOW, timezone="UTC", experiment=hypothesis)
    after = calculate_profile(snap, now=NOW, timezone="UTC")
    assert before.vector_profile() == after.vector_profile() == {"dz:1": -2, "dz:2": 2}


def test_timezone_aware_input_is_normalized_to_utc():
    p = profile(event(at=NOW.replace(tzinfo=UTC)), now=NOW.replace(tzinfo=UTC))
    assert p.now == NOW and p.tracks["dz:1"].score == 2
