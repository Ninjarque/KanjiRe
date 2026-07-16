"""The reading room must not claim a sentence is fully known when it contains
kanji the word index dropped (proper nouns, unresolved tokens)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.jputil import uncovered_kanji


def test_uncovered_kanji_flags_dropped_names():
    # The exact screenshot case: only 力 is indexed; the name 竹内 and the verb
    # 回れ are not, so their kanji are uncovered.
    ja = "回れトロイカ （竹内力とバンバンバザール）"
    assert uncovered_kanji(ja, ["力"]) == ["回", "竹", "内"]


def test_a_fully_indexed_sentence_has_no_uncovered_kanji():
    # Every kanji belongs to an indexed word -> nothing uncovered.
    ja = "私は日本語を勉強する"
    heads = ["私", "日本語", "勉強"]
    assert uncovered_kanji(ja, heads) == []


def test_kana_only_sentence_is_never_uncovered():
    assert uncovered_kanji("これはひらがなだけ", []) == []


def test_duplicates_collapse_and_order_is_preserved():
    assert uncovered_kanji("山川山田川", ["田"]) == ["山", "川"]


def test_the_known_check_would_now_report_one_new_not_all_known():
    """Simulate the reading room's unknown count on the screenshot sentence with
    力 known: it must be >=1 (something new), never 0 ('you know every word')."""
    ja = "回れトロイカ （竹内力とバンバンバザール）"
    heads = ["力"]
    known = {"力"}
    from kanjire.jputil import has_kanji

    kanji_words = [h for h in heads if has_kanji(h)]
    unknown = sum(1 for h in kanji_words if h not in known)
    if uncovered_kanji(ja, heads):
        unknown += 1
    assert unknown >= 1, "still mislabels a name-heavy sentence as fully known"
