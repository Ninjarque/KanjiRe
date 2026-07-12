"""Per-word knowledge stats: recording, classifying, summarising.

Every event the engine emits (a word was *seen*, *matched*, or *confused* with
another) is upserted into ``word_stats`` keyed by ``(expression, reading)`` — so
the same word played from any deck contributes to one cross-deck profile.

Each call commits its own write, deliberately, so an abandoned game still
leaves an honest trail. At human click rates the overhead is negligible.

Bucket classification
---------------------
A word lands in one of three buckets, driven entirely by what's in the table:

* ``unknown``    — never seen on a board
* ``less_known`` — seen, but the knowledge score is below the threshold
* ``known``      — seen, score at or above the threshold

``knowledge_score = matches / (matches + λ·mistakes_total + ε)`` where
λ weights mistakes a touch more than matches (so a single mistake meaningfully
slows your progress) and ε floors a fresh word at 0 rather than NaN.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from kanjire.data import db
from kanjire.model.vocab import Word

_FACES = ("kanji", "reading", "meaning")

#: Words with this many seen events and ``knowledge_score >= KNOWN_THRESHOLD``
#: are considered *known*.
KNOWN_THRESHOLD = 0.55
#: How much each mistake weighs against a match in the knowledge score.
_LAMBDA = 1.5
_EPS = 1.0  # keeps fresh words at score 0 rather than nan
#: A clean run of this many consecutive correct matches marks a word *known*
#: outright, no matter how badly it was flubbed in the past. This is what stops
#: Learn mode from re-proposing words you now consistently get right.
STREAK_KNOWN = 2
#: Each correct match in the active clean streak forgives this much of a past
#: mistake in the score (mistakes never literally leave the DB; the score just
#: recovers as you keep getting the word right).
_STREAK_FORGIVENESS = 0.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    """Player-local calendar date — the heatmap/streak day key."""
    return datetime.now().astimezone().date().isoformat()


#: FSRS-style ratings recorded in ``review_log``.
RATING_AGAIN = 1
RATING_GOOD = 3


# --------------------------------------------------------------------------- #
# Bucket / score helpers (pure functions, no DB)
# --------------------------------------------------------------------------- #
def _mistake_total(row: dict) -> int:
    return (
        (row.get("mistakes_kanji") or 0)
        + (row.get("mistakes_reading") or 0)
        + (row.get("mistakes_meaning") or 0)
    )


def knowledge_score(row: dict) -> float:
    matches = row.get("matches") or 0
    mistakes = _mistake_total(row)
    streak = row.get("current_streak") or 0
    # A current clean streak forgives a slice of old mistakes so the score
    # recovers for words the player has come to know.
    effective_mistakes = max(0.0, mistakes - _STREAK_FORGIVENESS * streak)
    return matches / (matches + _LAMBDA * effective_mistakes + _EPS)


def classify(row: dict | None) -> str:
    if row is None or not row.get("seen"):
        return "unknown"
    # A clean recent streak is decisive: a word you keep getting right is
    # "known" even if you flubbed it long ago.
    if (row.get("current_streak") or 0) >= STREAK_KNOWN:
        return "known"
    # A never-failed, matched word shouldn't languish in less_known just because
    # a single clean match only scores 0.5.
    if _mistake_total(row) == 0 and (row.get("matches") or 0) >= 1:
        return "known"
    return "known" if knowledge_score(row) >= KNOWN_THRESHOLD else "less_known"


# --------------------------------------------------------------------------- #
# Recorder
# --------------------------------------------------------------------------- #
class StatsRecorder:
    """Owns one writable SQLite connection and a tiny set of upsert verbs."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        db.init_stats_schema(con)

    # ---- events emitted by the engine ------------------------------- #
    def saw(self, word: Word) -> None:
        ts = _now()
        self.con.execute(
            """
            INSERT INTO word_stats (expression, reading, meaning, seen, last_seen_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(expression, reading) DO UPDATE SET
              seen         = seen + 1,
              meaning      = COALESCE(excluded.meaning, meaning),
              last_seen_at = excluded.last_seen_at
            """,
            (word.expression, word.reading, word.meaning, ts),
        )
        self.con.commit()

    def matched(self, word: Word) -> None:
        ts = _now()
        self.con.execute(
            """
            INSERT INTO word_stats (expression, reading, meaning, matches,
                                    last_correct_at, last_seen_at, current_streak)
            VALUES (?, ?, ?, 1, ?, ?, 1)
            ON CONFLICT(expression, reading) DO UPDATE SET
              matches         = matches + 1,
              meaning         = COALESCE(excluded.meaning, meaning),
              last_correct_at = excluded.last_correct_at,
              last_seen_at    = COALESCE(excluded.last_seen_at, last_seen_at),
              current_streak  = current_streak + 1
            """,
            (word.expression, word.reading, word.meaning, ts, ts),
        )
        self._log_event(ts, word, "match", None, RATING_GOOD)
        self.con.commit()

    def confused(self, target: Word, offending: Word, face: str) -> None:
        """A mismatch click: ``offending`` was selected while ``target`` was the
        in-progress group, and the click was on ``offending``'s ``face`` card."""
        if face not in _FACES:
            return
        col = f"mistakes_{face}"
        ts = _now()
        sql = f"""
        INSERT INTO word_stats (expression, reading, meaning,
                                {col}, last_mistake_at, current_streak)
        VALUES (?, ?, ?, 1, ?, 0)
        ON CONFLICT(expression, reading) DO UPDATE SET
          {col}            = {col} + 1,
          meaning          = COALESCE(excluded.meaning, meaning),
          last_mistake_at  = excluded.last_mistake_at,
          current_streak   = 0
        """
        # Both words involved get pinged on the same face dimension - that's the
        # whole point of the "only the wrong" semantics.
        for w in (target, offending):
            self.con.execute(sql, (w.expression, w.reading, w.meaning, ts))
            self._log_event(ts, w, "confuse", face, RATING_AGAIN)
        self.con.commit()

    def _log_event(self, ts: str, word: Word, event: str, face: str | None,
                   rating: int) -> None:
        self.con.execute(
            """
            INSERT INTO review_log (ts, day, expression, reading, event, face, rating)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, _today(), word.expression, word.reading, event, face, rating),
        )

    # ---- queries used by the stats / learn scenes ------------------- #
    def get_for(self, expression: str, reading: str) -> dict | None:
        row = self.con.execute(
            "SELECT * FROM word_stats WHERE expression=? AND reading=?",
            (expression, reading),
        ).fetchone()
        return dict(row) if row else None

    def all_rows(self) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT * FROM word_stats")]

    def hardest_seen(self, limit: int = 50) -> list[dict]:
        """Rows for words the player has SEEN and struggled with most, hardest
        first (ascending streak-aware knowledge score, then most mistakes).
        Used to pick Survival heart/coin bounties."""
        rows = [
            dict(r)
            for r in self.con.execute("SELECT * FROM word_stats WHERE seen > 0")
        ]
        rows.sort(key=lambda r: (knowledge_score(r), -_mistake_total(r)))
        return rows[:limit]

    def overview(self) -> dict:
        row = self.con.execute(
            """
            SELECT
              COUNT(*)             AS total_words,
              COALESCE(SUM(seen),0)             AS total_seen,
              COALESCE(SUM(matches),0)          AS total_matches,
              COALESCE(SUM(mistakes_kanji),0)   AS m_kanji,
              COALESCE(SUM(mistakes_reading),0) AS m_reading,
              COALESCE(SUM(mistakes_meaning),0) AS m_meaning
            FROM word_stats
            """
        ).fetchone()
        return dict(row) if row else {}

    def bucket_counts(self) -> dict[str, int]:
        counts = {"known": 0, "less_known": 0, "unknown": 0}
        for r in self.all_rows():
            counts[classify(r)] += 1
        return counts

    def classify_words(self, words: Iterable[Word]) -> dict[str, list[Word]]:
        """Group an arbitrary iterable of :class:`Word` by bucket using current
        stats. Words with no row in the table are *unknown*."""
        out: dict[str, list[Word]] = {"known": [], "less_known": [], "unknown": []}
        for w in words:
            row = self.get_for(w.expression, w.reading)
            out[classify(row)].append(w)
        return out

    def day_counts(self) -> dict[str, int]:
        """Review events per player-local day (heatmap fuel): {YYYY-MM-DD: n}."""
        return {
            r["day"]: r["n"]
            for r in self.con.execute(
                "SELECT day, COUNT(*) AS n FROM review_log GROUP BY day"
            )
        }

    def reviews_today(self) -> int:
        row = self.con.execute(
            "SELECT COUNT(*) AS n FROM review_log WHERE day=?", (_today(),)
        ).fetchone()
        return row["n"] if row else 0

    def reset_word(self, expression: str, reading: str) -> None:
        self.con.execute(
            "DELETE FROM word_stats WHERE expression=? AND reading=?",
            (expression, reading),
        )
        self.con.execute(
            "DELETE FROM review_log WHERE expression=? AND reading=?",
            (expression, reading),
        )
        self.con.commit()

    def reset_all(self) -> None:
        self.con.execute("DELETE FROM word_stats")
        self.con.execute("DELETE FROM review_log")
        self.con.commit()

    def close(self) -> None:
        try:
            self.con.close()
        except sqlite3.Error:
            pass
