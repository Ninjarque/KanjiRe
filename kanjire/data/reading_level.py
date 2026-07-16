"""Sentence difficulty rating for the reading curriculum.

Every sentence gets two numbers on one scale (higher = harder):

* **average** difficulty — how hard the sentence is *overall*, and
* **peak** difficulty — its single *hardest* word.

The gap between them is the sentence's "spikiness": a smooth N4 sentence has
peak≈average, while an otherwise-easy sentence with one rare word has a high
peak over a low average. The reading room lets the player pick a target level
(the average they want) and how much spikiness they'll tolerate (how far the
peak may sit above the target), which is what "learn more or less aggressively"
means concretely.

The scale is anchored on JLPT — N5=1 … N1=5 — with words that have no JLPT level
placed by frequency (a rare word is "beyond N1", ~5-6). Frequency is the
universal fallback because every word has it while only the JLPT deck has a
level.
"""
from __future__ import annotations

from dataclasses import dataclass

#: JLPT level (5=N5 easiest … 1=N1 hardest) -> difficulty (1=easy … 5=hard).
#: Just ``6 - jlpt``, named so the intent is obvious.
def _jlpt_difficulty(jlpt: int) -> float:
    return float(6 - jlpt)


def _freq_difficulty(zipf: float | None) -> float:
    """Place a word with no JLPT level by how common it is.

    zipf is ~1 (very rare) … ~7 (extremely common). A common word is easy; a
    rare one is harder than N1. Clamped to the JLPT range's neighbourhood so a
    single freak word can't dominate a sentence's average.
    """
    if zipf is None:
        return 4.0                      # unknown frequency: assume mid-hard
    return max(1.0, min(6.0, 7.0 - zipf))


def word_difficulty(jlpt: int | None, zipf: float | None) -> float:
    """One word's difficulty on the 1 (easy) … ~6 (hard) scale."""
    if jlpt:
        return _jlpt_difficulty(jlpt)
    return _freq_difficulty(zipf)


def load_word_difficulty(con) -> dict[str, float]:
    """``headword -> difficulty`` for every vocab word.

    A word can appear in several decks with different JLPT levels; we keep the
    *easiest* (a word you first met as N5 is an N5 word to you, even if a rarer
    sense exists), so a sentence is never rated harder than it reads.
    """
    out: dict[str, float] = {}
    try:
        rows = con.execute(
            "SELECT expression, jlpt, freq FROM words").fetchall()
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        d = word_difficulty(r["jlpt"], r["freq"])
        cur = out.get(r["expression"])
        if cur is None or d < cur:
            out[r["expression"]] = d
    return out


@dataclass(frozen=True)
class SentenceRating:
    average: float          # mean word difficulty
    peak: float             # hardest word difficulty
    hardest: str            # the headword responsible for the peak
    n_words: int            # content words rated

    @property
    def spike(self) -> float:
        """How far the hardest word sits above the average."""
        return self.peak - self.average


def rate_sentence(word_diffs: list[tuple[str, float]]) -> SentenceRating | None:
    """Rate a sentence from its ``(headword, difficulty)`` content words.

    Returns None when there is nothing to rate (a sentence with no content
    words we can place - it has no meaningful difficulty).
    """
    if not word_diffs:
        return None
    peak_head, peak = max(word_diffs, key=lambda wd: wd[1])
    avg = sum(d for _h, d in word_diffs) / len(word_diffs)
    return SentenceRating(average=round(avg, 3), peak=round(peak, 3),
                          hardest=peak_head, n_words=len(word_diffs))


def rate_from_heads(heads, word_diff: dict[str, float]) -> "SentenceRating | None":
    """Rate a sentence from its indexed *heads* using a difficulty map.

    Only kanji-bearing headwords the map can place are rated; a sentence of
    entirely unrated words returns None (unknown difficulty).
    """
    from kanjire.jputil import has_kanji

    diffs = [(h, word_diff[h]) for h in heads
             if has_kanji(h) and h in word_diff]
    return rate_sentence(diffs)


#: The player's chosen reading level maps a target average difficulty and a
#: tolerated spike. "Gentle" wants smooth sentences at-or-below level; "bold"
#: allows a hard word to poke above.
def fits(rating: SentenceRating, *, target: float, spread: float,
         above: float = 0.5) -> bool:
    """True if *rating* suits a player aiming at *target* average difficulty who
    tolerates a peak up to *spread* above target, allowing the average itself to
    drift *above* the target a little (below is always fine - easier is safe)."""
    if rating.average > target + above:
        return False
    return rating.peak <= target + spread
