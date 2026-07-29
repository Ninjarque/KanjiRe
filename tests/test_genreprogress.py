"""The Genres browser's progress model.

Toolkit-free, so both the pyglet scene and the Kivy screen are covered by
the same numbers they render.
"""
from __future__ import annotations

from kanjire.data.genres import GENRES
from kanjire.game import genreprogress as gp
from kanjire.model.vocab import Word


def W(i, expr, reading, jlpt=5):
    return Word(id=i, expression=expr, reading=reading, meaning="x",
                jlpt=jlpt, freq=3.0, deck="jlpt")


class _Index(dict):
    """Stand-in for clusters.genre_index()."""


def _patch_index(monkeypatch, mapping):
    from kanjire.data import clusters
    monkeypatch.setattr(clusters, "genre_index", lambda: mapping)


def test_every_genre_is_present_even_with_no_words(monkeypatch):
    _patch_index(monkeypatch, {})
    out = gp.build([], {})
    assert set(out) == {g.key for g in GENRES}
    for rows in out.values():
        assert rows[None].total == 0
        assert not rows[None].playable      # the browser dims it
        assert rows[None].ratio == 0.0
        # Every level slot exists, so the level view never KeyErrors.
        for lvl in gp.LEVELS:
            assert rows[lvl].total == 0


def test_words_land_in_their_genre_and_level(monkeypatch):
    pool = [W(1, "食事", "しょくじ", 5), W(2, "朝食", "ちょうしょく", 4),
            W(3, "電車", "でんしゃ", 5)]
    _patch_index(monkeypatch, {
        ("食事", "しょくじ"): ("food",),
        ("朝食", "ちょうしょく"): ("food",),
        ("電車", "でんしゃ"): ("transport",),
    })
    out = gp.build(pool, {})
    assert out["food"][5].total == 1
    assert out["food"][4].total == 1
    assert out["food"][None].total == 2
    assert out["transport"][5].total == 1
    assert out["transport"][None].total == 1


def test_a_word_counts_in_both_of_its_genres(monkeypatch):
    pool = [W(1, "牛乳", "ぎゅうにゅう", 5)]
    _patch_index(monkeypatch, {("牛乳", "ぎゅうにゅう"): ("food", "animals")})
    out = gp.build(pool, {})
    assert out["food"][None].total == 1
    assert out["animals"][None].total == 1


def test_known_counts_use_the_shared_classifier(monkeypatch):
    pool = [W(1, "食事", "しょくじ"), W(2, "朝食", "ちょうしょく")]
    _patch_index(monkeypatch, {("食事", "しょくじ"): ("food",),
                               ("朝食", "ちょうしょく"): ("food",)})
    # A row solid enough that classify() calls it "known".
    known_row = {"seen": 12, "matches": 12, "current_streak": 6,
                 "mistakes_kanji": 0, "mistakes_reading": 0,
                 "mistakes_meaning": 0}
    out = gp.build(pool, {("食事", "しょくじ"): known_row})
    cell = out["food"][None]
    assert cell.known == 1
    assert cell.total == 2
    assert 0.0 < cell.ratio < 1.0
    assert not cell.complete


def test_a_genre_is_complete_when_every_word_is_known(monkeypatch):
    pool = [W(1, "食事", "しょくじ")]
    _patch_index(monkeypatch, {("食事", "しょくじ"): ("food",)})
    known_row = {"seen": 12, "matches": 12, "current_streak": 6,
                 "mistakes_kanji": 0, "mistakes_reading": 0,
                 "mistakes_meaning": 0}
    out = gp.build(pool, {("食事", "しょくじ"): known_row})
    assert out["food"][None].complete


def test_playable_needs_enough_words_for_a_board(monkeypatch):
    pool = [W(i, f"語{i}", f"ご{i}") for i in range(4)]
    _patch_index(monkeypatch, {(f"語{i}", f"ご{i}"): ("food",)
                               for i in range(4)})
    out = gp.build(pool, {})
    assert out["food"][None].playable
    assert not out["food"][4].playable          # nothing at N4


def test_an_unknown_genre_key_is_ignored(monkeypatch):
    """A sidecar from a newer build must not crash an older browser."""
    pool = [W(1, "謎", "なぞ")]
    _patch_index(monkeypatch, {("謎", "なぞ"): ("from_the_future",)})
    out = gp.build(pool, {})
    assert all(rows[None].total == 0 for rows in out.values())


def test_words_outside_the_jlpt_levels_are_skipped(monkeypatch):
    """Corpus words carry jlpt=None; they must not land in a level bucket."""
    pool = [W(1, "難語", "なんご", None)]
    _patch_index(monkeypatch, {("難語", "なんご"): ("food",)})
    out = gp.build(pool, {})
    assert out["food"][None].total == 0


def test_totals_summarise_the_grid(monkeypatch):
    pool = [W(1, "食事", "しょくじ"), W(2, "電車", "でんしゃ")]
    _patch_index(monkeypatch, {("食事", "しょくじ"): ("food",),
                               ("電車", "でんしゃ"): ("transport",)})
    known_row = {"seen": 12, "matches": 12, "current_streak": 6,
                 "mistakes_kanji": 0, "mistakes_reading": 0,
                 "mistakes_meaning": 0}
    out = gp.build(pool, {("食事", "しょくじ"): known_row})
    started, complete, known = gp.totals(out)
    assert (started, complete, known) == (1, 1, 1)
