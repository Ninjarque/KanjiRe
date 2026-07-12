"""Builds the "Today's Training" session: due reviews + a governed trickle
of new words, with a gentler comeback plan after a break.

Reviews are cross-deck (the schedule follows the word, not the deck it was
played from); new words are scoped to the player's current deck/level
selection so a beginner's Today stays a beginner's Today.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from kanjire.data import db
from kanjire.model.vocab import Word

#: At most this many due reviews per Today session (a 3-5 minute cap, per the
#: session-length research). Anything beyond it silently waits - retrievability
#: sorting guarantees the most at-risk words go first.
REVIEW_TARGET = 30
#: A gap of this many days or more triggers the comeback plan.
COMEBACK_GAP_DAYS = 4
#: The comeback session: just this many most-at-risk words, no new words.
COMEBACK_REVIEWS = 20


@dataclass
class TodayPlan:
    reviews: list[Word] = field(default_factory=list)
    new_words: list[Word] = field(default_factory=list)
    comeback: bool = False

    @property
    def pool(self) -> list[Word]:
        return self.reviews + self.new_words

    @property
    def empty(self) -> bool:
        return not self.reviews and not self.new_words


def days_since_last_activity(stats) -> int | None:
    """Full days since the player's last recorded review; None if never."""
    days = stats.day_counts()
    if not days:
        return None
    last = max(days)  # ISO dates sort lexicographically
    try:
        return (date.today() - date.fromisoformat(last)).days
    except ValueError:
        return None


def build_today_plan(con, stats, *, decks=None, levels=None,
                     now: datetime | None = None) -> TodayPlan:
    """Assemble today's pool from the vocab DB + the player's SRS state.

    ``con`` is the (read-only) vocab connection; ``stats`` the StatsRecorder
    whose ``.srs`` store drives due/new decisions. ``decks``/``levels`` scope
    where NEW words come from (reviews are always cross-deck).
    """
    srs = getattr(stats, "srs", None)
    try:
        all_words = db.load_words(con, require_kanji=True)
    except Exception:
        all_words = []
    by_key = {(w.expression, w.reading): w for w in all_words}

    due_keys = srs.due_keys(now, limit=REVIEW_TARGET) if srs else []
    reviews = [by_key[k] for k in due_keys if k in by_key]

    gap = days_since_last_activity(stats)
    if gap is not None and gap >= COMEBACK_GAP_DAYS and reviews:
        # Welcome back: a normal-sized session of the most at-risk words,
        # nothing new, and no backlog number anywhere.
        return TodayPlan(reviews=reviews[:COMEBACK_REVIEWS], comeback=True)

    cap = srs.new_allowance(now) if srs else 0
    new_words: list[Word] = []
    if cap > 0:
        tracked = srs.tracked_keys() if srs else set()
        scope = all_words
        if decks:
            wanted = set(decks)
            scoped = [w for w in all_words if w.deck in wanted]
            if scoped:
                scope = scoped
        if levels:
            lv = set(levels)
            leveled = [w for w in scope if w.jlpt in lv or w.jlpt is None]
            if leveled:
                scope = leveled
        fresh = [w for w in scope
                 if (w.expression, w.reading) not in tracked]
        fresh.sort(key=lambda w: -w.freq)   # teach common words first
        seen_faces: set[str] = set()
        for w in fresh:
            m = w.meaning.strip().lower()
            if w.reading in seen_faces or m in seen_faces:
                continue                     # keep the batch board-safe
            new_words.append(w)
            seen_faces.add(w.reading)
            seen_faces.add(m)
            if len(new_words) >= cap:
                break
    return TodayPlan(reviews=reviews, new_words=new_words)
