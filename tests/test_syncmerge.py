"""Merge-engine properties: idempotent, commutative, monotone."""
from __future__ import annotations

import json
import sqlite3

import pytest

from kanjire.data import db
from kanjire.data.syncmerge import digest, export_snapshot, merge_snapshot


class _State:
    def __init__(self):
        self.data = {"high_scores": {}, "settings": {}, "presets": []}

    def save(self):
        pass


def _stats_con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.STATS_SCHEMA)
    from kanjire.srs.store import _SCHEMA
    con.executescript(_SCHEMA)
    return con


def _seed(con, expr, seen, matches, last_seen):
    con.execute(
        "INSERT INTO word_stats (expression, reading, seen, matches,"
        " last_seen_at, current_streak) VALUES (?, 'よみ', ?, ?, ?, 1)",
        (expr, seen, matches, last_seen))
    con.commit()


def test_counters_take_max_and_never_regress():
    a, b = _stats_con(), _stats_con()
    sa, sb = _State(), _State()
    _seed(a, "時間", seen=10, matches=8, last_seen="2026-07-01T10:00:00")
    _seed(b, "時間", seen=4, matches=9, last_seen="2026-07-02T10:00:00")

    merge_snapshot(a, sa, export_snapshot(b, sb))
    row = dict(a.execute("SELECT * FROM word_stats").fetchone())
    assert row["seen"] == 10          # local higher kept
    assert row["matches"] == 9        # remote higher adopted
    assert row["last_seen_at"] == "2026-07-02T10:00:00"


def test_merge_is_idempotent_and_commutative():
    a, b = _stats_con(), _stats_con()
    sa, sb = _State(), _State()
    _seed(a, "時間", 10, 8, "2026-07-01T10:00:00")
    _seed(a, "学校", 3, 2, "2026-07-01T11:00:00")
    _seed(b, "時間", 4, 9, "2026-07-02T10:00:00")
    _seed(b, "電話", 7, 7, "2026-06-30T10:00:00")
    sa.data["high_scores"] = {"Time Attack": 100}
    sb.data["high_scores"] = {"Time Attack": 250, "Zen": 40}

    # a <- b twice; b <- a twice: both settle on the same content.
    for _ in range(2):
        merge_snapshot(a, sa, export_snapshot(b, sb))
    for _ in range(2):
        merge_snapshot(b, sb, export_snapshot(a, sa))
    # one more round-trip proves a fixed point
    merge_snapshot(a, sa, export_snapshot(b, sb))
    assert digest(export_snapshot(a, sa)) == digest(export_snapshot(b, sb))
    assert sa.data["high_scores"] == {"Time Attack": 250, "Zen": 40}


def test_srs_newest_review_wins():
    a, b = _stats_con(), _stats_con()
    sa, sb = _State(), _State()
    a.execute("INSERT INTO srs_state (expression, reading, state, due,"
              " last_review, stability, lapses) VALUES"
              " ('時間','じかん',2,'2026-07-05','2026-07-01',3.0,0)")
    b.execute("INSERT INTO srs_state (expression, reading, state, due,"
              " last_review, stability, lapses) VALUES"
              " ('時間','じかん',2,'2026-07-09','2026-07-03',5.0,1)")
    merge_snapshot(a, sa, export_snapshot(b, sb))
    row = dict(a.execute("SELECT * FROM srs_state").fetchone())
    assert row["last_review"] == "2026-07-03"
    assert row["due"] == "2026-07-09"
    assert row["lapses"] == 1
    # And the older side must NOT overwrite back.
    merge_snapshot(b, sb, export_snapshot(a, sa))
    row_b = dict(b.execute("SELECT * FROM srs_state").fetchone())
    assert row_b["last_review"] == "2026-07-03"


def test_logs_union_without_duplicates():
    a, b = _stats_con(), _stats_con()
    sa, sb = _State(), _State()
    a.execute("INSERT INTO review_log (ts, day, expression, reading, event,"
              " rating) VALUES ('t1','d1','時間','じかん','match',3)")
    b.execute("INSERT INTO review_log (ts, day, expression, reading, event,"
              " rating) VALUES ('t1','d1','時間','じかん','match',3)")
    b.execute("INSERT INTO review_log (ts, day, expression, reading, event,"
              " rating) VALUES ('t2','d1','学校','がっこう','match',4)")
    merge_snapshot(a, sa, export_snapshot(b, sb))
    merge_snapshot(a, sa, export_snapshot(b, sb))   # idempotent
    n = a.execute("SELECT COUNT(*) c FROM review_log").fetchone()["c"]
    assert n == 2


def test_streak_newest_day_wins():
    a_state, b_state = _State(), _State()
    a, b = _stats_con(), _stats_con()
    a_state.data["settings"].update(streak_count=10, streak_freezes=2,
                                    streak_day="2026-07-10")
    b_state.data["settings"].update(streak_count=3, streak_freezes=0,
                                    streak_day="2026-07-12")
    merge_snapshot(a, a_state, export_snapshot(b, b_state))
    assert a_state.data["settings"]["streak_count"] == 3
    assert a_state.data["settings"]["streak_day"] == "2026-07-12"
    # The older side pushed the other way must not regress the newer one.
    merge_snapshot(b, b_state, export_snapshot(a, a_state))
    assert b_state.data["settings"]["streak_day"] == "2026-07-12"
    assert b_state.data["settings"]["streak_count"] == 3


