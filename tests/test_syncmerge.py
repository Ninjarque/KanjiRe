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
# New tables must not disturb sync
# --------------------------------------------------------------------------- #
def test_the_reading_library_does_not_break_sync(tmp_path):
    """The library lives in the same per-user DB as stats.

    Two devices must still agree on a digest even when one of them has read a
    novel the other has never seen — the snapshot is an explicit list of
    tables, and the library is deliberately not in it (yet). This pins that:
    if the library is ever added to the snapshot, this test must be updated
    on purpose rather than a novel silently making sync churn forever.
    """
    from kanjire.data import db, syncmerge
    from kanjire.data.library import Library
    from kanjire.data.stats import StatsRecorder
    from kanjire.userstate import UserState

    def fresh(name):
        con = db.connect(tmp_path / f"{name}.db")
        StatsRecorder(con)
        return con, UserState(tmp_path / f"{name}.json")

    a_con, a_state = fresh("a")
    b_con, b_state = fresh("b")
    before = syncmerge.digest(syncmerge.export_snapshot(a_con, a_state))
    assert before == syncmerge.digest(
        syncmerge.export_snapshot(b_con, b_state))

    Library(a_con).add("Novel", "私は本を読む。今日は暑い。")
    after = syncmerge.export_snapshot(a_con, a_state)
    assert syncmerge.digest(after) == before, \
        "adding a library book changed the sync digest"
    assert "library" not in after, \
        "the library entered the snapshot without merge rules"
