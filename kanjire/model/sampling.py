"""Frequency-weighted, collision-free selection of words for a round.

Two concerns are handled here:

1. **Weighting** - more frequent words should appear more often. ``freq`` is a
   zipf-like value (``log10`` of relative frequency, +9 offset), so the *true*
   relative frequency is proportional to ``10 ** freq``. A ``bias`` knob in
   ``[0, 1]`` interpolates between uniform (0.0) and true-frequency (1.0)
   sampling, letting common words dominate without crowding out variety.

2. **Solvability** - within a single round every card face must be unique, or
   the player could not tell which reading/meaning belongs to which kanji. We
   therefore reject any candidate that collides with an already-chosen word on
   its expression, reading or meaning.
"""
from __future__ import annotations

import random
from collections.abc import Sequence

from kanjire.model.vocab import Word

#: Buckets that are *review* (already-seen) rather than fresh learning. These
#: are sampled uniformly (bias 0), so review cycles evenly through what you
#: know instead of fixating on the most frequent word (e.g. 見る) every round.
REVIEW_BUCKETS = ("known", "less_known")
#: Recently-shown words get this *absolute* tiny weight (not a multiplier — a
#: multiplier can't overcome a hugely-frequent word's exponential weight). It
#: sits below any normal word (weight >= 1), so penalised words are only ever
#: chosen when nothing else is left — graceful, never an empty board.
_RECENT_PENALTY = 0.01


def _weight(word: Word, bias: float) -> float:
    # 10 ** (freq * bias): bias=1 -> proportional to real frequency,
    # bias=0 -> every word weight 1 (uniform).
    return 10.0 ** (max(word.freq, 0.0) * bias)


def _dedupe_by_face(words: Sequence[Word]) -> list[Word]:
    out: list[Word] = []
    seen_e, seen_r, seen_m = set(), set(), set()
    for w in words:
        m = w.meaning.strip().lower()
        if w.expression in seen_e or w.reading in seen_r or m in seen_m:
            continue
        out.append(w)
        seen_e.add(w.expression)
        seen_r.add(w.reading)
        seen_m.add(m)
    return out


def learn_sample_words(
    pool: Sequence[Word],
    n: int,
    *,
    buckets: dict[str, Sequence[Word]],
    weights: dict[str, int],
    bias: float = 0.4,
    rng: random.Random | None = None,
    penalize: frozenset[tuple[str, str]] | None = None,
) -> list[Word]:
    """Pick *n* words honouring a per-bucket mix.

    ``buckets`` maps ``"known" | "less_known" | "unknown"`` to subsets of pool.
    ``weights`` are integer shares for each bucket (0 = none).

    Review buckets (already-seen) are sampled uniformly; the *unknown* (fresh)
    bucket keeps the frequency *bias* so common new words are taught first.
    ``penalize`` (recently-shown keys) is passed through to avoid repeats.

    Gracefully falls back if a requested bucket is short: the leftover quota is
    redistributed to whatever non-empty buckets remain.
    """
    rng = rng or random
    penalize = penalize or frozenset()
    if n <= 0:
        return []
    total = sum(weights.values())
    if total <= 0:
        return list(weighted_sample_words(pool, n, bias=bias, rng=rng,
                                          penalize=penalize))

    # Initial target counts proportional to weights, with rounding fixed up.
    targets: dict[str, int] = {b: int(round(n * w / total)) for b, w in weights.items()}
    diff = n - sum(targets.values())
    if diff:
        order = sorted(weights, key=lambda b: -weights[b])
        i = 0
        while diff != 0 and order:
            b = order[i % len(order)]
            targets[b] += 1 if diff > 0 else -1
            diff += -1 if diff > 0 else 1
            i += 1

    selected: list[Word] = []
    leftover = 0
    used_keys: set[tuple[str, str]] = set()
    for b, target in targets.items():
        if target <= 0:
            continue
        avail = [w for w in buckets.get(b, ())
                 if (w.expression, w.reading) not in used_keys]
        if not avail:
            leftover += target
            continue
        take = min(target, len(avail))
        leftover += target - take
        # Review buckets sample uniformly; only fresh words honour frequency.
        b_bias = 0.0 if b in REVIEW_BUCKETS else bias
        chosen = weighted_sample_words(avail, take, bias=b_bias, rng=rng,
                                       penalize=penalize)
        for w in chosen:
            used_keys.add((w.expression, w.reading))
        selected.extend(chosen)

    # Cross-bucket dedup on face collisions.
    selected = _dedupe_by_face(selected)
    shortfall = n - len(selected)

    # Backfill from the whole pool if buckets were short or we deduped some out.
    if shortfall > 0:
        used_keys = {(w.expression, w.reading) for w in selected}
        remainder = [w for w in pool
                     if (w.expression, w.reading) not in used_keys]
        more = weighted_sample_words(remainder, shortfall, bias=bias, rng=rng,
                                     penalize=penalize)
        selected.extend(_dedupe_by_face([*selected, *more])[len(selected):])

    rng.shuffle(selected)
    return selected[:n]


def weighted_sample_words(
    pool: Sequence[Word],
    n: int,
    *,
    bias: float = 0.4,
    rng: random.Random | None = None,
    penalize: frozenset[tuple[str, str]] | None = None,
) -> list[Word]:
    """Pick up to *n* distinct, mutually-unambiguous words from *pool*.

    Uses the Efraimidis-Spirakis algorithm to produce a weighted random
    permutation, then greedily accepts words whose faces do not collide.

    ``penalize`` is a set of ``(expression, reading)`` keys to strongly
    down-weight (recently-shown words), so they rarely reappear back-to-back
    but can still be chosen if the pool would otherwise run dry.
    """
    rng = rng or random
    if n <= 0 or not pool:
        return []
    penalize = penalize or frozenset()

    # Weighted random permutation: key = U ** (1/weight), take largest keys.
    keyed = []
    for w in pool:
        if (w.expression, w.reading) in penalize:
            weight = _RECENT_PENALTY        # absolute floor — sink to the bottom
        else:
            weight = _weight(w, bias)
        keyed.append((rng.random() ** (1.0 / max(weight, 1e-9)), w))
    keyed.sort(key=lambda kw: kw[0], reverse=True)

    chosen: list[Word] = []
    seen_expr: set[str] = set()
    seen_reading: set[str] = set()
    seen_meaning: set[str] = set()

    for _, w in keyed:
        if len(chosen) >= n:
            break
        norm_meaning = w.meaning.strip().lower()
        if (
            w.expression in seen_expr
            or w.reading in seen_reading
            or norm_meaning in seen_meaning
        ):
            continue
        chosen.append(w)
        seen_expr.add(w.expression)
        seen_reading.add(w.reading)
        seen_meaning.add(norm_meaning)

    return chosen