def test_presets_union_by_name():
    a_state, b_state = _State(), _State()
    a, b = _stats_con(), _stats_con()
    a_state.data["presets"] = [{"name": "Drill", "duration": 45}]
    b_state.data["presets"] = [{"name": "Drill", "duration": 90},
                               {"name": "Boss", "duration": 30}]
    merge_snapshot(a, a_state, export_snapshot(b, b_state))
    names = sorted(p["name"] for p in a_state.data["presets"])
    assert names == ["Boss", "Drill"]
    drill = next(p for p in a_state.data["presets"] if p["name"] == "Drill")
    assert drill["duration"] == 45   # local wins the clash


def test_snapshot_round_trips_json():
    a, sa = _stats_con(), _State()
    _seed(a, "時間", 10, 8, "2026-07-01T10:00:00")
    snap = export_snapshot(a, sa)
    again = json.loads(json.dumps(snap, ensure_ascii=False))
    assert digest(again) == digest(snap)


# --------------------------------------------------------------------------- #
# The reading library travels between devices
# --------------------------------------------------------------------------- #
def _device(tmp_path, name):
    from kanjire.data import db
    from kanjire.data.stats import StatsRecorder
    from kanjire.userstate import UserState
    con = db.connect(tmp_path / f"{name}.db")
    StatsRecorder(con)
    return con, UserState(tmp_path / f"{name}.json")


def _sync(src, dst):
    """Push src's snapshot into dst (one direction)."""
    from kanjire.data import syncmerge
    snap = syncmerge.export_snapshot(*src)
    return syncmerge.merge_snapshot(dst[0], dst[1], snap)


def test_a_novel_travels_to_the_other_device(tmp_path):
    from kanjire.data.library import Library
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    Library(a[0]).add("Ch1", "私は本を読む。今日は暑い。")
    _sync(a, b)
    books = Library(b[0]).books()
    assert [x["title"] for x in books] == ["Ch1"]
    assert Library(b[0]).sentences(books[0]["id"]) == ["私は本を読む。",
                                                      "今日は暑い。"]


def test_the_furthest_position_wins(tmp_path):
    from kanjire.data.library import Library
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    la = Library(a[0])
    bid = la.add("Novel", "一。二。三。四。五。")
    _sync(a, b)
    lb = Library(b[0])
    other = lb.books()[0]["id"]
    lb.save_position(other, 4)          # read further on device B
    la.save_position(bid, 1)
    _sync(b, a)
    assert la.get(bid)["position"] == 4, "a staler cursor overwrote progress"
    _sync(a, b)
    assert lb.get(other)["position"] == 4


def test_the_same_text_added_twice_is_one_book(tmp_path):
    from kanjire.data.library import Library
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    Library(a[0]).add("Ch1", "私は本を読む。")
    Library(b[0]).add("Ch1", "私は本を読む。")   # added on both devices
    _sync(a, b)
    assert len(Library(b[0]).books()) == 1, "the same passage duplicated"


def test_deleting_propagates_and_is_not_revived(tmp_path):
    from kanjire.data.library import Library
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    la = Library(a[0])
    bid = la.add("Gone", "一。二。")
    _sync(a, b)
    lb = Library(b[0])
    assert lb.books()
    la.delete(bid)
    _sync(a, b)
    assert lb.books() == [], "the deletion did not travel"
    # And B syncing back must not bring it back to A.
    _sync(b, a)
    assert la.books() == [], "a tombstoned novel came back"


def test_an_older_peer_without_a_library_deletes_nothing(tmp_path):
    """A device on the previous snapshot version omits the key entirely."""
    from kanjire.data import syncmerge
    from kanjire.data.library import Library
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    Library(a[0]).add("Keep", "一。二。")
    old = syncmerge.export_snapshot(*b)
    old.pop("library")
    syncmerge.merge_snapshot(a[0], a[1], old)
    assert [x["title"] for x in Library(a[0]).books()] == ["Keep"]


def test_the_crutch_state_follows_the_more_recent_reader(tmp_path):
    from kanjire.data.library import Library
    from kanjire.data.weave import Token, WeaveState
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    la = Library(a[0])
    bid = la.add("Novel", "一。二。三。")
    _sync(a, b)
    lb = Library(b[0])
    other = lb.books()[0]["id"]
    st = WeaveState()
    st.tapped(Token("大学", "大学", "だいがく", "College", 5), None)
    lb.save_position(other, 2, weave=st)
    _sync(b, a)
    assert la.load_weave(bid).taps, "the looked-up words did not travel"


def test_two_devices_converge_to_one_digest(tmp_path):
    from kanjire.data import syncmerge
    from kanjire.data.library import Library
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    Library(a[0]).add("Ch1", "私は本を読む。今日は暑い。")
    _sync(a, b)
    _sync(b, a)
    assert (syncmerge.digest(syncmerge.export_snapshot(*a))
            == syncmerge.digest(syncmerge.export_snapshot(*b))),         "the devices never agreed, so sync would churn forever"


def test_an_oversized_passage_stays_local_without_breaking_sync(tmp_path):
    from kanjire.data import library as lib_mod
    from kanjire.data.library import Library
    a, b = _device(tmp_path, "a"), _device(tmp_path, "b")
    la = Library(a[0])
    la.add("Huge", "".join(f"文{i}。" for i in range(400)))
    old_cap = lib_mod.MAX_SYNC_CHARS
    lib_mod.MAX_SYNC_CHARS = 10          # force it over the ceiling
    try:
        _sync(a, b)
    finally:
        lib_mod.MAX_SYNC_CHARS = old_cap
    # Nothing arrives, but nothing explodes either.
    assert Library(b[0]).books() == []
