"""Read-only access to the clustering sidecar (``clusters.db``).

Three precomputed relations, all built offline by
``scripts/build_clusters.py`` and shipped inside the bundle:

* ``genres_of`` / ``genre_members`` — the named topic buckets,
* ``shape_map`` — kanji that look confusingly alike,
* ``sound_map`` — words whose readings are near-misses.

Like the other sidecars this degrades to empty everywhere when the file is
absent (dev checkout, stripped build): a missing clusters.db must cost the
player the genre features, never the game.

Imported decks are a real case, not an edge case: a corpus the player mined
themselves has no precomputed rows. Genres simply won't resolve for those
words, so callers must treat "no genre" as normal and never as an error.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from kanjire.data.genres import KEYS
from kanjire.paths import DATA_DIR

CLUSTERS_PATH = DATA_DIR / "clusters.db"

_con: sqlite3.Connection | None = None
_tried = False


def _open(path: Path) -> sqlite3.Connection | None:
    try:
        if not path.exists():
            return None
        # check_same_thread=False mirrors kanjidata: the game-launch worker
        # thread reads this while the UI thread animates the spinner.
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                              check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _cx() -> sqlite3.Connection | None:
    global _con, _tried
    if not _tried:
        _con = _open(CLUSTERS_PATH)
        _tried = True
    return _con


def available() -> bool:
    """True when genre features can be offered at all."""
    return _cx() is not None


# --------------------------------------------------------------------------- #
# Genres
# --------------------------------------------------------------------------- #
_genre_cache: dict[tuple[str, str], tuple[str, ...]] | None = None


def genre_index() -> dict[tuple[str, str], tuple[str, ...]]:
    """(expression, reading) -> the genres it belongs to, primary first.

    Loaded whole and cached: the table is ~12k rows, and the sampler would
    otherwise query it once per candidate word on every board.
    """
    global _genre_cache
    if _genre_cache is not None:
        return _genre_cache
    out: dict[tuple[str, str], list[str]] = {}
    con = _cx()
    if con is not None:
        try:
            for row in con.execute(
                "SELECT expression, reading, genre FROM word_genre "
                "ORDER BY rank"
            ):
                if row["genre"] in KEYS:
                    out.setdefault((row["expression"], row["reading"]),
                                   []).append(row["genre"])
        except sqlite3.Error:
            out = {}
    _genre_cache = {k: tuple(v) for k, v in out.items()}
    return _genre_cache


def genres_of(expression: str, reading: str) -> tuple[str, ...]:
    """The genres of one word (empty when unknown — e.g. an imported deck)."""
    return genre_index().get((expression, reading), ())


def genre_counts() -> dict[str, int]:
    """genre key -> how many words carry it as their primary genre."""
    con = _cx()
    if con is None:
        return {}
    try:
        return {r["genre"]: r["n"] for r in con.execute(
            "SELECT genre, COUNT(*) AS n FROM word_genre WHERE rank=0 "
            "GROUP BY genre") if r["genre"] in KEYS}
    except sqlite3.Error:
        return {}


def genre_members(genre: str) -> set[tuple[str, str]]:
    """Every (expression, reading) in *genre*, primary or secondary."""
    con = _cx()
    if con is None or genre not in KEYS:
        return set()
    try:
        return {(r["expression"], r["reading"]) for r in con.execute(
            "SELECT expression, reading FROM word_genre WHERE genre=?",
            (genre,))}
    except sqlite3.Error:
        return set()


def members_of(genres) -> set[tuple[str, str]]:
    """The union of several genres' members (the pool a genre filter keeps)."""
    out: set[tuple[str, str]] = set()
    for g in genres or ():
        out |= genre_members(g)
    return out


# --------------------------------------------------------------------------- #
# Lookalikes / soundalikes — shaped for the sampler's boost maps
# --------------------------------------------------------------------------- #
_shape_cache: dict[str, set[str]] | None = None
_sound_cache: dict[tuple[str, str], set[tuple[str, str]]] | None = None


def shape_map() -> dict[str, set[str]]:
    """kanji -> set of kanji that look like it.

    Same shape as :func:`kanjire.data.kanjidata.series_map`, so it drops
    straight into the sampler's affinity machinery. Cached; read-only.
    """
    global _shape_cache
    if _shape_cache is not None:
        return _shape_cache
    out: dict[str, set[str]] = {}
    con = _cx()
    if con is not None:
        try:
            for row in con.execute("SELECT kanji, neighbour FROM kanji_shape"):
                out.setdefault(row["kanji"], set()).add(row["neighbour"])
        except sqlite3.Error:
            out = {}
    _shape_cache = out
    return out


def sound_map() -> dict[tuple[str, str], set[tuple[str, str]]]:
    """word key -> set of word keys that sound like it (like ``pair_boost``)."""
    global _sound_cache
    if _sound_cache is not None:
        return _sound_cache
    out: dict[tuple[str, str], set[tuple[str, str]]] = {}
    con = _cx()
    if con is not None:
        try:
            for row in con.execute(
                "SELECT expression, reading, n_expression, n_reading "
                "FROM word_sound"
            ):
                out.setdefault((row["expression"], row["reading"]), set()).add(
                    (row["n_expression"], row["n_reading"]))
        except sqlite3.Error:
            out = {}
    _sound_cache = out
    return out


def close() -> None:
    global _con, _tried, _genre_cache, _shape_cache, _sound_cache
    try:
        if _con is not None:
            _con.close()
    except sqlite3.Error:
        pass
    _con = None
    _tried = False
    _genre_cache = _shape_cache = _sound_cache = None
