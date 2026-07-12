"""Frequency-weighted vocabulary coverage: the app's honest progress metric.

"You can recognize ~X% of everyday vocabulary" — where X weights every word
by how often it actually occurs (``10 ** zipf``), so knowing 100 common words
counts for far more than knowing 100 rare ones. For an imported corpus deck
the weight is the word's real occurrence count in that text, which makes the
number exact for the material the player cares about.

This is deliberately a statement about *vocabulary tokens*, not full running
text (grammar, names and kana-only words aren't counted) — the UI copy says
"everyday vocabulary", not "any text".
"""
from __future__ import annotations

from kanjire.data import db
from kanjire.data.stats import classify


def _weight(word) -> float:
    if word.count:                      # corpus decks: true occurrences
        return float(word.count)
    return 10.0 ** max(word.freq, 0.0)  # zipf-like -> relative frequency


def known_keys(stats) -> set[tuple[str, str]]:
    """Keys of every word the player currently classifies as *known*."""
    return {
        (r["expression"], r["reading"])
        for r in stats.all_rows()
        if classify(r) == "known"
    }


def deck_coverage(con, known: set[tuple[str, str]], deck: str) -> dict:
    """Coverage of one deck: {pct, known_n, total_n, next_milestone_words}.

    ``next_milestone_words`` estimates how many of the most frequent unknown
    words would lift coverage to the next 5%-step — the goal-gradient hook
    ("23 words to 80%").
    """
    words = db.load_words(con, decks=[deck], require_kanji=True)
    if not words:
        return {"pct": 0.0, "known_n": 0, "total_n": 0,
                "next_milestone_words": 0, "next_milestone_pct": 0}
    total = sum(_weight(w) for w in words)
    have = sum(_weight(w) for w in words
               if (w.expression, w.reading) in known)
    pct = 100.0 * have / total if total else 0.0

    milestone = min(100.0, (int(pct / 5) + 1) * 5.0)
    needed = 0
    if milestone < 100.0:
        gap = (milestone / 100.0) * total - have
        unknown = sorted(
            (w for w in words if (w.expression, w.reading) not in known),
            key=_weight, reverse=True,
        )
        acc = 0.0
        for w in unknown:
            if acc >= gap:
                break
            acc += _weight(w)
            needed += 1
    return {
        "pct": pct,
        "known_n": sum(1 for w in words
                       if (w.expression, w.reading) in known),
        "total_n": len(words),
        "next_milestone_pct": int(milestone),
        "next_milestone_words": needed,
    }


def all_coverage(con, stats, *, max_decks: int = 4) -> list[tuple[str, dict]]:
    """(deck_name, coverage) for the JLPT deck plus imported corpora."""
    known = known_keys(stats)
    out: list[tuple[str, dict]] = []
    try:
        decks = [r["name"] for r in db.list_decks(con)]
    except Exception:
        decks = []
    ordered = [d for d in decks if d == "jlpt"] + \
              [d for d in decks if d.startswith("corpus:")]
    for name in ordered[:max_decks]:
        try:
            out.append((name, deck_coverage(con, known, name)))
        except Exception:
            continue
    return out
