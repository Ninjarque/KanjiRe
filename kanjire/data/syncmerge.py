"""Cross-device progress snapshots + conflict-free merging.

The heart of device sync: :func:`export_snapshot` turns a player's progress
(stats.db + the progress-y parts of user_state) into one JSON-able dict, and
:func:`merge_snapshot` folds a remote snapshot into the local state.

Merging is designed to be **idempotent, commutative and monotone** — syncing
twice, in any order, from any number of devices, can only ever move progress
forward, never lose or double-count it:

* word_stats counters   → element-wise max      (monotone counters)
* word_stats timestamps → latest wins
* srs_state             → the row with the newest last_review wins wholesale
* review/session/read logs → set union on a natural key (append-only logs)
* high scores           → per-mode max
* streak                → the side with the newest streak_day wins
                          (same day: max count/freezes)
* presets               → union by name (local wins a name clash)

Deliberately NOT synced: cosmetic settings (palette, audio, locale — device
taste), the friend code and friends list (the code IS the device's identity
on the relay; cloning it would confuse presence).
"""
from __future__ import annotations

import json
from typing import Any

#: 2 = the reading library travels too (books + cursor + crutch state, with
#: tombstones). Older peers simply omit the key and see no library — the
#: merge treats a missing "library" as "nothing to say", never as "delete".
SNAPSHOT_VERSION = 2

#: word_stats columns that behave as monotone counters (merge = max).
_COUNTERS = ("seen", "matches", "mistakes_kanji", "mistakes_reading",
             "mistakes_meaning")
#: word_stats timestamp columns (merge = latest ISO string).
_STAMPS = ("last_seen_at", "last_correct_at", "last_mistake_at")

#: Cap the synced slice of the append-only logs (newest first). Keeps the
#: encrypted payload phone-friendly; older history stays on its own device.
_LOG_CAP = 20000
_SESSION_CAP = 60
_READ_CAP = 20000


def _later(a: str | None, b: str | None) -> str | None:
    """The later of two ISO timestamps (None = never)."""
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def export_snapshot(con, state) -> dict:
    """One JSON-able dict of everything worth carrying across devices.

    *con* is the stats.db connection, *state* the UserState.
    """
    rows = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]  # noqa: E731
    s = state.data.get("settings", {})
    # Every query is deterministically ORDERed: two devices holding the same
    # content must produce byte-identical snapshots (digest() equality is the
    # "nothing left to exchange" signal).
    return {
        "v": SNAPSHOT_VERSION,
        "word_stats": rows(
            "SELECT * FROM word_stats ORDER BY expression, reading"),
        "srs": (rows("SELECT * FROM srs_state ORDER BY expression, reading")
                if _has_srs(con) else []),
        "review_log": sorted(rows(
            "SELECT ts, day, expression, reading, event, face, rating,"
            " partner_expression, partner_reading FROM review_log"
            " ORDER BY id DESC LIMIT ?", _LOG_CAP),
            key=lambda r: (r["ts"], r["expression"], r["reading"],
                           r["event"], r["rating"])),
        "session_log": sorted(rows(
            "SELECT ts, day, mode, score, matches, mistakes, words"
            " FROM session_log ORDER BY id DESC LIMIT ?", _SESSION_CAP),
            key=lambda r: (r["ts"], r["mode"], r["score"])),
        "read_log": sorted(rows(
            "SELECT ts, day, sentence_id, chars, source FROM read_log"
            " ORDER BY id DESC LIMIT ?", _READ_CAP),
            key=lambda r: (r["ts"], r["sentence_id"])),
        "high_scores": dict(state.data.get("high_scores", {})),
        "streak": {
            "count": int(s.get("streak_count", 0) or 0),
            "freezes": int(s.get("streak_freezes", 0) or 0),
            "day": s.get("streak_day") or "",
        },
        "presets": list(state.data.get("presets", [])),
        # Your own texts, so a chapter started on the phone resumes on the
        # desktop. Ordered by content key inside export() for a stable digest.
        "library": _library(con).export(),
    }


#: One Library per connection. Constructing it runs executescript(), which
#: implicitly COMMITs — doing that on every export and every merge (i.e. every
#: sync tick) is both wasteful and a way to disturb an in-flight transaction.
_LIBRARIES: dict[int, object] = {}


def _library(con):
    from kanjire.data.library import Library
    lib = _LIBRARIES.get(id(con))
    if lib is None or getattr(lib, "con", None) is not con:
        lib = Library(con)
        _LIBRARIES[id(con)] = lib
    return lib


def _has_srs(con) -> bool:
    try:
        con.execute("SELECT 1 FROM srs_state LIMIT 1")
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Merge
# --------------------------------------------------------------------------- #
def merge_snapshot(con, state, snap: dict) -> dict[str, int]:
    """Fold a remote *snap* into local state. Returns change counts."""
    changed = {
        "words": _merge_word_stats(con, snap.get("word_stats") or []),
        "srs": _merge_srs(con, snap.get("srs") or []),
        "reviews": _merge_log(
            con, "review_log", snap.get("review_log") or [],
            key=("ts", "expression", "reading", "event", "rating"),
            cols=("ts", "day", "expression", "reading", "event", "face",
                  "rating", "partner_expression", "partner_reading")),
        "sessions": _merge_log(
            con, "session_log", snap.get("session_log") or [],
            key=("ts", "mode", "score"),
            cols=("ts", "day", "mode", "score", "matches", "mistakes",
                  "words")),
        "reads": _merge_log(
            con, "read_log", snap.get("read_log") or [],
            key=("ts", "sentence_id"),
            cols=("ts", "day", "sentence_id", "chars", "source")),
    }
    # Absent key = an older peer that knows nothing about the library. That
    # must mean "no news", not "delete everything".
    if "library" in snap:
        changed["library"] = _library(con).merge(snap.get("library") or [])
    con.commit()
    changed["scores"] = _merge_scores(state, snap.get("high_scores") or {})
    changed["streak"] = _merge_streak(state, snap.get("streak") or {})
    changed["presets"] = _merge_presets(state, snap.get("presets") or [])
    state.save()
    return changed


