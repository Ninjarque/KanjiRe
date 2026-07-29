"""The diglot-weave reader: your own text, mostly in Japanese.

Read a passage in Japanese; tap a word you don't know and it opens up
(reading + meaning) *and* every later appearance of that word switches to
English for a while, then quietly switches back once you've had enough
exposure. Words that are both unfamiliar and hard start in English without
being asked.

Three pieces live here, all toolkit-free so both UIs share them:

* **Tokenising** — splitting a passage into sentences and words. NOT with
  MeCab: the Android package is ``python3, kivy, paho-mqtt, certifi,
  pynacl``, so a morphological analyser simply is not there. Instead a
  longest-match pass over the bundled vocabulary finds the words we have
  entries for — which is exactly the set we could gloss or swap anyway.
* **The library** — passages the player added, with where they left off, so
  a novel survives closing the app.
* **The weave rule** — how many future appearances of a word stay English,
  derived from what the stats layer already knows about it.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from kanjire.data.stats import classify
from kanjire.jputil import has_kanji

#: Sentence enders. Newlines split too — pasted prose often has no 。 at all.
_END = "。．.!?！？\n"
#: The longest headword we will try to match at one position. The vocabulary
#: tops out well below this; it only bounds the inner loop.
MAX_WORD = 12
#: A passage longer than this is truncated on import — a whole novel would
#: make the reader unusable long before it made it slow.
MAX_CHARS = 200_000


# --------------------------------------------------------------------------- #
# Tokenising
# --------------------------------------------------------------------------- #
def split_sentences(text: str) -> list[str]:
    """A passage as sentences, punctuation kept, blanks dropped."""
    out: list[str] = []
    buf: list[str] = []
    for ch in (text or "")[:MAX_CHARS]:
        buf.append(ch)
        if ch in _END:
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


@dataclass
class Token:
    """One run of text, either a known word or a stretch we can't name."""
    text: str                       #: exactly as it appears in the passage
    expression: str = ""            #: dictionary form, when we matched one
    reading: str = ""
    meaning: str = ""
    jlpt: int | None = None

    @property
    def known_word(self) -> bool:
        return bool(self.expression)

    @property
    def key(self) -> tuple[str, str]:
        return (self.expression, self.reading)


class Lexicon:
    """Longest-match word index over the vocabulary DB.

    Built once and reused: a passage is re-tokenised whenever the reader
    reopens it, and rebuilding this per sentence made that visibly slow.
    """

    def __init__(self, con: sqlite3.Connection, decks=None) -> None:
        self._by_len: dict[int, dict[str, tuple]] = {}
        self._max = 1
        try:
            rows = con.execute(
                "SELECT expression, reading, meaning, jlpt FROM words"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for row in rows:
            expr = row["expression"] if hasattr(row, "keys") else row[0]
            if not expr or len(expr) > MAX_WORD:
                continue
            # Entries carry decorations the passage never will (～, spaces,
            # parenthesised notes); those can't be matched, so skip them.
            if any(c in expr for c in "～~ ()（）"):
                continue
            data = (expr,
                    row["reading"] if hasattr(row, "keys") else row[1],
                    row["meaning"] if hasattr(row, "keys") else row[2],
                    row["jlpt"] if hasattr(row, "keys") else row[3])
            slot = self._by_len.setdefault(len(expr), {})
            # Prefer the easier (higher JLPT number) reading of a homograph:
            # a beginner meeting 生 wants "life", not the N1 sense.
            prev = slot.get(expr)
            if prev is None or (data[3] or 0) > (prev[3] or 0):
                slot[expr] = data
            self._max = max(self._max, len(expr))

    def match_at(self, text: str, i: int) -> tuple | None:
        """The longest entry starting at *i*, or None."""
        for span in range(min(self._max, len(text) - i), 0, -1):
            hit = self._by_len.get(span, {}).get(text[i:i + span])
            if hit is not None:
                return hit
        return None

    def tokenize(self, text: str) -> list[Token]:
        """*text* as tokens: matched words, and the gaps between them.

        Gaps are kept as plain tokens (particles, inflections, punctuation)
        so the passage still reads as itself — this is a reading aid, not a
        parse tree.
        """
        out: list[Token] = []
        gap: list[str] = []
        i, n = 0, len(text)
        while i < n:
            hit = self.match_at(text, i)
            # A single kana "word" (と, に, の …) is almost always a particle
            # here, not the vocabulary entry of the same shape — matching it
            # would make every sentence a wall of tappable noise.
            if hit is not None and (len(hit[0]) > 1 or has_kanji(hit[0])):
                if gap:
                    out.append(Token("".join(gap)))
                    gap = []
                out.append(Token(hit[0], hit[0], hit[1] or "", hit[2] or "",
                                 hit[3]))
                i += len(hit[0])
                continue
            gap.append(text[i])
            i += 1
        if gap:
            out.append(Token("".join(gap)))
        return out


# --------------------------------------------------------------------------- #
# The weave rule
# --------------------------------------------------------------------------- #
#: How many later appearances stay English, by knowledge bucket. A word you
#: have never seen needs the crutch for longer than one you merely lapsed.
_BASE = {"unknown": 6, "less_known": 3, "known": 1}
#: Harder words (lower JLPT number) hold the crutch longer.
_JLPT_BONUS = {1: 4, 2: 3, 3: 2, 4: 1, 5: 0, None: 2}
#: Every re-tap means it didn't stick — extend, up to a ceiling.
_RETAP_BONUS = 2
MAX_HOLD = 14


def hold_for(row: dict | None, jlpt: int | None, taps: int = 0) -> int:
    """How many future appearances of a word should read as English.

    Driven entirely by what we already record about the word: its knowledge
    bucket, its JLPT level, and how many times the player has had to tap it.
    """
    base = _BASE.get(classify(row), 6)
    hold = base + _JLPT_BONUS.get(jlpt, 2) + _RETAP_BONUS * max(0, taps)
    return max(1, min(MAX_HOLD, hold))


def starts_in_english(row: dict | None, jlpt: int | None) -> bool:
    """True for words that are both unfamiliar AND hard.

    These get the crutch without being asked for it, so a passage above the
    player's level is readable from the first line instead of a wall of taps.
    """
    return classify(row) == "unknown" and (jlpt or 5) <= 2


@dataclass
class WeaveState:
    """Per-word crutch counters for one reading session.

    Persisted through :class:`Library` so a novel resumed tomorrow remembers
    which words were still in English.
    """
    holds: dict[tuple[str, str], int] = field(default_factory=dict)
    taps: dict[tuple[str, str], int] = field(default_factory=dict)

    def tapped(self, token: Token, row: dict | None) -> int:
        """Register a tap; returns how long the word now stays English."""
        key = token.key
        self.taps[key] = self.taps.get(key, 0) + 1
        hold = hold_for(row, token.jlpt, self.taps[key] - 1)
        self.holds[key] = hold
        return hold

    def show_english(self, token: Token, row: dict | None) -> bool:
        if not token.known_word:
            return False
        if self.holds.get(token.key, 0) > 0:
            return True
        return (token.key not in self.taps
                and starts_in_english(row, token.jlpt))

    def consume(self, token: Token) -> None:
        """Count one appearance against the crutch, and drop it at zero."""
        key = token.key
        left = self.holds.get(key)
        if left:
            if left <= 1:
                self.holds.pop(key, None)
            else:
                self.holds[key] = left - 1
