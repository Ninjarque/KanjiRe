"""Per-genre, per-level progress — the model behind the Genres browser.

The Journey road measures progress along one frequency-ordered line. Genres
measure it along forty topical ones, each split by JLPT level, so "how much
of the food vocabulary do I actually know, at my level?" has an answer.

Toolkit-free on purpose: both the pyglet scene and the Kivy screen render
from these same numbers, the way ``menuconfig`` already shares the settings
model.
"""
from __future__ import annotations

from typing import NamedTuple

from kanjire.data import clusters
from kanjire.data.genres import GENRES
from kanjire.data.stats import classify

#: Levels shown as a genre's rows, easiest first.
LEVELS = (5, 4, 3, 2, 1)


class Cell(NamedTuple):
    """One genre x level bucket."""
    genre: str
    level: int | None          #: None = the genre's total across all levels
    known: int
    total: int
    words: tuple               #: the Word objects behind it

    @property
    def ratio(self) -> float:
        return self.known / self.total if self.total else 0.0

    @property
    def playable(self) -> bool:
        # Fewer than four words can't make a board worth dealing.
        return self.total >= 4

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.known >= self.total


def build(pool, stats_rows: dict) -> dict[str, dict]:
    """Map every genre to its per-level and total progress.

    *pool* is the candidate words (already deck/level filtered by the caller);
    *stats_rows* maps ``(expression, reading)`` to a stats row, exactly as the
    Journey scene already assembles it, so this costs one pass and no queries.

    Genres with no words in *pool* are still present, with zero totals — the
    browser dims them rather than hiding them, so the grid keeps its shape.
    """
    index = clusters.genre_index()
    buckets: dict[str, dict[int | None, list]] = {
        g.key: {lvl: [] for lvl in LEVELS} for g in GENRES}
    for word in pool:
        for genre in index.get((word.expression, word.reading), ()):
            slot = buckets.get(genre)
            if slot is None:
                continue          # a genre this build doesn't know
            if word.jlpt in slot:
                slot[word.jlpt].append(word)

    out: dict[str, dict] = {}
    for g in GENRES:
        rows: dict[int | None, Cell] = {}
        all_words: list = []
        all_known = 0
        for lvl in LEVELS:
            words = buckets[g.key][lvl]
            known = sum(1 for w in words
                        if classify(stats_rows.get(
                            (w.expression, w.reading))) == "known")
            rows[lvl] = Cell(g.key, lvl, known, len(words), tuple(words))
            all_words.extend(words)
            all_known += known
        rows[None] = Cell(g.key, None, all_known, len(all_words),
                          tuple(all_words))
        out[g.key] = rows
    return out


def totals(progress: dict[str, dict]) -> tuple[int, int, int]:
    """``(genres_started, genres_complete, words_known)`` across the board."""
    started = complete = known = 0
    for rows in progress.values():
        cell = rows[None]
        known += cell.known
        if cell.known:
            started += 1
        if cell.complete:
            complete += 1
    return started, complete, known
