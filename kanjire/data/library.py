"""The reading library: passages the player added, and where they left off.

Lives in the per-user stats DB (never in the bundled vocabulary DB), so a
release update swaps the program and the dictionaries while a half-read
novel stays exactly where it was — the same guarantee stats and streaks get.

A passage is stored as its sentences plus a cursor. Progress is therefore
just ``position / len(sentences)``, which is what the library list fills its
bars with, and resuming is just seeking back to the cursor.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from kanjire.data.weave import split_sentences

SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    added_at     TEXT,
    last_read_at TEXT,
    chars        INTEGER NOT NULL DEFAULT 0,
    n_sentences  INTEGER NOT NULL DEFAULT 0,
    position     INTEGER NOT NULL DEFAULT 0,
    weave        TEXT                        -- JSON: the crutch counters
);
CREATE TABLE IF NOT EXISTS library_sentences (
    book_id INTEGER NOT NULL,
    idx     INTEGER NOT NULL,
    ja      TEXT NOT NULL,
    PRIMARY KEY (book_id, idx)
);
"""


def _now() -> str:
    # Microseconds, not seconds: "most recently read first" ties when a
    # reader opens two passages within the same second, and then falls back
    # to insertion order — the opposite of what the list promises.
    return datetime.now(timezone.utc).isoformat()


class Library:
    """Owns the library tables on an existing stats connection."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        con.executescript(SCHEMA)
        con.commit()

    # ---- adding / removing --------------------------------------------- #
    def add(self, title: str, text: str) -> int | None:
        """Store a passage, split into sentences. Returns its id, or None
        when there was nothing readable in it."""
        sentences = split_sentences(text)
        if not sentences:
            return None
        title = (title or "").strip() or "Untitled"
        cur = self.con.execute(
            "INSERT INTO library (title, added_at, last_read_at, chars, "
            "n_sentences, position, weave) VALUES (?,?,?,?,?,0,NULL)",
            (title[:120], _now(), None, sum(len(s) for s in sentences),
             len(sentences)),
        )
        book_id = int(cur.lastrowid)
        self.con.executemany(
            "INSERT INTO library_sentences (book_id, idx, ja) VALUES (?,?,?)",
            [(book_id, i, s) for i, s in enumerate(sentences)],
        )
        self.con.commit()
        return book_id

    def delete(self, book_id: int) -> bool:
        cur = self.con.execute("DELETE FROM library WHERE id=?", (book_id,))
        self.con.execute("DELETE FROM library_sentences WHERE book_id=?",
                         (book_id,))
        self.con.commit()
        return cur.rowcount > 0

    def rename(self, book_id: int, title: str) -> None:
        title = (title or "").strip()
        if not title:
            return
        self.con.execute("UPDATE library SET title=? WHERE id=?",
                         (title[:120], book_id))
        self.con.commit()

    # ---- reading ------------------------------------------------------- #
    def books(self) -> list[dict]:
        """Every passage, most recently read first, with its progress."""
        ids = [r["id"] for r in self.con.execute(
            "SELECT id FROM library ORDER BY "
            "COALESCE(last_read_at, added_at) DESC, id DESC")]
        return [b for b in (self.get(i) for i in ids) if b]

    def get(self, book_id: int) -> dict | None:
        row = self.con.execute("SELECT * FROM library WHERE id=?",
                               (book_id,)).fetchone()
        if row is None:
            return None
        book = dict(row)
        total = book["n_sentences"] or 0
        book["ratio"] = (book["position"] / total) if total else 0.0
        book["done"] = bool(total and book["position"] >= total)
        return book

    def sentences(self, book_id: int, start: int = 0,
                  limit: int = 40) -> list[str]:
        """A window of sentences — a novel is not rendered all at once."""
        return [r["ja"] for r in self.con.execute(
            "SELECT ja FROM library_sentences WHERE book_id=? AND idx>=? "
            "ORDER BY idx LIMIT ?", (book_id, max(0, start), max(1, limit)))]

    def save_position(self, book_id: int, position: int,
                      weave=None) -> None:
        """Remember where the reader is, and which words are still English.

        Called as the player scrolls, so re-opening a novel lands them back
        on the line they stopped at rather than at chapter one.
        """
        blob = None
        if weave is not None:
            blob = json.dumps({
                "holds": [[k[0], k[1], v] for k, v in weave.holds.items()],
                "taps": [[k[0], k[1], v] for k, v in weave.taps.items()],
            })
        self.con.execute(
            "UPDATE library SET position=?, last_read_at=?, "
            "weave=COALESCE(?, weave) WHERE id=?",
            (max(0, int(position)), _now(), blob, book_id))
        self.con.commit()

    def load_weave(self, book_id: int):
        """The stored crutch counters, or a fresh state."""
        from kanjire.data.weave import WeaveState
        row = self.con.execute("SELECT weave FROM library WHERE id=?",
                               (book_id,)).fetchone()
        state = WeaveState()
        if not row or not row["weave"]:
            return state
        try:
            data = json.loads(row["weave"])
            state.holds = {(e, r): int(v) for e, r, v in data.get("holds", [])}
            state.taps = {(e, r): int(v) for e, r, v in data.get("taps", [])}
        except Exception:      # noqa: BLE001 — a corrupt blob just resets
            return WeaveState()
        return state

    def totals(self) -> dict:
        row = self.con.execute(
            "SELECT COUNT(*) AS books, COALESCE(SUM(chars), 0) AS chars "
            "FROM library").fetchone()
        return {"books": row["books"], "chars": row["chars"]}
