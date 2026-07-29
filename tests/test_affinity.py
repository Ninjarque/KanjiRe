"""Genre filtering and the clustering affinity dials.

Covers the sampler contract the menu depends on: dials pull a board toward
related words without ever being able to starve it, and the tighter meaning
check keeps clustered boards solvable.
"""
from __future__ import annotations

import random

import pytest

from kanjire.game.config import GameConfig
from kanjire.model.sampling import (AFFINITY_STEPS, Affinity, NO_AFFINITY,
                                    affinity_for, filter_by_genres,
                                    weighted_sample_words)
from kanjire.model.vocab import Word


def W(i, expr, reading, meaning, jlpt=5, freq=3.0):
    return Word(id=i, expression=expr, reading=reading, meaning=meaning,
                jlpt=jlpt, freq=freq, deck="test")


@pytest.fixture
def pool():
    return [
        W(1, "食事", "しょくじ", "Meal"),
        W(2, "朝食", "ちょうしょく", "Breakfast"),
        W(3, "夕食", "ゆうしょく", "Dinner"),
        W(4, "電車", "でんしゃ", "Train"),
        W(5, "自転車", "じてんしゃ", "Bicycle"),
        W(6, "政治", "せいじ", "Politics"),
        W(7, "経済", "けいざい", "Economy"),
        W(8, "音楽", "おんがく", "Music"),
    ]


# --------------------------------------------------------------------------- #
# Config validation
# --------------------------------------------------------------------------- #
def test_config_clamps_dials_and_drops_unknown_genres():
    cfg = GameConfig(aff_meaning=9, aff_looks=-2, aff_sound=2,
                     genres=("food", "nonsense"))
    assert (cfg.aff_meaning, cfg.aff_looks, cfg.aff_sound) == (3, 0, 2)
    assert cfg.genres == ("food",)


def test_config_defaults_leave_clustering_off():
    cfg = GameConfig()
    assert cfg.genres == ()
    assert affinity_for(cfg) is NO_AFFINITY


# --------------------------------------------------------------------------- #
# Genre filtering
# --------------------------------------------------------------------------- #
def test_no_genres_selected_returns_the_pool_untouched(pool):
    assert filter_by_genres(pool, ()) == pool


def test_a_genre_with_no_data_never_empties_the_pool(pool, monkeypatch):
    """An imported corpus has no precomputed genres — it must still play."""
    from kanjire.data import clusters
    monkeypatch.setattr(clusters, "members_of", lambda genres: set())
    assert filter_by_genres(pool, ("food",)) == pool


def test_a_genre_that_matches_nothing_in_this_pool_falls_back(pool, monkeypatch):
    from kanjire.data import clusters
    monkeypatch.setattr(clusters, "members_of",
                        lambda genres: {("無関係", "むかんけい")})
    # Rather than hand back an empty board, keep every word.
    assert filter_by_genres(pool, ("food",)) == pool


def test_genre_filter_keeps_only_members(pool, monkeypatch):
    from kanjire.data import clusters
    monkeypatch.setattr(clusters, "members_of", lambda genres: {
        ("食事", "しょくじ"), ("朝食", "ちょうしょく")})
    kept = filter_by_genres(pool, ("food",))
    assert [w.expression for w in kept] == ["食事", "朝食"]


# --------------------------------------------------------------------------- #
# The dials
# --------------------------------------------------------------------------- #
def test_an_affinity_without_its_map_is_inert():
    assert not Affinity(meaning=3).active           # dial on, no data
    assert not Affinity(genre_map={("a", "b"): ("food",)}).active   # data, no dial
    assert Affinity(genre_map={("a", "b"): ("food",)}, meaning=1).active


