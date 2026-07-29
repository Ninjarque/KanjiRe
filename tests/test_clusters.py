"""The genre taxonomy and the clustering sidecar reader.

The sidecar itself is built offline, so these tests split in two: the
taxonomy rules (always checked) and the data (checked only when clusters.db
is actually present, so a fresh checkout still passes).
"""
from __future__ import annotations

import sqlite3

import pytest

from kanjire.data import clusters
from kanjire.data.genres import BY_KEY, GENRES, KEYS, valid_genres
from kanjire.i18n import STRINGS


def test_genre_keys_are_unique():
    keys = [g.key for g in GENRES]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(KEYS) == set(BY_KEY)


def test_every_genre_icon_is_one_japanese_character():
    # Icons are drawn with the bundled JP fonts — a multi-char or emoji icon
    # would render as tofu on Linux and Android.
    for g in GENRES:
        assert len(g.icon) == 1, g.key
        assert ord(g.icon) > 0x2E80, f"{g.key}: {g.icon} is not a CJK glyph"


def test_every_genre_is_translated_in_every_locale():
    for locale, table in STRINGS.items():
        missing = [g.key for g in GENRES if not table.get(g.tr)]
        assert not missing, f"{locale} is missing: {missing}"


def test_valid_genres_drops_unknowns_and_keeps_taxonomy_order():
    # A newer peer (or an older save) may name a genre this build lacks;
    # that must narrow the game, never raise.
    assert valid_genres(["food", "not_a_genre", "animals"]) == ("food", "animals")
    assert valid_genres(["animals", "food"]) == ("food", "animals")
    assert valid_genres(None) == ()
    assert valid_genres(["nonsense"]) == ()
    assert valid_genres([1, None, "food"]) == ("food",)


def test_reader_degrades_when_the_sidecar_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(clusters, "CLUSTERS_PATH", tmp_path / "nope.db")
    clusters.close()
    try:
        assert clusters.available() is False
        assert clusters.genre_index() == {}
        assert clusters.genres_of("食べ物", "たべもの") == ()
        assert clusters.genre_counts() == {}
        assert clusters.genre_members("food") == set()
        assert clusters.members_of(["food", "animals"]) == set()
        assert clusters.shape_map() == {}
        assert clusters.sound_map() == {}
    finally:
        clusters.close()


def test_unknown_genre_key_is_never_returned(tmp_path, monkeypatch):
    """A sidecar built by a newer version must not smuggle in unknown keys."""
    path = tmp_path / "clusters.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE word_genre (expression TEXT, reading TEXT, "
        "genre TEXT, rank INTEGER, score REAL);"
    )
    con.executemany(
        "INSERT INTO word_genre VALUES (?,?,?,?,?)",
        [("犬", "いぬ", "animals", 0, 1.0),
         ("犬", "いぬ", "from_the_future", 1, 0.9)],
    )
    con.commit()
    con.close()

    monkeypatch.setattr(clusters, "CLUSTERS_PATH", path)
    clusters.close()
    try:
        assert clusters.genres_of("犬", "いぬ") == ("animals",)
        assert clusters.genre_members("from_the_future") == set()
    finally:
        clusters.close()


# --------------------------------------------------------------------------- #
# Data checks — skipped on a checkout that hasn't built the sidecar yet.
# --------------------------------------------------------------------------- #
needs_data = pytest.mark.skipif(not clusters.CLUSTERS_PATH.exists(),
                                reason="clusters.db not built")


@needs_data
def test_shipped_sidecar_covers_the_common_words():
    clusters.close()
    index = clusters.genre_index()
    assert len(index) > 5000
    counts = clusters.genre_counts()
    # Every genre must fill the largest board (menuconfig.SIZES tops out at
    # 24 words), or the browser would offer a topic that can't be played.
    # Counted on primary genres only, so this is the conservative floor.
    thin = {k: counts.get(k, 0) for k in KEYS if counts.get(k, 0) < 24}
    assert not thin, f"genres too thin to fill a board: {thin}"


@needs_data
def test_lookalikes_and_soundalikes_are_populated():
    clusters.close()
    shape = clusters.shape_map()
    # The classic confusions the bitmap pass exists to catch.
    assert "士" in shape.get("土", set())
    assert "末" in shape.get("未", set())
    assert "体" in shape.get("休", set())
    assert all(k not in v for k, v in shape.items()), "a kanji looks like itself"

    sound = clusters.sound_map()
    assert sound
    assert all(key not in partners for key, partners in sound.items())
