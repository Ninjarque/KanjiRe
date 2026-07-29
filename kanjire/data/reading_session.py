"""Toolkit-free Reading Room session logic, shared by pyglet and Kivy UIs.

Extracted from the pyglet ReadingScene: the i+1 sentence queue with the
curriculum dials (new-word budget, difficulty ordering), per-source word
lookup, and read logging. The pyglet scene still carries its own copy until
the parity switchover; keep behaviour identical when touching either.
"""
from __future__ import annotations

import random

from kanjire.data import coverage as coverage_mod
from kanjire.data import kanjidata, reading_level
from kanjire.jputil import has_kanji, uncovered_kanji

#: Refill the queue when it runs this low.
REFILL_AT = 5

#: i+1 dial: how many words in a sentence may be new. (value, tr key)
NEW_WORD_OPTIONS = ((0, "READ_NEW_0"), (1, "READ_NEW_1"), (2, "READ_NEW_2"))

#: Pool ordering preference. (value, tr key)
DIFFICULTY_OPTIONS = (("easy", "READ_DIFF_EASY"),
                      ("comfortable", "READ_DIFF_MID"),
                      ("challenging", "READ_DIFF_HARD"))


class ReadingSession:
    def __init__(self, con, stats, state, rng: random.Random | None = None):
        self.con = con
        self.stats = stats
        self.state = state
        self.rng = rng or random.Random()
        self._known = {
            expr for expr, _r in coverage_mod.known_keys(stats)
            if has_kanji(expr)
        }
        self.new_words = int(state.setting("read_new_words", "1") or 1)
        if self.new_words not in {v for v, _ in NEW_WORD_OPTIONS}:
            self.new_words = 1
        self.difficulty = state.setting("read_difficulty", "comfortable")
        if self.difficulty not in {v for v, _ in DIFFICULTY_OPTIONS}:
            self.difficulty = "comfortable"
        self._word_diff = reading_level.load_word_difficulty(con)

        # Sources: Tanaka plus any imported deck that captured sentences.
        self.sources: list[tuple[str, str]] = [("tanaka", "")]
        try:
            for r in con.execute(
                    "SELECT deck, COUNT(*) AS n FROM corpus_sentences "
                    "GROUP BY deck HAVING n > 0"):
                name = (r["deck"][len("corpus:"):].replace("-", " ").title()
                        if r["deck"].startswith("corpus:") else r["deck"])
                self.sources.append((r["deck"], name))
        except Exception:
            pass
        self.source = "tanaka"
        self._read_ids = stats.read_sentence_ids(self.source)
        self._queue: list[dict] = []
        self.current: dict | None = None

    # ---- dials --------------------------------------------------------- #
    def set_source(self, key: str) -> None:
        if key == self.source:
            return
        self.source = key
        self._read_ids = self.stats.read_sentence_ids(key)
        self.requeue()

    def set_new_words(self, n: int) -> None:
        self.new_words = int(n)
        self.state.set_setting("read_new_words", str(n))
        self.requeue()

    def set_difficulty(self, pref: str) -> None:
        self.difficulty = pref
        self.state.set_setting("read_difficulty", pref)
        self.requeue()

    def requeue(self) -> None:
        """A dial changed: rebuild the queue and surface a fresh sentence.

        The current sentence is dropped WITHOUT being counted — changing a
        dial is not reading.
        """
        from kanjire import readdiag
        readdiag.note("requeue", dropped=None if self.current is None
                      else self.current["id"], source=self.source)
        self._queue.clear()
        self.current = None
        self.advance(log=False, reason="requeue")

    # ---- pool ---------------------------------------------------------- #
    def _order_pool(self, pool: list[dict]) -> list[dict]:
        """Rate a readable shortlist and order it by the preference; unrated
        sentences sort to the middle so they neither lead nor vanish."""
        for s in pool:
            heads = [h for (h, _r, _g) in self.words_of(s["id"])]
            r = reading_level.rate_from_heads(heads, self._word_diff)
            s["avg"] = r.average if r else None
            s["peak"] = r.peak if r else None
        rated = [s for s in pool if s["avg"] is not None]
        if not rated:
            return pool
        avgs = sorted(s["avg"] for s in rated)
        median = avgs[len(avgs) // 2]

        def key(s):
            a = s["avg"]
            if a is None:
                a = median
            if self.difficulty == "easy":
                return (a, s["peak"] or a)
            if self.difficulty == "challenging":
                return (-a, -(s["peak"] or a))
            return (abs(a - median), s["peak"] or a)

        return sorted(pool, key=key)

    def words_of(self, sentence_id: int):
        """Indexed (headword, reading, good) for the current source."""
        if self.source == "tanaka":
            return kanjidata.words_of(sentence_id)
        try:
            return [(r["headword"], r["reading"], False)
                    for r in self.con.execute(
                        "SELECT headword, reading FROM corpus_sentence_words "
                        "WHERE sentence_id=?", (sentence_id,))]
        except Exception:
            return []

    def _corpus_sentences(self, max_unknown: int,
                          exclude: set[int]) -> list[dict]:
        out: list[dict] = []
        try:
            rows = self.con.execute(
                "SELECT id, ja FROM corpus_sentences "
                "WHERE deck=? AND n_kanji_words > 0", (self.source,)
            ).fetchall()
            for r in rows:
                if r["id"] in exclude:
                    continue
                words = self.con.execute(
                    "SELECT headword FROM corpus_sentence_words "
                    "WHERE sentence_id=?", (r["id"],)).fetchall()
                heads = [w["headword"] for w in words]
                kanji_words = [h for h in heads if has_kanji(h)]
                if not kanji_words:
                    continue
                unknown = sum(1 for h in kanji_words if h not in self._known)
                # Kanji the indexer dropped (names, unresolved tokens) count
                # as unknown too — otherwise a name-heavy sentence claims you
                # know every word on the strength of one common one.
                if uncovered_kanji(r["ja"], heads):
                    unknown += 1
                if unknown <= max_unknown:
                    out.append({"id": r["id"], "ja": r["ja"], "en": "",
                                "unknown": unknown})
        except Exception:
            return []
        out.sort(key=lambda s: (s["unknown"], self.rng.random()))
        return out[:40]

    def _refill(self) -> None:
        exclude = self._read_ids | {s["id"] for s in self._queue}
        if self.current:
            exclude.add(self.current["id"])
        # Try the chosen budget first, then loosen by one so the room never
        # runs dry when the exact setting is momentarily empty.
        budgets = [self.new_words]
        if self.new_words < 2:
            budgets.append(self.new_words + 1)
        for max_unknown in budgets:
            if self.source == "tanaka":
                got = kanjidata.readable_sentences(
                    self._known, max_unknown=max_unknown, limit=60,
                    exclude_ids=exclude, rng=self.rng)
            else:
                got = self._corpus_sentences(max_unknown, exclude)
            if got:
                self._queue.extend(self._order_pool(got))
                return

    # ---- flow ---------------------------------------------------------- #
    def advance(self, log: bool = True, reason: str = "?") -> dict | None:
        """Log the current sentence (if any) and surface the next one.

        *reason* is only for the trace log — every caller names itself, so a
        counter that moves without the Next button says so out loud.
        """
        from kanjire import readdiag
        if log and self.current is not None:
            try:
                self.stats.log_read(self.current["id"],
                                    len(self.current["ja"]),
                                    source=self.source, reason=reason)
            except Exception:
                pass
            self._read_ids.add(self.current["id"])
        else:
            readdiag.note("advance-nolog", reason=reason,
                          had=None if self.current is None
                          else self.current["id"])
        if len(self._queue) <= REFILL_AT:
            self._refill()
        self.current = self._queue.pop(0) if self._queue else None
        return self.current

    def current_words(self) -> list[tuple[str, str | None, bool]]:
        if self.current is None:
            return []
        return self.words_of(self.current["id"])

    def is_known(self, head: str) -> bool:
        return head in self._known

    def level_tag(self) -> int | None:
        """The current sentence's ~JLPT level (1..5 = N1..N5), or None."""
        avg = (self.current or {}).get("avg")
        if avg is None:
            return None
        return max(1, min(5, 6 - round(avg)))

    # ---- word popup data ------------------------------------------------ #
    def vocab_word(self, head: str, reading: str | None) -> dict | None:
        try:
            q = "SELECT * FROM words WHERE expression=?"
            args = [head]
            if reading:
                q += " AND reading=?"
                args.append(reading)
            q += " ORDER BY CASE WHEN deck='jlpt' THEN 0 ELSE 1 END LIMIT 1"
            row = self.con.execute(q, args).fetchone()
            if row is None and reading:
                row = self.con.execute(
                    "SELECT * FROM words WHERE expression=? LIMIT 1",
                    (head,)).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def enqueue_learn(self, head: str, reading: str) -> None:
        try:
            if self.stats.srs is not None:
                self.stats.srs.enqueue_new(head, reading)
        except Exception:
            pass
