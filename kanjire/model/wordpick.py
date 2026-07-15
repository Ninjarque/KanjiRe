"""One-shot word sampling from a :class:`GameConfig`.

The card game samples words *per board* through the engine; a typed-recall
session just needs a single fixed set up front. This picks ``n`` words honouring
the same content controls every other mode uses - deck, JLPT levels, and the
learn-bucket mix (known / less-known / unknown) - so Recall is exactly as
tunable as Learn or Survival, from the same menu rows.
"""
from __future__ import annotations

import random

from kanjire.data import db
from kanjire.model.sampling import learn_sample_words, weighted_sample_words

#: The Learn/Survival bucket steps (0-3) as concrete integer weights. Mirrors
#: menu._LEARN_STEPS so the menu selectors mean the same thing here.
_LEARN_STEPS = (0, 1, 3, 6)


def candidate_pool(app, config) -> list:
    """Every word the config could draw from (deck + level filtered)."""
    levels = config.levels or None
    try:
        return db.load_words(app.con, decks=list(config.decks), levels=levels,
                             require_kanji=True)
    except Exception:  # noqa: BLE001 - a bad deck must not crash the caller
        return []


def sample_words(app, config, n: int, *, rng: random.Random | None = None) -> list:
    """Pick *n* words for a session honouring the config's difficulty mix.

    Uses the learn-bucket sampler when the config asks for a mix (as Recall and
    Learn do), otherwise a plain frequency-weighted draw. Returns fewer than *n*
    only when the pool itself is smaller.
    """
    rng = rng or random.Random()
    pool = candidate_pool(app, config)
    if not pool or n <= 0:
        return []
    n = min(n, len(pool))

    try:
        pairs = app.stats.confusion_partners()
    except Exception:  # noqa: BLE001
        pairs = {}

    wants_mix = any((config.learn_known, config.learn_less_known,
                     config.learn_unknown))
    if wants_mix:
        try:
            buckets = app.stats.classify_words(pool)
        except Exception:  # noqa: BLE001
            buckets = {}
        weights = {
            "known":      _LEARN_STEPS[config.learn_known],
            "less_known": _LEARN_STEPS[config.learn_less_known],
            "unknown":    _LEARN_STEPS[config.learn_unknown],
        }
        return learn_sample_words(pool, n, buckets=buckets, weights=weights,
                                  bias=config.frequency_bias, rng=rng,
                                  pair_boost=pairs)
    return weighted_sample_words(pool, n, bias=config.frequency_bias, rng=rng,
                                 pair_boost=pairs)
