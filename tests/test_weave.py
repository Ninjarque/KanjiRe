"""The diglot-weave reader: tokenising, the crutch rule, and the library."""
from __future__ import annotations

import pytest

from kanjire.data import db
from kanjire.data.library import Library
from kanjire.data.stats import StatsRecorder
from kanjire.data.weave import (Lexicon, Token, WeaveState, hold_for,
                                split_sentences, starts_in_english)


# --------------------------------------------------------------------------- #
# Sentence splitting
# --------------------------------------------------------------------------- #
def test_splits_on_japanese_and_ascii_enders():
    got = split_sentences("私は本を読む。今日は暑い！なぜ？")
    assert got == ["私は本を読む。", "今日は暑い！", "なぜ？"]


def test_newlines_split_prose_without_punctuation():
    # Pasted prose often has no 。 at all — line breaks are all we get.
    assert split_sentences("一行目\n二行目\n") == ["一行目", "二行目"]


def test_a_trailing_fragment_is_kept():
    assert split_sentences("終わりのない文") == ["終わりのない文"]


def test_blank_input_yields_nothing():
    for junk in ("", "   ", "\n\n", None):
        assert split_sentences(junk) == []


# --------------------------------------------------------------------------- #
# Tokenising (no MeCab: Android has none)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def lex():
    con = db.connect(read_only=True)
    try:
        yield Lexicon(con)
    finally:
        con.close()


def test_tokenizer_finds_real_words(lex):
    toks = lex.tokenize("私は大学の本を読みます。")
    words = [t.expression for t in toks if t.known_word]
    assert "大学" in words, words
    assert "本" in words, words


def test_longest_match_wins(lex):
    """大学 must not be read as 大 + 学 — both are entries too."""
    assert lex.match_at("大", 0) is not None      # the trap really exists
    toks = lex.tokenize("大学")
    assert [t.expression for t in toks if t.known_word] == ["大学"]


def test_the_passage_survives_tokenising(lex):
    text = "私は本を読む。今日は暑い！"
    assert "".join(t.text for t in lex.tokenize(text)) == text


def test_bare_single_kana_are_not_words(lex):
    """と/に/の are particles here; matching them would make every sentence
    a wall of tappable noise."""
    toks = lex.tokenize("本を読むとき")
    for t in toks:
        if t.known_word:
            assert len(t.expression) > 1 or any(
                ord(c) > 0x4DFF for c in t.expression), t.expression


def test_matched_tokens_carry_a_gloss(lex):
    toks = [t for t in lex.tokenize("日本語の本") if t.known_word]
    assert toks and all(t.meaning for t in toks)


def test_empty_text_tokenizes_to_nothing(lex):
    assert lex.tokenize("") == []


# --------------------------------------------------------------------------- #
# The crutch rule
# --------------------------------------------------------------------------- #
KNOWN = {"seen": 9, "matches": 9, "current_streak": 5,
         "mistakes_kanji": 0, "mistakes_reading": 0, "mistakes_meaning": 0}
SHAKY = {"seen": 6, "matches": 2, "current_streak": 0,
         "mistakes_kanji": 4, "mistakes_reading": 0, "mistakes_meaning": 0}


def test_an_unknown_word_holds_longer_than_a_known_one():
    assert hold_for(None, 5) > hold_for(KNOWN, 5)


def test_a_harder_word_holds_longer():
    assert hold_for(None, 1) > hold_for(None, 5)


def test_re_tapping_extends_the_hold():
    assert hold_for(SHAKY, 3, taps=2) > hold_for(SHAKY, 3, taps=0)


def test_the_hold_is_bounded():
    from kanjire.data.weave import MAX_HOLD
    assert 1 <= hold_for(None, 1, taps=99) <= MAX_HOLD


def test_hard_and_unknown_words_start_in_english():
    assert starts_in_english(None, 1) is True
    assert starts_in_english(None, 2) is True
    # Easy-but-unknown stays Japanese: that is the word you are here to meet.
    assert starts_in_english(None, 5) is False
    # Known-but-hard needs no crutch either.
    assert starts_in_english(KNOWN, 1) is False


# --------------------------------------------------------------------------- #
# WeaveState: tap, decay, flip back
# --------------------------------------------------------------------------- #
def _tok(expr="大学", jlpt=5):
    return Token(expr, expr, "だいがく", "College; university", jlpt)


def test_a_tap_switches_later_appearances_to_english():
    st, tok = WeaveState(), _tok()
    assert st.show_english(tok, KNOWN) is False
    st.tapped(tok, KNOWN)
    assert st.show_english(tok, KNOWN) is True