def _merge_word_stats(con, remote: list[dict]) -> int:
    changed = 0
    for r in remote:
        expr, read = r.get("expression"), r.get("reading")
        if not expr or read is None:
            continue
        cur = con.execute(
            "SELECT * FROM word_stats WHERE expression=? AND reading=?",
            (expr, read)).fetchone()
        if cur is None:
            cols = ("expression", "reading", "meaning", *_COUNTERS, *_STAMPS,
                    "current_streak")
            con.execute(
                f"INSERT INTO word_stats ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [r.get(c) if c not in _COUNTERS
                 else int(r.get(c) or 0) for c in cols])
            changed += 1
            continue
        cur = dict(cur)
        upd: dict[str, Any] = {}
        for c in _COUNTERS:
            rv = int(r.get(c) or 0)
            if rv > int(cur.get(c) or 0):
                upd[c] = rv
        for c in _STAMPS:
            lv = _later(cur.get(c), r.get(c))
            if lv != cur.get(c):
                upd[c] = lv
        # current_streak follows whichever side was seen more recently.
        if _later(cur.get("last_seen_at"), r.get("last_seen_at")) \
                == r.get("last_seen_at") and r.get("last_seen_at") \
                and r.get("last_seen_at") != cur.get("last_seen_at"):
            upd["current_streak"] = int(r.get("current_streak") or 0)
            if r.get("meaning"):
                upd["meaning"] = r["meaning"]
        if upd:
            sets = ",".join(f"{c}=?" for c in upd)
            con.execute(
                f"UPDATE word_stats SET {sets} "
                f"WHERE expression=? AND reading=?",
                [*upd.values(), expr, read])
            changed += 1
    return changed


def _merge_srs(con, remote: list[dict]) -> int:
    if not remote:
        return 0
    if not _has_srs(con):
        from kanjire.srs.store import _SCHEMA
        con.executescript(_SCHEMA)
    changed = 0
    cols = ("expression", "reading", "state", "step", "stability",
            "difficulty", "due", "last_review", "lapses", "created_day")
    for r in remote:
        expr, read = r.get("expression"), r.get("reading")
        if not expr or read is None:
            continue
        cur = con.execute(
            "SELECT last_review, stability FROM srs_state "
            "WHERE expression=? AND reading=?", (expr, read)).fetchone()
        if cur is not None:
            # Newest review wins wholesale; tie → higher stability
            # (deterministic on both sides).
            l_lr, r_lr = cur["last_review"], r.get("last_review")
            if _later(l_lr, r_lr) == l_lr and l_lr != r_lr:
                continue
            if l_lr == r_lr and (cur["stability"] or 0) >= \
                    (r.get("stability") or 0):
                continue
        con.execute(
            f"INSERT OR REPLACE INTO srs_state ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [r.get(c) for c in cols])
        changed += 1
    return changed


def _merge_log(con, table: str, remote: list[dict],
               key: tuple[str, ...], cols: tuple[str, ...]) -> int:
    if not remote:
        return 0
    have = {
        tuple(row[k] for k in key)
        for row in con.execute(f"SELECT {','.join(key)} FROM {table}")
    }
    added = 0
    for r in remote:
        k = tuple(r.get(c) for c in key)
        if k in have:
            continue
        have.add(k)
        con.execute(
            f"INSERT INTO {table} ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [r.get(c) for c in cols])
        added += 1
    return added


def _merge_scores(state, remote: dict) -> int:
    changed = 0
    scores = state.data.setdefault("high_scores", {})
    for mode, val in remote.items():
        if int(val or 0) > int(scores.get(mode, 0) or 0):
            scores[mode] = int(val)
            changed += 1
    return changed


def _merge_streak(state, remote: dict) -> int:
    s = state.data.setdefault("settings", {})
    r_day = remote.get("day") or ""
    l_day = s.get("streak_day") or ""
    if not r_day:
        return 0
    if r_day > l_day:
        s["streak_count"] = int(remote.get("count") or 0)
        s["streak_freezes"] = int(remote.get("freezes") or 0)
        s["streak_day"] = r_day
        return 1
    if r_day == l_day:
        changed = 0
        if int(remote.get("count") or 0) > int(s.get("streak_count", 0) or 0):
            s["streak_count"] = int(remote["count"])
            changed = 1
        if int(remote.get("freezes") or 0) > \
                int(s.get("streak_freezes", 0) or 0):
            s["streak_freezes"] = int(remote["freezes"])
            changed = 1
        return changed
    return 0


def _merge_presets(state, remote: list[dict]) -> int:
    presets = state.data.setdefault("presets", [])
    names = {p.get("name") for p in presets}
    added = 0
    for p in remote:
        if p.get("name") and p["name"] not in names:
            presets.append(dict(p))
            names.add(p["name"])
            added += 1
    return added


# --------------------------------------------------------------------------- #
# Wire helpers
# --------------------------------------------------------------------------- #
def digest(snap: dict) -> str:
    """Stable content hash — equal digests mean nothing left to exchange."""
    import hashlib
    payload = json.dumps(snap, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
