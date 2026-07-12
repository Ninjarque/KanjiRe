"""Tests for the word sampler: confusability boost, collisions, penalties,
and the append-only review_log recording."""
from __future__ import annotations

import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.data.stats import StatsRecorder
from kanjire.model.sampling import weighted_sample_words
from kanjire.model.vocab import Word


def _w(i, expr, reading, meaning, freq=1.0, jlpt=5):
    return Word(id=i, expression=expr, reading=reading, meaning=meaning,
                jlpt=jlpt, freq=freq, deck="test")


def _family_pool():
    """One high-frequency anchor (食べる), one low-frequency word sharing its
    kanji (食事), and many unrelated low-frequency words."""
    pool = [
        _w(1, "食べる", "たべる", "To eat", freq=5.0),
        _w(2, "食事", "しょくじ", "Meal", freq=1.0),
    ]
    fillers = [
        ("山", "やま", "Mountain"), ("川", "かわ", "River"),
        ("空", "そら", "Sky"), ("海", "うみ", "Sea"),
        ("犬", "いぬ", "Dog"), ("猫", "ねこ", "Cat"),
        ("木", "き", "Tree"), ("花", "はな", "Flower"),
        ("雨", "あめ", "Rain"), ("雪", "ゆき", "Snow"),
        ("火", "ひ", "Fire"), ("水", "みず", "Water"),
        ("石", "いし", "Stone"), ("風", "かぜ", "Wind"),
        ("月", "つき", "Moon"), ("星", "ほし", "Star"),
        ("道", "みち", "Road"), ("町", "まち", "Town"),
    ]
    for j, (e, r, m) in enumerate(fillers):
        pool.append(_w(10 + j, e, r, m, freq=1.0))
    return pool


def test_confusable_boost_pulls_in_shared_kanji():
    """When the anchor is on the board, the kanji-sharing word should co-occur
    far more often than a same-frequency unrelated word."""
    pool = _family_pool()
    rng = random.Random(42)
    together = 0
    baseline = 0  # co-occurrence of an arbitrary unrelated word (山)
    rounds = 300
    for _ in range(rounds):
        picked = weighted_sample_words(pool, 4, bias=0.4, rng=rng)
        exprs = {w.expression for w in picked}
        if "食べる" in exprs:
            together += "食事" in exprs
            baseline += "山" in exprs
    assert together > baseline * 2, (together, baseline)


def test_confusable_off_is_flat():
    pool = _family_pool()
    rng = random.Random(42)
    together = 0
    baseline = 0
    for _ in range(300):
        picked = weighted_sample_words(pool, 4, bias=0.4, rng=rng,
                                       confusable=False)
        exprs = {w.expression for w in picked}
        if "食べる" in exprs:
            together += "食事" in exprs
            baseline += "山" in exprs
    assert together < baseline * 2 + 12, (together, baseline)


def test_no_face_collisions():
    pool = _family_pool() + [
        _w(90, "食う", "たべる", "To eat (rough)"),   # reading + meaning clash
        _w(91, "山地", "さんち", "Mountain"),          # meaning clash with 山
    ]
    rng = random.Random(7)
    for _ in range(50):
        picked = weighted_sample_words(pool, 8, rng=rng)
        exprs = [w.expression for w in picked]
        reads = [w.reading for w in picked]
        means = [w.meaning.strip().lower() for w in picked]
        assert len(set(exprs)) == len(exprs)
        assert len(set(reads)) == len(reads)
        assert len(set(means)) == len(means)


def test_penalized_words_sink():
    pool = _family_pool()
    rng = random.Random(3)
    penal = frozenset({("食べる", "たべる")})
    hits = 0
    for _ in range(200):
        picked = weighted_sample_words(pool, 4, bias=0.0, rng=rng,
                                       penalize=penal)
        hits += any(w.expression == "食べる" for w in picked)
    assert hits <= 6, hits  # ~0.01 weight vs 19 words at weight 1


def test_pair_boost_reunites_confused_words():
    """Words the player historically confused should co-occur far more often
    once one of them is picked."""
    pool = _family_pool()
    rng = random.Random(11)
    # Pretend 山 and 川 were confused before (no kanji share, same freq).
    pairs = {("山", "やま"): {("川", "かわ")},
             ("川", "かわ"): {("山", "やま")}}
    with_boost = 0
    without = 0
    for _ in range(300):
        got = {w.expression for w in weighted_sample_words(
            pool, 4, bias=0.0, rng=rng, pair_boost=pairs)}
        if "山" in got or "川" in got:
            with_boost += ("山" in got and "川" in got)
        got = {w.expression for w in weighted_sample_words(
            pool, 4, bias=0.0, rng=rng)}
        if "山" in got or "川" in got:
            without += ("山" in got and "川" in got)
    assert with_boost > without * 2 + 10, (with_boost, without)


def test_confusion_partners_query():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    rec = StatsRecorder(con)
    a = _w(1, "山", "やま", "Mountain")
    b = _w(2, "川", "かわ", "River")
    rec.confused(a, b, "meaning")
    partners = rec.confusion_partners()
    assert partners[("山", "やま")] == {("川", "かわ")}
    assert partners[("川", "かわ")] == {("山", "やま")}
    # self-confusions (typed recall failures) are excluded
    rec.confused(a, a, "reading")
    partners = rec.confusion_partners()
    assert ("山", "やま") not in partners[("山", "やま")] \
        if ("山", "やま") in partners else True


def test_review_log_records_and_resets():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    rec = StatsRecorder(con)
    a = _w(1, "食べる", "たべる", "To eat")
    b = _w(2, "山", "やま", "Mountain")

    rec.saw(a)                      # saw is NOT logged
    rec.matched(a)                  # one 'match' row, rating 3
    rec.confused(a, b, "reading")   # two 'confuse' rows, rating 1

    rows = [dict(r) for r in con.execute(
        "SELECT * FROM review_log ORDER BY id")]
    assert len(rows) == 3
    assert rows[0]["event"] == "match" and rows[0]["rating"] == 3
    assert rows[0]["expression"] == "食べる"
    assert {r["expression"] for r in rows[1:]} == {"食べる", "山"}
    assert all(r["rating"] == 1 and r["face"] == "reading" for r in rows[1:])
    assert all(len(r["day"]) == 10 for r in rows)

    counts = rec.day_counts()
    assert sum(counts.values()) == 3
    assert rec.reviews_today() == 3

    rec.reset_word("山", "やま")
    assert sum(rec.day_counts().values()) == 2
    rec.reset_all()
    assert rec.day_counts() == {}


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
