"""Tests for the coverage meter and placement (mark-as-known) seeding."""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.data import db
from kanjire.data.coverage import all_coverage, deck_coverage, known_keys
from kanjire.data.stats import StatsRecorder, classify
from kanjire.model.vocab import Word


def _vocab_con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    db.init_db(con)
    db.upsert_deck(con, "jlpt", "jlpt")
    # One very common word (zipf 6), one medium (4), two rare (2).
    rows = [
        ("食べる", "たべる", "To eat", 5, 6.0),
        ("山", "やま", "Mountain", 5, 4.0),
        ("珍しい", "めずらしい", "Rare", 4, 2.0),
        ("鯨", "くじら", "Whale", 3, 2.0),
    ]
    for expr, read, mean, lv, freq in rows:
        db.upsert_word(con, deck="jlpt", expression=expr, reading=read,
                       meaning=mean, jlpt=lv, freq=freq)
    return con


def _stats_con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return con


def _w(expr, read, mean="x", lv=5, freq=3.0):
    return Word(id=hash(expr) & 0xFFFF, expression=expr, reading=read,
                meaning=mean, jlpt=lv, freq=freq, deck="jlpt")


def test_coverage_weights_by_frequency():
    vcon = _vocab_con()
    rec = StatsRecorder(_stats_con())
    # Knowing only the most common word: weight 10^6 of total ~1.0101e6.
    rec.saw(_w("食べる", "たべる"))
    rec.matched(_w("食べる", "たべる"))
    cov = deck_coverage(vcon, known_keys(rec), "jlpt")
    assert cov["known_n"] == 1 and cov["total_n"] == 4
    assert cov["pct"] > 95, cov
    # Knowing only a rare word instead: nearly zero coverage.
    rec.reset_all()
    rec.saw(_w("鯨", "くじら"))
    rec.matched(_w("鯨", "くじら"))
    cov = deck_coverage(vcon, known_keys(rec), "jlpt")
    assert cov["pct"] < 1, cov
    # Milestone points at the most frequent unknown words.
    assert cov["next_milestone_words"] >= 1


def test_all_coverage_lists_jlpt_first():
    vcon = _vocab_con()
    db.upsert_deck(vcon, "corpus:mytext", "corpus")
    db.upsert_word(vcon, deck="corpus:mytext", expression="山", reading="やま",
                   meaning="Mountain", freq=3.0, count=10)
    rec = StatsRecorder(_stats_con())
    out = all_coverage(vcon, rec)
    assert [name for name, _ in out] == ["jlpt", "corpus:mytext"]


def test_mark_known_seeds_stats_and_srs():
    rec = StatsRecorder(_stats_con())
    words = [_w("食べる", "たべる"), _w("山", "やま"), _w("鯨", "くじら")]
    n = rec.mark_known(words)
    assert n == 3
    for w in words:
        row = rec.get_for(w.expression, w.reading)
        assert row and classify(row) == "known", row
    # SRS: seeded as future reviews (not due now), spread out.
    assert rec.srs.due_count() == 0
    assert len(rec.srs.tracked_keys()) == 3
    dues = [r["due"] for r in rec.con.execute("SELECT due FROM srs_state")]
    assert len(set(dues)) == 3, "due dates should be spread, not identical"
    # Seeded words don't count as introduced-today (created_day is today
    # though — allowance shrinks, which is intended: placement day is a
    # big enough bite already).
    # Re-marking is a no-op for SRS state.
    before = sorted(dues)
    rec.mark_known(words)
    after = sorted(r["due"] for r in rec.con.execute("SELECT due FROM srs_state"))
    assert before == after


def test_mark_known_preserves_existing_history():
    rec = StatsRecorder(_stats_con())
    w = _w("山", "やま")
    rec.saw(w)
    rec.matched(w)
    rec.matched(w)
    rec.mark_known([w])
    row = rec.get_for(w.expression, w.reading)
    assert row["matches"] == 2, "placement must not clobber real history"


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
