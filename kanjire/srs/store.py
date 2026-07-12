"""FSRS-backed per-word scheduling state.

Every graded event (a clean match, a match after an error, a confusion)
updates one FSRS card per ``(expression, reading)``. The card's *due* date
and predicted *retrievability* then drive the Today session: which words to
review, which are most at risk after a break, and how many new words the
player can absorb without building review debt.

Design rules (see docs/ROADMAP.md):

* **No review debt.** Nothing here ever surfaces an "overdue count"; overdue
  cards just have lower retrievability and sort first.
* **Recognition is weak evidence.** Board matches rate at most Good; a card
  never rates Easy from the matching game alone (typed recall will).
* **Graceful without fsrs.** If the ``fsrs`` package is missing (stripped
  build, dev env), the store becomes a no-op and the game plays exactly as
  before — scheduling is an enhancement, never a dependency of the core loop.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def _local_today() -> str:
    """Player-local calendar date (same convention as the review_log)."""
    return datetime.now().astimezone().date().isoformat()

try:  # soft dependency: everything degrades to no-op without it
    from fsrs import Card, Rating, Scheduler
    HAVE_FSRS = True
except Exception:  # pragma: no cover - exercised only in stripped envs
    Card = Rating = Scheduler = None  # type: ignore[assignment]
    HAVE_FSRS = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS srs_state (
    expression  TEXT NOT NULL,
    reading     TEXT NOT NULL,
    state       INTEGER NOT NULL,
    step        INTEGER,
    stability   REAL,
    difficulty  REAL,
    due         TEXT NOT NULL,      -- UTC ISO-8601
    last_review TEXT,
    lapses      INTEGER NOT NULL DEFAULT 0,
    created_day TEXT,               -- player-local day the word entered the SRS
    PRIMARY KEY (expression, reading)
);
CREATE INDEX IF NOT EXISTS idx_srs_due ON srs_state(due);
"""

#: Ratings the rest of the app uses (mirrors fsrs.Rating values so the store
#: can be driven without importing fsrs at call sites).
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4

#: Daily workload governor. The player comfortably absorbs about this many
#: brand-new words a day; the allowance shrinks as the due pile grows so a
#: heavy review day never also becomes a heavy learning day.
NEW_TARGET_PER_DAY = 10
#: Above this many due reviews, no new words are introduced at all.
DUE_SOFT_CEILING = 60


