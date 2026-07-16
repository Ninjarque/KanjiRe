"""Sentence difficulty rating + the reading curriculum ordering."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.data.reading_level import (fits, rate_from_heads, rate_sentence,
                                        word_difficulty)


def test_jlpt_and_frequency_place_words_on_one_scale():
    assert word_difficulty(5, None) == 1.0        # N5 easiest
    assert word_difficulty(1, None) == 5.0        # N1 hardest
    # No JLPT: a common word is easy, a rare one is hard.
    assert word_difficulty(None, 6.0) == 1.0
    assert word_difficulty(None, 2.0) == 5.0
    # JLPT wins over frequency when both exist.
    assert word_difficulty(5, 1.0) == 1.0


def test_rating_reports_average_peak_and_the_hardest_word():
    r = rate_sentence([("a", 1.0), ("b", 2.0), ("c", 4.0)])
    assert r.average == round((1 + 2 + 4) / 3, 3)
    assert r.peak == 4.0 and r.hardest == "c"
    assert r.spike == r.peak - r.average
    assert rate_sentence([]) is None


def test_fits_respects_target_and_spread():
    r = rate_sentence([("a", 2.0), ("b", 2.0), ("c", 3.0)])  # avg~2.33 peak 3
    assert fits(r, target=3.0, spread=1.0)          # comfortably within
    assert not fits(r, target=1.0, spread=0.5)      # peak 3 > 1+0.5
    # Easier than target is always fine.
    easy = rate_sentence([("a", 1.0), ("b", 1.0)])
    assert fits(easy, target=4.0, spread=1.0)


def test_rate_from_heads_uses_the_map_and_skips_unrated():
    diff = {"日本語": 2.0, "勉強": 3.0}
    r = rate_from_heads(["日本語", "勉強", "竹内"], diff)   # 竹内 unrated -> skipped
    assert r is not None and r.n_words == 2
    assert r.peak == 3.0
    # A sentence of only unrated words has no difficulty.
    assert rate_from_heads(["竹内", "トロイカ"], diff) is None


def test_load_word_difficulty_keeps_the_easiest_reading():
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE words (expression TEXT, jlpt INT, freq REAL)")
    con.executemany("INSERT INTO words VALUES (?,?,?)",
                    [("生", 5, 5.0), ("生", 1, 3.0)])   # same word, two levels
    from kanjire.data.reading_level import load_word_difficulty

    d = load_word_difficulty(con)
    assert d["生"] == 1.0, "should keep the easiest (N5) placement"
