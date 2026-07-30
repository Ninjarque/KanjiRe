"""Tapping flips a word — and counts exactly once per appearance.

The cap is the whole point. A novel contains the same word dozens of times, so
without a per-appearance limit anybody could tap one word back and forth and
drive their own knowledge buckets wherever they liked. These tests pin that
down, because a metric you can farm is worse than no metric.
"""
from __future__ import annotations

import sqlite3

from kanjire.data.stats import StatsRecorder, classify
from kanjire.data.weave import Token, WeaveState, stamp_slots


def W(surface, expr=None, reading="", meaning="gloss", jlpt=5, slot=""):
    return Token(surface, expr if expr is not None else surface, reading,
                 meaning, jlpt, slot)


def test_slots_label_each_appearance_of_a_word():
    groups = stamp_slots([[W("犬"), W("が")], [W("犬")]])
    assert [t.slot for g in groups for t in g] == ["0:0", "0:1", "1:0"]
    # Same word, different appearances — that is what makes the cap possible.
    assert groups[0][0].key == groups[1][0].key
    assert groups[0][0].slot != groups[1][0].slot


def test_a_tap_turns_a_japanese_word_english_and_counts_it_missed():
    st = WeaveState()
    tok = W("大学", slot="0:0")
    english, verdict = st.flip(tok, None)
    assert english is True
    assert verdict == "missed"


def test_tapping_the_english_back_returns_japanese_and_counts_it_learned():
    st = WeaveState()
    first, second = W("大学", slot="0:0"), W("大学", slot="7:2")
    st.flip(first, None)                      # -> English  (missed)
    english, verdict = st.flip(second, None)   # -> Japanese (learned)
    assert english is False
    assert verdict == "learned"


def test_one_appearance_can_never_count_twice():
    """Flip the SAME appearance back and forth: one verdict, then silence."""
    st = WeaveState()
    tok = W("大学", slot="3:1")
    verdicts = [st.flip(tok, None)[1] for _ in range(6)]
    assert verdicts[0] == "missed"
    assert verdicts[1:] == ["", "", "", "", ""], (
        "an appearance scored more than once — the buckets are farmable")


def test_the_word_still_flips_after_it_has_stopped_counting():
    """The cap is on evidence, not on the reader's freedom."""
    st = WeaveState()
    tok = W("大学", slot="3:1")
    states = [st.flip(tok, None)[0] for _ in range(4)]
    assert states == [True, False, True, False]


def test_dropping_the_crutch_lets_later_appearances_be_japanese():
    st = WeaveState()
    a, b = W("大学", slot="0:0"), W("大学", slot="9:0")
    st.flip(a, None)                     # English, with a hold
    assert st.show_english(b, None) is True
    st.flip(b, None)                     # turned back: hold cleared
    assert st.show_english(W("大学", slot="12:0"), None) is False, \
        "later appearances must be Japanese again, or nothing can be re-tested"


def test_each_appearance_gets_its_own_verdict():
    st = WeaveState()
    st.flip(W("大学", slot="0:0"), None)      # missed
    st.flip(W("大学", slot="1:0"), None)      # learned
    st.flip(W("大学", slot="2:0"), None)      # missed again, new appearance
    assert st.scored == {"0:0": "missed", "1:0": "learned",
                         "2:0": "missed"}


def test_unstamped_tokens_are_never_recorded():
    """A token with no slot (a stray Token in a test or an old passage) must
    not create a scored entry keyed on the empty string."""
    st = WeaveState()
    _english, verdict = st.flip(W("大学"), None)
    assert verdict == "missed"          # still evidence for the word itself
    assert st.scored == {}              # but no bogus slot recorded


# ---- the stats side ---------------------------------------------------- #
def _stats():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return StatsRecorder(con)


def test_turning_a_word_back_to_japanese_is_positive_evidence():
    st = _stats()
    st.reading_lookup("大学", "だいがく", "college")
    after_miss = st.get_for("大学", "だいがく")
    assert after_miss["current_streak"] == 0
    st.reading_recall("大学", "だいがく", "college")
    row = st.get_for("大学", "だいがく")
    assert row["matches"] == 1
    assert row["current_streak"] == 1
    assert classify(row) != "unknown", "a recall should move the bucket"


def test_recall_on_a_word_never_seen_before_creates_it():
    st = _stats()
    st.reading_recall("犬", "いぬ", "dog")
    row = st.get_for("犬", "いぬ")
    assert row is not None and row["matches"] == 1 and row["seen"] == 1


def test_drawing_a_page_twice_does_not_spend_the_crutch_twice():
    """Re-rendering is not re-reading.

    A page is rebuilt on every tap, every page turn back to it and every
    change of text size. Spending a crutch turn each time is how a word tapped
    once ended up back in Japanese a few taps later, regardless of how badly
    it was known.
    """
    st = WeaveState()
    tok = W("大学", slot="4:2")
    st.holds[tok.key] = 5
    for _ in range(10):
        st.consume(tok)                  # the same appearance, redrawn
    assert st.holds[tok.key] == 4, "redrawing a page burned the crutch down"


def test_passing_new_appearances_does_spend_it():
    st = WeaveState()
    st.holds[("大学", "")] = 3
    for i in range(3):
        st.consume(W("大学", slot=f"{i}:0"))
    assert ("大学", "") not in st.holds, "the crutch never wore off"


def test_spent_appearances_survive_closing_the_novel(tmp_path):
    from kanjire.data import db
    from kanjire.data.library import Library

    con = db.connect(tmp_path / "stats.db")
    StatsRecorder(con)
    lib = Library(con)
    bid = lib.add("Ch", "大学に行く。")
    state = lib.load_weave(bid)
    state.holds[("大学", "")] = 4
    state.consume(W("大学", slot="0:0"))
    lib.save_position(bid, 0, weave=state)

    reloaded = lib.load_weave(bid)
    assert reloaded.spent == {"0:0"}
    reloaded.consume(W("大学", slot="0:0"))
    assert reloaded.holds[("大学", "")] == 3, \
        "reopening the novel spent an appearance that was already spent"


def test_the_cap_survives_closing_the_novel(tmp_path):
    """Otherwise re-opening a chapter would re-score every appearance in it."""
    from kanjire.data import db
    from kanjire.data.library import Library

    con = db.connect(tmp_path / "stats.db")
    StatsRecorder(con)
    lib = Library(con)
    bid = lib.add("Ch", "大学に行く。")
    state = lib.load_weave(bid)
    state.flip(W("大学", slot="0:0"), None)
    lib.save_position(bid, 0, weave=state)

    reloaded = lib.load_weave(bid)
    assert reloaded.scored == {"0:0": "missed"}
    assert reloaded.flip(W("大学", slot="0:0"), None)[1] == "", \
        "a reopened novel re-scored an appearance it had already counted"
