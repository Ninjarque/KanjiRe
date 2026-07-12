"""Tests for the FSRS-backed SRS store and its StatsRecorder integration."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.data.stats import StatsRecorder
from kanjire.model.vocab import Word
from kanjire.srs.store import (
    AGAIN, GOOD, DUE_SOFT_CEILING, NEW_TARGET_PER_DAY, SrsStore,
)


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return con


def _w(i, expr, reading):
    return Word(id=i, expression=expr, reading=reading, meaning=f"m{i}",
                jlpt=5, freq=3.0, deck="test")


def test_good_reviews_grow_the_interval():
    store = SrsStore(_con())
    assert store.enabled, "fsrs must be installed for the test suite"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.update("食べる", "たべる", GOOD, t0)
    row1 = dict(store.con.execute("SELECT * FROM srs_state").fetchone())
    # Learning step: due within the hour
    due1 = datetime.fromisoformat(row1["due"])
    assert due1 - t0 < timedelta(hours=1)
    # Keep rating Good at each due date: intervals must stretch to days
    t, due = t0, due1
    for _ in range(4):
        t = due + timedelta(minutes=1)
        store.update("食べる", "たべる", GOOD, t)
        due = datetime.fromisoformat(dict(
            store.con.execute("SELECT * FROM srs_state").fetchone())["due"])
    assert due - t > timedelta(days=1), f"interval did not grow: {due - t}"


def test_again_marks_lapse_and_brings_word_back():
    store = SrsStore(_con())
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        store.update("犬", "いぬ", GOOD, t0 + timedelta(days=i * 3))
    row = dict(store.con.execute("SELECT * FROM srs_state").fetchone())
    long_due = datetime.fromisoformat(row["due"])
    t_fail = t0 + timedelta(days=20)
    store.update("犬", "いぬ", AGAIN, t_fail)
    row = dict(store.con.execute("SELECT * FROM srs_state").fetchone())
    assert row["lapses"] == 1
    new_due = datetime.fromisoformat(row["due"])
    assert new_due < long_due, "a lapse must shorten the schedule"
    assert new_due - t_fail < timedelta(days=1)


def test_due_keys_sorted_by_risk():
    store = SrsStore(_con())
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Word A reviewed long ago (very overdue = low retrievability), word B
    # reviewed recently (just barely due).
    store.update("古い", "ふるい", GOOD, t0)
    store.update("新しい", "あたらしい", GOOD, t0 + timedelta(days=30))
    now = t0 + timedelta(days=40)
    keys = store.due_keys(now)
    assert keys, "both words should be due"
    assert keys[0] == ("古い", "ふるい"), f"most at-risk first: {keys}"


def test_new_allowance_shrinks_with_due_pile():
    store = SrsStore(_con())
    assert store.new_allowance() == NEW_TARGET_PER_DAY  # empty pile
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(DUE_SOFT_CEILING):
        store.update(f"語{i}", f"ご{i}", GOOD, t0)
    now = t0 + timedelta(days=365)  # everything long due
    assert store.due_count(now) >= DUE_SOFT_CEILING
    assert store.new_allowance(now) == 0


def test_leech_keys():
    store = SrsStore(_con())
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(7):
        store.update("難", "むずかしい", AGAIN, t + timedelta(hours=i))
        store.update("楽", "たのしい", GOOD, t + timedelta(hours=i))
    leeches = store.leech_keys(min_lapses=6)
    assert leeches == [("難", "むずかしい")]


def test_stats_recorder_feeds_srs():
    con = _con()
    rec = StatsRecorder(con)
    assert rec.srs is not None and rec.srs.enabled
    a, b = _w(1, "食べる", "たべる"), _w(2, "山", "やま")
    rec.matched(a)                    # clean -> Good
    rec.matched(b, clean=False)       # errored -> Hard
    rec.confused(a, b, "reading")     # both lapse
    rows = {(r["expression"], r["reading"]): dict(r)
            for r in con.execute("SELECT * FROM srs_state")}
    assert set(rows) == {("食べる", "たべる"), ("山", "やま")}
    assert all(r["lapses"] == 1 for r in rows.values())
    # review_log carries the ratings: Good, Hard, then two Agains
    ratings = [r["rating"] for r in con.execute(
        "SELECT rating FROM review_log ORDER BY id")]
    assert ratings == [3, 2, 1, 1]
    # reset clears srs state too
    rec.reset_all()
    assert con.execute("SELECT COUNT(*) c FROM srs_state").fetchone()["c"] == 0


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