def test_the_crutch_wears_off_after_enough_appearances():
    st, tok = WeaveState(), _tok()
    hold = st.tapped(tok, KNOWN)
    for _ in range(hold):
        assert st.show_english(tok, KNOWN) is True
        st.consume(tok)
    assert st.show_english(tok, KNOWN) is False, \
        "the word never returned to Japanese"


def test_a_word_you_keep_tapping_stays_english_longer():
    st, tok = WeaveState(), _tok()
    first = st.tapped(tok, SHAKY)
    for _ in range(first):
        st.consume(tok)
    second = st.tapped(tok, SHAKY)
    assert second > first


def test_unmatched_text_is_never_swapped():
    st = WeaveState()
    assert st.show_english(Token("を"), None) is False


def test_consuming_an_untapped_word_is_harmless():
    st = WeaveState()
    st.consume(_tok())          # must not raise or create a hold
    assert st.holds == {}


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #
@pytest.fixture
def lib(tmp_path):
    con = db.connect(tmp_path / "stats.db")
    StatsRecorder(con)          # the real DB always has both
    return Library(con)


def test_adding_a_passage_splits_and_counts_it(lib):
    book_id = lib.add("Chapter 1", "私は本を読む。今日は暑い。")
    book = lib.get(book_id)
    assert book["title"] == "Chapter 1"
    assert book["n_sentences"] == 2
    assert book["position"] == 0 and book["ratio"] == 0.0


def test_an_empty_passage_is_refused(lib):
    assert lib.add("Nothing", "   \n ") is None
    assert lib.books() == []


def test_progress_is_position_over_length(lib):
    book_id = lib.add("Novel", "一。二。三。四。")
    lib.save_position(book_id, 2)
    book = lib.get(book_id)
    assert book["position"] == 2
    assert lib.books()[0]["ratio"] == pytest.approx(0.5)
    assert lib.books()[0]["done"] is False
    lib.save_position(book_id, 4)
    assert lib.books()[0]["done"] is True


def test_reading_resumes_from_the_saved_position(lib):
    book_id = lib.add("Novel", "一。二。三。四。五。")
    lib.save_position(book_id, 3)
    assert lib.sentences(book_id, lib.get(book_id)["position"])[0] == "四。"


def test_sentences_are_windowed(lib):
    book_id = lib.add("Long", "".join(f"文{i}。" for i in range(100)))
    assert len(lib.sentences(book_id, 0, limit=10)) == 10


def test_the_crutch_state_survives_a_reload(lib):
    book_id = lib.add("Novel", "一。二。")
    st = WeaveState()
    st.tapped(_tok(), None)
    lib.save_position(book_id, 1, weave=st)
    back = lib.load_weave(book_id)
    assert back.holds == st.holds and back.taps == st.taps


def test_a_corrupt_crutch_blob_resets_instead_of_crashing(lib):
    book_id = lib.add("Novel", "一。")
    lib.con.execute("UPDATE library SET weave='{not json' WHERE id=?",
                    (book_id,))
    state = lib.load_weave(book_id)
    assert state.holds == {} and state.taps == {}


def test_deleting_a_book_removes_its_sentences(lib):
    book_id = lib.add("Gone", "一。二。")
    assert lib.delete(book_id) is True
    assert lib.get(book_id) is None
    assert lib.sentences(book_id) == []
    assert lib.delete(book_id) is False


def test_renaming(lib):
    book_id = lib.add("Old", "一。")
    lib.rename(book_id, "New")
    assert lib.get(book_id)["title"] == "New"
    lib.rename(book_id, "   ")          # blank is ignored, not destructive
    assert lib.get(book_id)["title"] == "New"


def test_most_recently_read_sorts_first(lib):
    a = lib.add("A", "一。")
    b = lib.add("B", "二。")
    lib.save_position(a, 1)
    assert lib.books()[0]["id"] == a
    lib.save_position(b, 1)
    assert lib.books()[0]["id"] == b


# --------------------------------------------------------------------------- #
# Pasting (the bug: a pasted chapter arrived as an empty box)
# --------------------------------------------------------------------------- #
def test_windows_line_endings_are_normalised():
    """Kivy's TextInput.paste() yields NOTHING for \r\n content — which is
    what a browser or editor puts on the clipboard. Normalising is the fix."""
    from kanjire.data.weave import normalize_text
    got = normalize_text("私は本を読む。\r\n今日は暑い。\r")
    assert "\r" not in got
    assert got.count("\n") == 2


def test_sentences_survive_crlf_paste():
    assert split_sentences("一。\r\n二。\r\n三。") == ["一。", "二。", "三。"]


def test_zero_width_and_nbsp_junk_is_dropped():
    from kanjire.data.weave import normalize_text
    got = normalize_text("私\u200bは\u00a0本\ufeffを読む。")
    assert "\u200b" not in got and "\ufeff" not in got
    assert "私は 本を読む。" == got


