"""Toolkit-free game-session assembly, shared by the pyglet and Kivy UIs.

This is the pool/sampler/meta-provider/tally wiring that used to live at the
top of the pyglet ``GameScene``; extracted so the Kivy front-end drives the
exact same rules (Learn buckets, confusable pairing, Survival bounties)
without duplicating them. The pyglet scene still carries its own copy until
the parity switchover.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from kanjire import kana
from kanjire.data import db, kanjidata
from kanjire.data.stats import knowledge_score
from kanjire.game.config import GameConfig
from kanjire.game.engine import GameEngine
from kanjire.i18n import tr
from kanjire.model.sampling import (affinity_for, filter_by_genres,
                                    learn_sample_words, weighted_sample_words)

#: Discrete Learn-mode selectors map onto these relative weights.
LEARN_STEPS: dict[int, int] = {0: 0, 1: 1, 2: 3, 3: 6}


class SessionTally:
    """Session-local record of what the player struggled with this game,
    forwarding every event to the app-wide recorder unchanged."""

    def __init__(self, recorder) -> None:
        self._rec = recorder
        self._confused: dict[tuple[str, str], int] = {}
        self._words: dict[tuple[str, str], object] = {}

    def saw(self, word) -> None:
        if self._rec is not None:
            self._rec.saw(word)

    def matched(self, word, clean: bool = True) -> None:
        if self._rec is not None:
            self._rec.matched(word, clean)

    def confused(self, target, offending, face) -> None:
        for w in (target, offending):
            key = (w.expression, w.reading)
            self._confused[key] = self._confused.get(key, 0) + 1
            self._words[key] = w
        if self._rec is not None:
            self._rec.confused(target, offending, face)

    def struggled(self, limit: int = 12) -> list:
        """Words involved in confusions this session, most-confused first."""
        keys = sorted(self._confused, key=lambda k: -self._confused[k])
        return [self._words[k] for k in keys[:limit]]

    def struggled_keys(self) -> set[tuple[str, str]]:
        return set(self._confused)


@dataclass
class Session:
    """A ready-to-start engine plus everything the UI needs around it."""

    engine: GameEngine
    tally: SessionTally
    config: GameConfig
    pool: list = field(default_factory=list)
    error: str | None = None
    is_kana: bool = False


def build_session(con, stats, config: GameConfig, *,
                  pool=None, rng: random.Random | None = None,
                  stats_read=None) -> Session:
    """Assemble (but don't start) a game session.

    *con* is the vocab DB connection, *stats* the app-wide StatsRecorder
    (either may be None only in tests). An explicit *pool* bypasses deck
    loading (rematch flows). *stats_read*, when given, serves the
    BUILD-TIME stats reads (bucket classification, confusion pairs,
    hardest-seen) — a worker thread passes its own thread-local recorder
    here, while *stats* stays the main recorder that the tally and the
    per-deal meta_provider use during play on the UI thread.
    """
    is_kana = kana.KANA_DECK in config.decks
    error = None
    if pool is not None:
        pool = list(pool)
        error = None if pool else tr("NO_WORDS")
    elif is_kana:
        pool = []
    else:
        pool = db.load_words(
            con, decks=list(config.decks), levels=config.levels or None,
            require_kanji=True,
        )
        pool = filter_by_genres(pool, config.genres)
        error = None if pool else tr("NO_WORDS")

    rng = rng or random.Random()
    sr = stats_read if stats_read is not None else stats

    # Confusability fuel for the sampler: historically-confused pairs get
    # re-paired on purpose, and kanji from the same phonetic series (keisei)
    # get juxtaposed so the sound families become visible.
    try:
        pairs = sr.confusion_partners()
    except Exception:
        pairs = {}
    try:
        series = kanjidata.series_map()
    except Exception:
        series = {}
    # The player's own clustering dials (genre / lookalike / soundalike).
    affinity = affinity_for(config)

    if is_kana:
        sampler = lambda pool, n, *, bias, rng, penalize=None: kana.sample(
            n, length=config.kana_length, script=config.kana_script, rng=rng,
        )
    elif any((config.learn_known, config.learn_less_known, config.learn_unknown)):
        buckets = sr.classify_words(pool)
        weights = {
            "known":      LEARN_STEPS[config.learn_known],
            "less_known": LEARN_STEPS[config.learn_less_known],
            "unknown":    LEARN_STEPS[config.learn_unknown],
        }
        sampler = lambda pool, n, *, bias, rng, penalize=None: learn_sample_words(
            pool, n, buckets=buckets, weights=weights, bias=bias, rng=rng,
            penalize=penalize, pair_boost=pairs, series_map=series,
            affinity=affinity,
        )
    else:
        sampler = (
            lambda pool, n, *, bias, rng, penalize=None:
                weighted_sample_words(pool, n, bias=bias, rng=rng,
                                      penalize=penalize, pair_boost=pairs,
                                      series_map=series, affinity=affinity)
        )

    # Survival (lives_mode): per-deal new/bounty metadata from the player's
    # history. "new" = never matched; bounty candidate = the hardest already-
    # learned word on the board that's among their toughest overall.
    meta_provider = None
    if config.lives_mode and not is_kana and stats is not None:
        hard_keys = {
            (r["expression"], r["reading"]) for r in sr.hardest_seen(60)
        }

        def meta_provider(words):
            is_new: list[bool] = []
            scored: list[tuple[float, int]] = []
            for i, w in enumerate(words):
                try:
                    row = stats.get_for(w.expression, w.reading)
                except Exception:
                    row = None
                new = (row is None) or ((row.get("matches") or 0) == 0)
                is_new.append(new)
                if (not new) and (w.expression, w.reading) in hard_keys:
                    scored.append((knowledge_score(row), i))
            scored.sort(key=lambda si: si[0])  # hardest (lowest) first
            cand = scored[0][1] if scored else None
            return is_new, cand

    tally = SessionTally(stats)
    engine = GameEngine(config, pool, rng=rng, recorder=tally,
                        sampler=sampler, meta_provider=meta_provider)
    return Session(engine=engine, tally=tally, config=config,
                   pool=pool, error=error, is_kana=is_kana)
