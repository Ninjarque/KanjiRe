"""Integration tests against the real, built database.

These are skipped automatically if the database has not been built yet.
Run under pytest *or* as a plain script.
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.data import db
from kanjire.game.config import GameConfig
from kanjire.game.engine import GameEngine
from kanjire.jputil import has_kanji
from kanjire.paths import DB_PATH

_HAVE_DB = DB_PATH.exists()


def _skip_if_no_db() -> bool:
    if not _HAVE_DB:
        print("SKIP (database not built; run scripts/build_jlpt_dataset.py)")
        return True
    return False


def test_decks_present():
    if _skip_if_no_db():
        return
    con = db.connect(read_only=True)
    try:
        names = {r["name"] for r in db.list_decks(con)}
        assert "jlpt" in names, f"jlpt deck missing; have {names}"
    finally:
        con.close()


def test_jlpt_words_loadable():
    if _skip_if_no_db():
        return
    con = db.connect(read_only=True)
    try:
        n5 = db.load_words(con, decks=["jlpt"], levels=[5], require_kanji=True)
        assert len(n5) > 50
        # every loaded word is well-formed and actually contains kanji
        for w in n5[:200]:
            assert w.expression and w.reading and w.meaning
            assert has_kanji(w.expression)
            assert w.jlpt == 5
    finally:
        con.close()


def test_full_round_solvable_two_faces():
    if _skip_if_no_db():
        return
    con = db.connect(read_only=True)
    try:
        words = db.load_words(con, decks=["jlpt"], levels=[5], require_kanji=True)
    finally:
        con.close()
    cfg = GameConfig(
        decks=("jlpt",), levels=(5,), faces=("kanji", "meaning"),
        words_per_round=4, duration=None, max_mistakes=None,
    )
    eng = GameEngine(cfg, words, rng=random.Random(0))
    eng.start()
    assert len(eng.board_cards) == 4 * 2
    for g in range(len(eng.round_words)):
        for cid in list(eng.group_cards[g]):
            eng.select(cid)
    assert eng.rounds_completed == 1
    assert eng.score > 0


def test_corpus_deck_if_present():
    if _skip_if_no_db():
        return
    con = db.connect(read_only=True)
    try:
        corpus = [r["name"] for r in db.list_decks(con) if r["name"].startswith("corpus:")]
        if not corpus:
            print("note: no corpus deck (run scripts/fetch_sample_corpus.py) - ok")
            return
        words = db.load_words(con, decks=[corpus[0]], require_kanji=True)
        assert len(words) > 10
        assert all(has_kanji(w.expression) for w in words[:100])
    finally:
        con.close()


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