def test_full_width_space_becomes_a_normal_one():
    from kanjire.data.weave import normalize_text
    assert normalize_text("今日は　暑い。") == "今日は 暑い。"


def test_control_characters_are_stripped_but_newlines_kept():
    from kanjire.data.weave import normalize_text
    got = normalize_text("一\x00二\x07三\n四\t五")
    assert got == "一二三\n四\t五"


def test_describe_counts_what_the_user_pasted():
    from kanjire.data.weave import describe
    chars, sentences = describe("一。\r\n二。")
    assert sentences == 2
    assert chars == len("一。\n二。")


def test_describe_of_nothing_is_zero():
    from kanjire.data.weave import describe
    assert describe("") == (0, 0)
    assert describe(None) == (0, 0)


# --------------------------------------------------------------------------- #
# Coverage on real prose (the "will it work on an actual novel" question)
# --------------------------------------------------------------------------- #
def test_conjugated_words_are_recognised(lex):
    """Inflected forms are most of what a real page is made of."""
    got = {t.text: t.expression for t in
           lex.tokenize("子供が公園で遊んでいる。新聞を読みました。行きます。")
           if t.known_word}
    assert got.get("遊んでいる") == "遊ぶ"
    assert got.get("読みました") == "読む"
    assert got.get("行きます") == "行く"


def test_a_kanji_no_word_covers_is_still_tappable(lex):
    """A novel is full of vocabulary the deck lacks. 'The word I need is the
    one I can't tap' is the worst failure a reading aid can have."""
    toks = {t.text: t for t in lex.tokenize("悲嘆")}
    assert set(toks) == {"悲", "嘆"}, "an unknown compound went dead"
    for t in toks.values():
        assert t.known_word and t.meaning, f"{t.text} has no gloss"


def test_particles_are_still_not_tappable(lex):
    """The fallback must not turn every kana into a tappable word."""
    for t in lex.tokenize("本を読む"):
        if t.text in ("を", "は", "の", "に"):
            assert not t.known_word


def test_most_kanji_in_real_sentences_are_tappable(lex):
    """Measured on the bundled corpus, not asserted from taste.

    Counting *tokens* is meaningless here — particles and 。 are tokens and
    are correctly untappable. What matters is how much of the kanji text a
    reader can actually ask about.
    """
    import sqlite3

    from kanjire.jputil import has_kanji
    from kanjire.paths import DATA_DIR

    path = DATA_DIR / "sentences.db"
    if not path.exists():
        pytest.skip("sentence corpus not built")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = [r["ja"] for r in con.execute("SELECT ja FROM sentences LIMIT 300")]
    con.close()

    total = hit = 0
    for sentence in rows:
        for t in lex.tokenize(sentence):
            n = sum(1 for c in t.text if has_kanji(c))
            total += n
            if t.known_word:
                hit += n
    assert total > 500, "not enough sample text to judge"
    ratio = hit / total
    assert ratio > 0.95, f"only {ratio:.1%} of kanji text is tappable"


def test_an_old_library_table_is_migrated_not_broken(tmp_path):
    """A DB from before sync existed has no key/deleted_at columns.

    CREATE TABLE IF NOT EXISTS does not add them, so every read failed with
    "no such column" — which would have hit every existing user on update.
    """
    con = db.connect(tmp_path / "stats.db")
    StatsRecorder(con)
    # Recreate the pre-sync shape by hand.
    con.executescript("""
        DROP TABLE IF EXISTS library;
        CREATE TABLE library (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, added_at TEXT,
            last_read_at TEXT, chars INTEGER DEFAULT 0,
            n_sentences INTEGER DEFAULT 0, position INTEGER DEFAULT 0,
            weave TEXT);
        CREATE TABLE IF NOT EXISTS library_sentences (
            book_id INTEGER NOT NULL, idx INTEGER NOT NULL, ja TEXT NOT NULL,
            PRIMARY KEY (book_id, idx));
    """)
    con.execute("INSERT INTO library (title, n_sentences, position) "
                "VALUES ('Old', 2, 1)")
    con.execute("INSERT INTO library_sentences (book_id, idx, ja) "
                "VALUES (1, 0, '一。'), (1, 1, '二。')")
    con.commit()

    lib = Library(con)                  # must migrate, not explode
    books = lib.books()
    assert [b["title"] for b in books] == ["Old"]
    assert books[0]["position"] == 1, "existing progress was lost"
    # And it gained an identity, so it can join sync.
    assert lib.get(books[0]["id"])["key"], "no key backfilled"
    assert lib.export()[0]["sentences"] == ["一。", "二。"]
