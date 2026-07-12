"""Read-only access to the knowledge sidecars: components, phonetic series,
pitch accent (``kanjidata.db``) and example sentences (``sentences.db``).

Everything degrades gracefully when a sidecar is missing (dev checkout
before running the fetch scripts, or a stripped build): every query returns
empty results, and the UI simply doesn't show that block.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from kanjire.paths import DATA_DIR

KANJIDATA_PATH = DATA_DIR / "kanjidata.db"
SENTENCES_PATH = DATA_DIR / "sentences.db"

_kd_con: sqlite3.Connection | None = None
_st_con: sqlite3.Connection | None = None
_tried = False


def _open(path: Path) -> sqlite3.Connection | None:
    try:
        if not path.exists():
            return None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _cons() -> tuple[sqlite3.Connection | None, sqlite3.Connection | None]:
    global _kd_con, _st_con, _tried
    if not _tried:
        _kd_con = _open(KANJIDATA_PATH)
        _st_con = _open(SENTENCES_PATH)
        _tried = True
    return _kd_con, _st_con


def available() -> bool:
    kd, _ = _cons()
    return kd is not None


def components_of(kanji: str) -> list[str]:
    """Visual components of one kanji character ("試" -> 言 工 弋)."""
    kd, _ = _cons()
    if kd is None:
        return []
    row = kd.execute("SELECT components FROM components WHERE kanji=?",
                     (kanji,)).fetchone()
    return row["components"].split() if row else []


def keisei_info(kanji: str) -> dict | None:
    """Phonetic-compound analysis of one kanji, with its family.

    Returns ``{type, semantic, phonetic, readings, series}`` where series is
    the other kanji sharing the phonetic component (self excluded), or None
    when the kanji isn't in the dataset."""
    kd, _ = _cons()
    if kd is None:
        return None
    row = kd.execute("SELECT * FROM keisei_kanji WHERE kanji=?",
                     (kanji,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["readings"] = [r for r in (out.get("readings") or "").split(",") if r]
    out["series"] = []
    out["series_readings"] = []
    phon = out.get("phonetic")
    if phon:
        srow = kd.execute("SELECT * FROM keisei_series WHERE phonetic=?",
                          (phon,)).fetchone()
        if srow:
            out["series"] = [k for k in (srow["compounds"] or "").split()
                             if k != kanji]
            out["series_readings"] = [
                r for r in (srow["readings"] or "").split(",") if r]
    return out


def series_map() -> dict[str, set[str]]:
    """kanji -> set of kanji in the same phonetic series (for the sampler)."""
    kd, _ = _cons()
    out: dict[str, set[str]] = {}
    if kd is None:
        return out
    for row in kd.execute("SELECT phonetic, compounds FROM keisei_series"):
        members = (row["compounds"] or "").split()
        for m in members:
            out.setdefault(m, set()).update(x for x in members if x != m)
    return out


def pitch_of(expression: str, reading: str) -> str | None:
    """Pitch accent downstep number(s) for a word, e.g. "0" or "1,3"."""
    kd, _ = _cons()
    if kd is None:
        return None
    row = kd.execute(
        "SELECT accent FROM pitch WHERE expression=? AND reading=?",
        (expression, reading),
    ).fetchone()
    if row is None:
        row = kd.execute(
            "SELECT accent FROM pitch WHERE expression=? LIMIT 1",
            (expression,),
        ).fetchone()
    return row["accent"] if row else None


def sentences_for(expression: str, reading: str | None = None,
                  limit: int = 3) -> list[tuple[str, str]]:
    """Example sentences containing the word, checked examples first."""
    _, st = _cons()
    if st is None:
        return []
    if reading:
        rows = st.execute(
            """
            SELECT s.ja, s.en FROM sentence_words w
            JOIN sentences s ON s.id = w.sentence_id
            WHERE w.headword=? AND (w.reading=? OR w.reading IS NULL)
            ORDER BY w.good DESC, LENGTH(s.ja) ASC LIMIT ?
            """,
            (expression, reading, limit),
        ).fetchall()
    else:
        rows = st.execute(
            """
            SELECT s.ja, s.en FROM sentence_words w
            JOIN sentences s ON s.id = w.sentence_id
            WHERE w.headword=?
            ORDER BY w.good DESC, LENGTH(s.ja) ASC LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
    return [(r["ja"], r["en"]) for r in rows]


def close() -> None:
    global _kd_con, _st_con, _tried
    for con in (_kd_con, _st_con):
        try:
            if con is not None:
                con.close()
        except sqlite3.Error:
            pass
    _kd_con = _st_con = None
    _tried = False
