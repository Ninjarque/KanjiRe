"""The reading library: passages the player added, and where they left off.

Lives in the per-user stats DB (never in the bundled vocabulary DB), so a
release update swaps the program and the dictionaries while a half-read
novel stays exactly where it was — the same guarantee stats and streaks get.

A passage is stored as its sentences plus a cursor. Progress is therefore
just ``position / len(sentences)``, which is what the library list fills its
bars with, and resuming is just seeking back to the cursor.
"""
from __future__ import annotations

import hashlib
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
    weave        TEXT,                       -- JSON: the crutch counters
    -- Cross-device identity: a content hash, because `id` is a per-device
    -- autoincrement and two phones would collide on it immediately.
    key          TEXT,
    -- Tombstone. Without it, deleting a novel on one device just lets the
    -- other device sync it straight back.
    deleted_at   TEXT
);
CREATE TABLE IF NOT EXISTS library_sentences (
    book_id INTEGER NOT NULL,
    idx     INTEGER NOT NULL,
    ja      TEXT NOT NULL,
    PRIMARY KEY (book_id, idx)
);
"""


#: A device will not push more library text than this per snapshot. A novel
#: is the player's own file, but the snapshot rides an MQTT relay, so there
#: has to be a ceiling; the largest passages simply stay local.
MAX_SYNC_CHARS = 400_000


def book_key(title: str, sentences) -> str:
    """Stable identity for a passage across devices.

    A content hash, so the same chapter added on the phone and on the desktop
    is recognised as one book rather than duplicated. Title is included so two
    genuinely different passages that happen to share text stay distinct.
    """
    h = hashlib.sha256()
    h.update((title or "").strip().encode("utf-8"))
    h.update(b"\x00")
    for line in sentences:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:24]


def _later(a: str | None, b: str | None) -> str | None:
    """The later of two ISO timestamps (None counts as earliest)."""
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


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
        # Additive migrations. CREATE TABLE IF NOT EXISTS does NOT add columns
        # to a table that already exists, so anyone who used the library before
        # sync arrived has a row shape without these — and every read would
        # fail with "no such column". Same pattern as StatsRecorder.
        for column, decl in (("key", "TEXT"), ("deleted_at", "TEXT")):
            try:
                con.execute(f"ALTER TABLE library ADD COLUMN {column} {decl}")
            except sqlite3.OperationalError:
                pass                    # already there
        # Backfill identities for books added before keys existed, so they can
        # take part in sync instead of being invisible to it.
        try:
            missing = [r["id"] for r in con.execute(
                "SELECT id FROM library WHERE key IS NULL OR key=''")]
            for book_id in missing:
                row = con.execute("SELECT title FROM library WHERE id=?",
                                  (book_id,)).fetchone()
                lines = [r["ja"] for r in con.execute(
                    "SELECT ja FROM library_sentences WHERE book_id=? "
                    "ORDER BY idx", (book_id,))]
                con.execute("UPDATE library SET key=? WHERE id=?",
                            (book_key(row["title"] if row else "", lines),
                             book_id))
        except sqlite3.Error:
            pass
        con.commit()

    # ---- adding / removing --------------------------------------------- #
    def add(self, title: str, text: str) -> int | None:
        """Store a passage, split into sentences. Returns its id, or None
        when there was nothing readable in it."""
        sentences = split_sentences(text)
        if not sentences:
            return None
        title = (title or "").strip() or "Untitled"
        key = book_key(title, sentences)
        existing = self.con.execute(
            "SELECT id FROM library WHERE key=? AND deleted_at IS NULL",
            (key,)).fetchone()
        if existing:
            return int(existing["id"])      # same passage: don't duplicate
        cur = self.con.execute(
            "INSERT INTO library (title, added_at, last_read_at, chars, "
            "n_sentences, position, weave, key) VALUES (?,?,?,?,?,0,NULL,?)",
            (title[:120], _now(), None, sum(len(s) for s in sentences),
             len(sentences), key),
        )
        book_id = int(cur.lastrowid)
        self.con.executemany(
            "INSERT INTO library_sentences (book_id, idx, ja) VALUES (?,?,?)",
            [(book_id, i, s) for i, s in enumerate(sentences)],
        )
        self.con.commit()
        return book_id

    def delete(self, book_id: int) -> bool:
        """Tombstone the book and drop its text.

        The row survives (with ``deleted_at``) so the deletion can travel to
        the player's other devices; without that, the other device would sync
        the novel straight back.
        """
        cur = self.con.execute(
            "UPDATE library SET deleted_at=?, position=0, weave=NULL "
            "WHERE id=? AND deleted_at IS NULL", (_now(), book_id))
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
            "SELECT id FROM library WHERE deleted_at IS NULL ORDER BY "
            "COALESCE(last_read_at, added_at) DESC, id DESC")]
        return [b for b in (self.get(i) for i in ids) if b]

    def get(self, book_id: int) -> dict | None:
        row = self.con.execute(
            "SELECT * FROM library WHERE id=? AND deleted_at IS NULL",
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
                # Which appearances have already been counted as evidence —
                # without this a reopened novel would score them all again.
                "scored": dict(getattr(weave, "scored", {})),
                "spent": sorted(getattr(weave, "spent", ()) or ()),
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
            state.scored = {str(k): str(v) for k, v
                            in (data.get("scored") or {}).items()}
            state.spent = {str(v) for v in (data.get("spent") or ())}
        except Exception:      # noqa: BLE001 — a corrupt blob just resets
            return WeaveState()
        return state

    # ---- sync ---------------------------------------------------------- #
    def export(self) -> list[dict]:
        """Every passage as a portable record, newest first, size-capped.

        Deterministically ordered so two devices holding the same library
        produce byte-identical snapshots — the digest is what tells the sync
        service there is nothing left to exchange.
        """
        out: list[dict] = []
        budget = MAX_SYNC_CHARS
        for row in self.con.execute("SELECT * FROM library ORDER BY key"):
            book = dict(row)
            if not book.get("key"):
                continue                    # pre-sync row; nothing to match on
            record = {
                "key": book["key"],
                "title": book["title"],
                "added_at": book["added_at"],
                "last_read_at": book["last_read_at"],
                "position": int(book["position"] or 0),
                "n_sentences": int(book["n_sentences"] or 0),
                "chars": int(book["chars"] or 0),
                "weave": book["weave"],
                "deleted_at": book["deleted_at"],
                "sentences": [],
            }
            if not book["deleted_at"]:
                size = int(book["chars"] or 0)
                if size <= budget:
                    budget -= size
                    record["sentences"] = [
                        r["ja"] for r in self.con.execute(
                            "SELECT ja FROM library_sentences WHERE book_id=? "
                            "ORDER BY idx", (book["id"],))]
            out.append(record)
        return out

    def merge(self, remote: list[dict]) -> int:
        """Fold another device's library in. Returns rows changed.

        Rules, chosen to match the rest of sync:
        * a book is identified by its content key;
        * **furthest position wins** — finishing a chapter on the phone must
          not be undone by the desktop's staler cursor;
        * the crutch counters come from whichever side read most recently;
        * a tombstone always wins, so a deletion propagates instead of the
          novel coming back.
        """
        changed = 0
        for record in remote or []:
            key = record.get("key")
            if not key:
                continue
            local = self.con.execute(
                "SELECT * FROM library WHERE key=?", (key,)).fetchone()
            if local is None:
                if record.get("deleted_at"):
                    # Never seen it and it's already gone: keep the tombstone
                    # so a third device can't reintroduce it either.
                    self.con.execute(
                        "INSERT INTO library (title, added_at, chars, "
                        "n_sentences, position, key, deleted_at) "
                        "VALUES (?,?,?,?,0,?,?)",
                        (record.get("title") or "Untitled",
                         record.get("added_at"), 0, 0, key,
                         record["deleted_at"]))
                    changed += 1
                    continue
                sentences = record.get("sentences") or []
                if not sentences:
                    continue                # text was over the size cap
                cur = self.con.execute(
                    "INSERT INTO library (title, added_at, last_read_at, "
                    "chars, n_sentences, position, weave, key) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (record.get("title") or "Untitled",
                     record.get("added_at"), record.get("last_read_at"),
                     sum(len(x) for x in sentences), len(sentences),
                     min(int(record.get("position") or 0), len(sentences)),
                     record.get("weave"), key))
                book_id = int(cur.lastrowid)
                self.con.executemany(
                    "INSERT INTO library_sentences (book_id, idx, ja) "
                    "VALUES (?,?,?)",
                    [(book_id, i, x) for i, x in enumerate(sentences)])
                changed += 1
                continue

            if record.get("deleted_at") and not local["deleted_at"]:
                self.con.execute(
                    "UPDATE library SET deleted_at=?, position=0, weave=NULL "
                    "WHERE id=?", (record["deleted_at"], local["id"]))
                self.con.execute(
                    "DELETE FROM library_sentences WHERE book_id=?",
                    (local["id"],))
                changed += 1
                continue
            if local["deleted_at"]:
                continue                    # a tombstone is never revived

            position = max(int(local["position"] or 0),
                           int(record.get("position") or 0))
            position = min(position, int(local["n_sentences"] or 0))
            newer = _later(local["last_read_at"], record.get("last_read_at"))
            weave = local["weave"]
            if newer and newer == record.get("last_read_at") \
                    and newer != local["last_read_at"]:
                weave = record.get("weave") or weave
            if (position != int(local["position"] or 0)
                    or newer != local["last_read_at"]
                    or weave != local["weave"]):
                self.con.execute(
                    "UPDATE library SET position=?, last_read_at=?, weave=? "
                    "WHERE id=?", (position, newer, weave, local["id"]))
                changed += 1
        self.con.commit()
        return changed

    def totals(self) -> dict:
        row = self.con.execute(
            "SELECT COUNT(*) AS books, COALESCE(SUM(chars), 0) AS chars "
            "FROM library").fetchone()
        return {"books": row["books"], "chars": row["chars"]}
