"""SQLite access layer for vocabulary decks.

The database is the single source of truth the game reads at runtime. It is
produced by the offline build scripts (``scripts/build_jlpt_dataset.py`` and
``scripts/ingest_corpus.py``) and is fully self-contained: no NLP libraries are
needed to *read* it.

Tables
------
``decks``  - one row per source deck (the JLPT list, or an ingested corpus).
``words``  - vocabulary items (expression / reading / meaning / level / freq).
``kanji``  - per-kanji frequency & info, mostly for corpus decks and stats.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from kanjire.jputil import has_kanji
from kanjire.model.vocab import Word
from kanjire.paths import DB_PATH

#: Vocabulary tables (read-only at runtime in a release build).
VOCAB_SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    name        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,            -- 'jlpt' | 'corpus'
    description TEXT,
    source      TEXT,
    created_at  TEXT,
    word_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS words (
    id          INTEGER PRIMARY KEY,
    deck        TEXT NOT NULL,
    expression  TEXT NOT NULL,
    reading     TEXT NOT NULL,
    meaning     TEXT NOT NULL,             -- English gloss (always present)
    meaning_fr  TEXT,                       -- French gloss when JMdict has one
    jlpt        INTEGER,                   -- 5..1 or NULL
    freq        REAL NOT NULL DEFAULT 0,   -- zipf-like, higher = more common
    pos         TEXT,
    count       INTEGER,                   -- raw occurrences (corpus)
    has_kanji   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(deck, expression, reading)
);
CREATE INDEX IF NOT EXISTS idx_words_deck  ON words(deck);
CREATE INDEX IF NOT EXISTS idx_words_jlpt  ON words(jlpt);
CREATE INDEX IF NOT EXISTS idx_words_kanji ON words(has_kanji);

CREATE TABLE IF NOT EXISTS kanji (
    id        INTEGER PRIMARY KEY,
    deck      TEXT NOT NULL,
    char      TEXT NOT NULL,
    count     INTEGER DEFAULT 0,
    freq      REAL DEFAULT 0,
    grade     INTEGER,                    -- jouyou grade (KanjiDic2)
    jlpt      INTEGER,                    -- kanji JLPT level (KanjiDic2)
    meanings  TEXT,
    UNIQUE(deck, char)
);
CREATE INDEX IF NOT EXISTS idx_kanji_deck ON kanji(deck);
"""

#: Per-user knowledge tracking. Stored in its own SQLite file so updating the
#: bundled vocab DB never wipes player progress.
STATS_SCHEMA = """
-- Cross-deck per-word knowledge stats: the same expression+reading played from
-- any deck contributes to one unified profile.
CREATE TABLE IF NOT EXISTS word_stats (
    expression       TEXT NOT NULL,
    reading          TEXT NOT NULL,
    meaning          TEXT,                       -- cached last-seen gloss
    seen             INTEGER NOT NULL DEFAULT 0, -- board appearances
    matches          INTEGER NOT NULL DEFAULT 0, -- groups completed
    mistakes_kanji   INTEGER NOT NULL DEFAULT 0,
    mistakes_reading INTEGER NOT NULL DEFAULT 0,
    mistakes_meaning INTEGER NOT NULL DEFAULT 0,
    last_seen_at     TEXT,
    last_correct_at  TEXT,
    last_mistake_at  TEXT,
    current_streak   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (expression, reading)
);
CREATE INDEX IF NOT EXISTS idx_stats_seen    ON word_stats(seen);
CREATE INDEX IF NOT EXISTS idx_stats_matches ON word_stats(matches);

-- Append-only log of graded recall events, one row per match/confusion.
-- This is the raw history behind the activity heatmap and (later) the
-- FSRS scheduler: never mutated, only inserted and (on word reset) deleted.
CREATE TABLE IF NOT EXISTS review_log (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,           -- UTC ISO-8601
    day        TEXT NOT NULL,           -- player-local YYYY-MM-DD (heatmap key)
    expression TEXT NOT NULL,
    reading    TEXT NOT NULL,
    event      TEXT NOT NULL,           -- 'match' | 'confuse' | 'recall'
    face       TEXT,                    -- mistake face for 'confuse' events
    rating     INTEGER NOT NULL,        -- FSRS-style: 1=again .. 4=easy
    partner_expression TEXT,            -- confuse: the other word involved
    partner_reading    TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_day  ON review_log(day);
CREATE INDEX IF NOT EXISTS idx_review_word ON review_log(expression, reading);
"""

