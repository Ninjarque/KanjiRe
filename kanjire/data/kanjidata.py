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


def words_of(sentence_id: int) -> list[tuple[str, str | None, bool]]:
    """Every indexed word of one sentence: (headword, reading, checked)."""
    _, st = _cons()
    if st is None:
        return []
    return [
        (r["headword"], r["reading"], bool(r["good"]))
        for r in st.execute(
            "SELECT headword, reading, good FROM sentence_words "
            "WHERE sentence_id=?", (sentence_id,))
    ]


def readable_sentences(known_exprs: set[str], *, max_unknown: int = 1,
                       limit: int = 40,
                       exclude_ids: set[int] | None = None,
                       rng=None) -> list[dict]:
    """Sentences at the player's level: every kanji word known except at
    most *max_unknown* (the i+1 rule). Returns dicts with
    ``{id, ja, en, unknown}`` sorted easiest-first with a random tiebreak.

    ``known_exprs`` should be kanji-bearing dictionary forms; the density
    denominator (``n_kanji_words``) counts exactly those, so kana-only
    knowledge neither helps nor hurts the ratio.
    """
    import random as _random
    _, st = _cons()
    if st is None:
        return []
    rng = rng or _random.Random()
    exclude_ids = exclude_ids or set()

    counts: dict[int, int] = {}
    known_list = list(known_exprs)
    for i in range(0, len(known_list), 400):
        chunk = known_list[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in st.execute(
            f"SELECT sentence_id, COUNT(DISTINCT headword) AS n "
            f"FROM sentence_words WHERE headword IN ({marks}) "
            f"GROUP BY sentence_id", chunk,
        ):
            counts[r["sentence_id"]] = counts.get(r["sentence_id"], 0) + r["n"]

    prelim: list[dict] = []
    for r in st.execute(
            "SELECT id, ja, en, n_kanji_words FROM sentences "
            "WHERE n_kanji_words > 0"):
        if r["id"] in exclude_ids:
            continue
        unknown = r["n_kanji_words"] - counts.get(r["id"], 0)
        if 0 <= unknown <= max_unknown:
            prelim.append({"id": r["id"], "ja": r["ja"], "en": r["en"],
                           "unknown": unknown})

    # Refine: the word-count denominator ignores kanji the indexer dropped
    # (proper nouns, unresolved tokens). A sentence with such kanji is NOT one
    # you know every word of, so fetch the shortlist's indexed headwords and
    # bump unknown by one when uncovered kanji remain. (Only the shortlist is
    # queried, so this stays cheap.)
    from kanjire.jputil import uncovered_kanji
    heads_by_sent: dict[int, list[str]] = {}
    ids = [s["id"] for s in prelim]
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in st.execute(
            f"SELECT sentence_id, headword FROM sentence_words "
            f"WHERE sentence_id IN ({marks})", chunk,
        ):
            heads_by_sent.setdefault(r["sentence_id"], []).append(r["headword"])

    out: list[dict] = []
    for s in prelim:
        if uncovered_kanji(s["ja"], heads_by_sent.get(s["id"], [])):
            s["unknown"] += 1
        if s["unknown"] <= max_unknown:
            out.append(s)
    out.sort(key=lambda s: (s["unknown"], rng.random()))
    return out[:limit]


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