def test_meaning_dial_pulls_a_board_toward_one_genre(pool):
    genre_map = {
        ("食事", "しょくじ"): ("food",),
        ("朝食", "ちょうしょく"): ("food",),
        ("夕食", "ゆうしょく"): ("food",),
        ("電車", "でんしゃ"): ("transport",),
        ("自転車", "じてんしゃ"): ("transport",),
        ("政治", "せいじ"): ("society",),
        ("経済", "けいざい"): ("society",),
        ("音楽", "おんがく"): ("arts",),
    }
    aff = Affinity(genre_map=genre_map, meaning=3)
    rng = random.Random(4)
    # Across many boards, a themed draw should be far more genre-coherent
    # than an unthemed one.
    def coherence(affinity):
        total = 0
        for _ in range(60):
            picked = weighted_sample_words(pool, 3, bias=0.0, rng=rng,
                                           confusable=False, affinity=affinity)
            genres = [genre_map[(w.expression, w.reading)][0] for w in picked]
            total += max(genres.count(g) for g in set(genres))
        return total / 60

    assert coherence(aff) > coherence(None) + 0.4


def test_looks_dial_pulls_in_lookalike_kanji():
    pool = [
        W(1, "待つ", "まつ", "To wait"),
        W(2, "持つ", "もつ", "To hold"),
        W(3, "時間", "じかん", "Time"),
        W(4, "音楽", "おんがく", "Music"),
        W(5, "牛乳", "ぎゅうにゅう", "Milk"),
        W(6, "旅行", "りょこう", "Trip"),
    ]
    aff = Affinity(shape_map={"待": {"持", "時"}, "持": {"待", "時"},
                              "時": {"待", "持"}}, looks=3)
    rng = random.Random(11)
    hits = 0
    for _ in range(60):
        picked = weighted_sample_words(pool, 2, bias=0.0, rng=rng,
                                       confusable=False, affinity=aff)
        exprs = {w.expression for w in picked}
        if len(exprs & {"待つ", "持つ", "時間"}) == 2:
            hits += 1
    assert hits > 30, f"lookalikes co-occurred only {hits}/60 boards"


def test_sound_dial_pulls_in_soundalike_readings():
    pool = [
        W(1, "病院", "びょういん", "Hospital"),
        W(2, "入院", "にゅういん", "Hospitalisation"),
        W(3, "音楽", "おんがく", "Music"),
        W(4, "旅行", "りょこう", "Trip"),
        W(5, "野菜", "やさい", "Vegetable"),
    ]
    aff = Affinity(sound_map={("病院", "びょういん"): {("入院", "にゅういん")},
                              ("入院", "にゅういん"): {("病院", "びょういん")}},
                   sound=3)
    rng = random.Random(5)
    hits = 0
    for _ in range(60):
        picked = weighted_sample_words(pool, 2, bias=0.0, rng=rng,
                                       confusable=False, affinity=aff)
        if {w.expression for w in picked} == {"病院", "入院"}:
            hits += 1
    assert hits > 15, f"soundalikes co-occurred only {hits}/60 boards"


def test_a_maxed_dial_still_fills_the_board(pool):
    """Dials are boosts, not filters: an impossible theme must not starve."""
    aff = Affinity(genre_map={("食事", "しょくじ"): ("food",)}, meaning=3)
    picked = weighted_sample_words(pool, 6, rng=random.Random(1), affinity=aff)
    assert len(picked) == 6


def test_affinity_steps_are_monotonic():
    assert AFFINITY_STEPS[0] == 1.0
    assert list(AFFINITY_STEPS) == sorted(AFFINITY_STEPS)


# --------------------------------------------------------------------------- #
# Solvability under clustering
# --------------------------------------------------------------------------- #
def test_words_sharing_one_sense_are_never_dealt_together():
    # "To shut" is a sense of both — a board with both has an unanswerable
    # meaning card. Clustering makes such pairs much likelier to meet.
    pool = [
        W(1, "閉める", "しめる", "To close, to shut"),
        W(2, "閉まる", "しまる", "To shut, to be closed"),
        W(3, "開ける", "あける", "To open"),
    ]
    for seed in range(30):
        picked = weighted_sample_words(pool, 2, rng=random.Random(seed))
        exprs = {w.expression for w in picked}
        assert exprs != {"閉める", "閉まる"}, f"seed {seed} dealt both"


def test_distinct_senses_still_coexist():
    pool = [
        W(1, "開ける", "あける", "To open"),
        W(2, "閉める", "しめる", "To close, to shut"),
    ]
    picked = weighted_sample_words(pool, 2, rng=random.Random(0))
    assert len(picked) == 2