#: Backward-compatible combined schema (older code, build scripts).
SCHEMA = VOCAB_SCHEMA + STATS_SCHEMA


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
def connect(path: Path | str = DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if read_only:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def init_stats_schema(con: sqlite3.Connection) -> None:
    """Create just the ``word_stats`` table on the given connection.

    Used by :class:`kanjire.data.stats.StatsRecorder` so the per-user stats
    DB stays focused; nothing else needs to live there."""
    con.executescript(STATS_SCHEMA)
    con.commit()


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def upsert_deck(
    con: sqlite3.Connection,
    name: str,
    kind: str,
    *,
    description: str = "",
    source: str = "",
    created_at: str = "",
) -> None:
    con.execute(
        """
        INSERT INTO decks(name, kind, description, source, created_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            kind=excluded.kind,
            description=excluded.description,
            source=excluded.source,
            created_at=excluded.created_at
        """,
        (name, kind, description, source, created_at),
    )


def upsert_word(
    con: sqlite3.Connection,
    *,
    deck: str,
    expression: str,
    reading: str,
    meaning: str,
    meaning_fr: str | None = None,
    jlpt: int | None = None,
    freq: float = 0.0,
    pos: str | None = None,
    count: int | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO words(deck, expression, reading, meaning, meaning_fr, jlpt,
                          freq, pos, count, has_kanji)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(deck, expression, reading) DO UPDATE SET
            meaning=excluded.meaning,
            meaning_fr=COALESCE(excluded.meaning_fr, words.meaning_fr),
            jlpt=COALESCE(excluded.jlpt, words.jlpt),
            freq=MAX(excluded.freq, words.freq),
            pos=COALESCE(excluded.pos, words.pos),
            count=COALESCE(excluded.count, words.count)
        """,
        (deck, expression, reading, meaning, meaning_fr, jlpt, freq, pos, count,
         int(has_kanji(expression))),
    )


def upsert_kanji(
    con: sqlite3.Connection,
    *,
    deck: str,
    char: str,
    count: int = 0,
    freq: float = 0.0,
    grade: int | None = None,
    jlpt: int | None = None,
    meanings: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO kanji(deck, char, count, freq, grade, jlpt, meanings)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(deck, char) DO UPDATE SET
            count=excluded.count,
            freq=excluded.freq,
            grade=COALESCE(excluded.grade, kanji.grade),
            jlpt=COALESCE(excluded.jlpt, kanji.jlpt),
            meanings=COALESCE(excluded.meanings, kanji.meanings)
        """,
        (deck, char, count, freq, grade, jlpt, meanings),
    )


def refresh_deck_counts(con: sqlite3.Connection) -> None:
    con.execute(
        """
        UPDATE decks SET word_count = (
            SELECT COUNT(*) FROM words WHERE words.deck = decks.name
        )
        """
    )
    con.commit()


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def _row_to_word(row: sqlite3.Row) -> Word:
    # meaning_fr may not exist in older DB files; ``row[...]`` would raise.
    try:
        meaning_fr = row["meaning_fr"]
    except (IndexError, KeyError):
        meaning_fr = None
    return Word(
        id=row["id"],
        expression=row["expression"],
        reading=row["reading"],
        meaning=row["meaning"],
        meaning_fr=meaning_fr,
        jlpt=row["jlpt"],
        freq=row["freq"],
        deck=row["deck"],
        pos=row["pos"],
        count=row["count"],
    )


def list_decks(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(con.execute("SELECT * FROM decks ORDER BY kind, name").fetchall())


def load_words(
    con: sqlite3.Connection,
    *,
    decks: Sequence[str] | None = None,
    levels: Iterable[int] | None = None,
    require_kanji: bool = True,
    min_freq: float = 0.0,
) -> list[Word]:
    """Load words matching the given filters.

    *decks*  - restrict to these deck names (default: all).
    *levels* - restrict to these JLPT levels (default: all).
    """
    clauses = ["1=1"]
    params: list[object] = []
    if decks:
        clauses.append(f"deck IN ({','.join('?' * len(decks))})")
        params.extend(decks)
    if levels:
        levels = list(levels)
        clauses.append(f"jlpt IN ({','.join('?' * len(levels))})")
        params.extend(levels)
    if require_kanji:
        clauses.append("has_kanji = 1")
    if min_freq > 0:
        clauses.append("freq >= ?")
        params.append(min_freq)

    sql = f"SELECT * FROM words WHERE {' AND '.join(clauses)}"
    rows = con.execute(sql, params).fetchall()
    return [_row_to_word(r) for r in rows]


def word_count(con: sqlite3.Connection, **kwargs) -> int:
    return len(load_words(con, **kwargs))