class SrsStore:
    """Owns the ``srs_state`` table on an existing stats-DB connection."""

    def __init__(self, con: sqlite3.Connection,
                 desired_retention: float = 0.9) -> None:
        self.con = con
        self.enabled = HAVE_FSRS
        con.executescript(_SCHEMA)
        try:  # additive migration for tables created before created_day
            con.execute("ALTER TABLE srs_state ADD COLUMN created_day TEXT")
        except sqlite3.OperationalError:
            pass
        con.commit()
        if self.enabled:
            # Learning steps are short on purpose: a fresh word rated Good
            # comes due again within the same play session, mimicking Anki's
            # intra-session learning steps.
            self.scheduler = Scheduler(
                desired_retention=desired_retention,
                learning_steps=(timedelta(minutes=1), timedelta(minutes=10)),
                relearning_steps=(timedelta(minutes=10),),
                enable_fuzzing=True,
            )
        else:
            self.scheduler = None

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def _load_card(self, expression: str, reading: str):
        row = self.con.execute(
            "SELECT * FROM srs_state WHERE expression=? AND reading=?",
            (expression, reading),
        ).fetchone()
        if row is None:
            return Card(), 0
        d = dict(row)
        return Card(
            card_id=abs(hash((expression, reading))) & 0x7FFFFFFF,
            state=d["state"], step=d["step"],
            stability=d["stability"], difficulty=d["difficulty"],
            due=datetime.fromisoformat(d["due"]),
            last_review=(datetime.fromisoformat(d["last_review"])
                         if d["last_review"] else None),
        ), d.get("lapses") or 0

    def _save_card(self, expression: str, reading: str, card, lapses: int) -> None:
        # created_day is written on first insert only (the conflict branch
        # leaves it alone) - it drives the per-day new-word allowance.
        self.con.execute(
            """
            INSERT INTO srs_state (expression, reading, state, step, stability,
                                   difficulty, due, last_review, lapses,
                                   created_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(expression, reading) DO UPDATE SET
              state=excluded.state, step=excluded.step,
              stability=excluded.stability, difficulty=excluded.difficulty,
              due=excluded.due, last_review=excluded.last_review,
              lapses=excluded.lapses
            """,
            (expression, reading, int(card.state), card.step,
             card.stability, card.difficulty,
             card.due.isoformat(),
             card.last_review.isoformat() if card.last_review else None,
             lapses, _local_today()),
        )

    def update(self, expression: str, reading: str, rating: int,
               ts: datetime | None = None) -> None:
        """Grade one recall event. Never raises (scheduling must not be able
        to break gameplay); commits its own write like the stats recorder."""
        if not self.enabled:
            return
        try:
            card, lapses = self._load_card(expression, reading)
            when = ts or datetime.now(timezone.utc)
            card, _log = self.scheduler.review_card(card, Rating(rating), when)
            if rating == AGAIN:
                lapses += 1
            self._save_card(expression, reading, card, lapses)
            self.con.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Queries for session building
    # ------------------------------------------------------------------ #
    def due_keys(self, now: datetime | None = None,
                 limit: int = 200) -> list[tuple[str, str]]:
        """Words due for review, most at-risk (lowest retrievability) first.

        Overdue cards are not treated specially — an old due date simply
        means lower retrievability, which floats the word to the front."""
        if not self.enabled:
            return []
        now = now or datetime.now(timezone.utc)
        rows = self.con.execute(
            "SELECT * FROM srs_state WHERE due <= ? ORDER BY due ASC LIMIT ?",
            (now.isoformat(), limit * 3),
        ).fetchall()
        scored = []
        for r in rows:
            d = dict(r)
            try:
                card, _ = self._load_card(d["expression"], d["reading"])
                risk = self.scheduler.get_card_retrievability(card, now)
            except Exception:
                risk = 0.0
            scored.append((risk, (d["expression"], d["reading"])))
        scored.sort(key=lambda t: t[0])
        return [key for _, key in scored[:limit]]

    def due_count(self, now: datetime | None = None) -> int:
        if not self.enabled:
            return 0
        now = now or datetime.now(timezone.utc)
        row = self.con.execute(
            "SELECT COUNT(*) AS n FROM srs_state WHERE due <= ?",
            (now.isoformat(),),
        ).fetchone()
        return row["n"] if row else 0

    def tracked_keys(self) -> set[tuple[str, str]]:
        """Every word the scheduler has state for (used to find NEW words)."""
        return {
            (r["expression"], r["reading"])
            for r in self.con.execute(
                "SELECT expression, reading FROM srs_state")
        }

    def leech_keys(self, min_lapses: int = 6,
                   limit: int = 30) -> list[tuple[str, str]]:
        """Chronic offenders, most-lapsed first (bounty-hunt fodder)."""
        return [
            (r["expression"], r["reading"])
            for r in self.con.execute(
                "SELECT expression, reading FROM srs_state "
                "WHERE lapses >= ? ORDER BY lapses DESC LIMIT ?",
                (min_lapses, limit),
            )
        ]

    def introduced_today(self) -> int:
        """How many brand-new words already entered the SRS today."""
        row = self.con.execute(
            "SELECT COUNT(*) AS n FROM srs_state WHERE created_day = ?",
            (_local_today(),),
        ).fetchone()
        return row["n"] if row else 0

    def new_allowance(self, now: datetime | None = None) -> int:
        """How many brand-new words today's session may still introduce.

        Shrinks linearly with the due pile (0 due -> NEW_TARGET_PER_DAY,
        DUE_SOFT_CEILING+ due -> 0), minus what today already introduced.
        This is the structural no-review-debt guarantee: learning pauses by
        itself while reviews catch up, and finishing Today's Training doesn't
        immediately refill it with more new words."""
        if not self.enabled:
            return NEW_TARGET_PER_DAY
        due = self.due_count(now)
        if due >= DUE_SOFT_CEILING:
            base = 0
        else:
            base = round(NEW_TARGET_PER_DAY * (1 - due / DUE_SOFT_CEILING))
        return max(0, base - self.introduced_today())

    def seed_known(self, keys, *, rng=None, spread_days: int = 45) -> int:
        """Bulk-mark words as already known (placement / "I know these").

        Creates review-state cards with a healthy stability and due dates
        spread uniformly over the next *spread_days*, so seeding 600 words
        trickles them into Today reviews instead of dumping a 600-card pile
        (the no-review-debt rule applies to placement too). Words that
        already have SRS state are left untouched. Returns how many seeded.
        """
        if not self.enabled:
            return 0
        import random as _random
        rng = rng or _random.Random()
        now = datetime.now(timezone.utc)
        existing = self.tracked_keys()
        n = 0
        for expression, reading in keys:
            if (expression, reading) in existing:
                continue
            due = now + timedelta(days=rng.uniform(1.0, float(spread_days)))
            card = Card(
                card_id=abs(hash((expression, reading))) & 0x7FFFFFFF,
                state=2,                    # Review
                step=None,
                stability=float(spread_days),
                difficulty=5.0,             # mid-scale; adapts from here
                due=due,
                last_review=now,
            )
            self._save_card(expression, reading, card, 0)
            n += 1
        self.con.commit()
        return n

    def reset_word(self, expression: str, reading: str) -> None:
        self.con.execute(
            "DELETE FROM srs_state WHERE expression=? AND reading=?",
            (expression, reading),
        )
        self.con.commit()

    def reset_all(self) -> None:
        self.con.execute("DELETE FROM srs_state")
        self.con.commit()
