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
   its expression, reading, or *any single sense* of its meaning.

3. **Confusability** - a board of mutually-unrelated words is a weak
   recognition test (any half-remembered cue solves it). Once a word is
   chosen, candidates that *share a kanji* with it (and, mildly, same-JLPT
   words) are boosted, so boards trend toward genuinely confusable sets -
   recognition hardened toward recall.

4. **Clustering** - the same mechanism, but under the player's control: an
   :class:`Affinity` carries their genre / lookalike / soundalike dials and
   the precomputed maps behind them, so a board can be themed ("all food"),
   visually treacherous (待 持 侍 時), or an ear-training trap. These are
   boosts, never filters: an over-tuned dial makes a board harder to fill,
   never impossible.
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from kanjire.jputil import kanji_chars
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
#: Multiplier for candidates sharing a kanji character with an already-chosen
#: word (the confusability axis: 食べる on the board pulls in 食事).
_KANJI_SHARE_BOOST = 8.0
#: Mild multiplier for candidates at the same JLPT level as a chosen word,
#: keeping boards roughly level-coherent without forcing it.
_LEVEL_MATCH_BOOST = 1.5
#: Strong multiplier for a candidate the player has *historically confused*
#: with an already-chosen word — deliberately re-testing old confusions is
#: the highest-value distractor there is.
_PAIR_BOOST = 30.0
#: Multiplier for candidates whose kanji share a *phonetic series* (keisei)
#: with a chosen word's kanji (晴 on the board pulls in 清/精 — same 青=せい
#: sound family). Teaches the series by juxtaposition.
_SERIES_BOOST = 5.0

#: The player-facing affinity dials (0-3) as multipliers.
#:
#: These have to be *big*. Frequency weighting spans orders of magnitude
#: (``10 ** (freq * bias)`` reaches ~10**2.8 for the commonest words), so the
#: first draft's 15x boost was quietly losing to "this other word is simply
#: more common" — measured on the real deck, the themed boards came out no
#: more themed than random ones. "Some" now roughly cancels the frequency
#: spread and "Many" overwhelms it, while still being a boost and not a
#: filter: a board short on matches falls back to unrelated words instead of
#: coming up empty.
AFFINITY_STEPS: tuple[float, ...] = (1.0, 8.0, 60.0, 500.0)


@dataclass(frozen=True)
class Affinity:
    """The clustering pull applied while a board is assembled.

    Each dial is 0-3 (None/Few/Some/Many) and needs its matching map to do
    anything; a dial without data is silently inert, which is exactly what an
    imported deck with no precomputed genres should feel like.
    """
    #: (expression, reading) -> genre keys
    genre_map: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict)
    #: kanji -> kanji that look alike
    shape_map: dict[str, set[str]] = field(default_factory=dict)
    #: (expression, reading) -> keys that sound alike
    sound_map: dict[tuple[str, str], set[tuple[str, str]]] = field(
        default_factory=dict)
    meaning: int = 0
    looks: int = 0
    sound: int = 0

    @property
    def active(self) -> bool:
        return bool((self.meaning and self.genre_map)
                    or (self.looks and self.shape_map)
                    or (self.sound and self.sound_map))


#: Shared empty instance — the overwhelmingly common case.
NO_AFFINITY = Affinity()


def affinity_for(config) -> Affinity:
    """Build the :class:`Affinity` a :class:`~kanjire.game.config.GameConfig`
    asks for, loading only the cluster maps its dials actually use.

    Returns :data:`NO_AFFINITY` when every dial is off or the clustering
    sidecar is absent, so nothing here can fail a game launch.
    """
    meaning = int(getattr(config, "aff_meaning", 0) or 0)
    looks = int(getattr(config, "aff_looks", 0) or 0)
    sound = int(getattr(config, "aff_sound", 0) or 0)
    if not (meaning or looks or sound):
        return NO_AFFINITY
    try:
        from kanjire.data import clusters
        return Affinity(
            genre_map=clusters.genre_index() if meaning else {},
            shape_map=clusters.shape_map() if looks else {},
            sound_map=clusters.sound_map() if sound else {},
            meaning=meaning, looks=looks, sound=sound,
        )
    except Exception:  # noqa: BLE001 — clustering is a bonus, never a blocker
        return NO_AFFINITY


def filter_by_genres(pool: Sequence[Word], genres) -> list[Word]:
    """The words of *pool* belonging to any of *genres*.

    An empty genre selection returns the pool untouched. So does a selection
    that matches nothing at all in this pool — a deck with no genre data
    (freshly imported corpus) must still be playable, and silently handing
    back an empty board would look like a bug to the player.
    """
    pool = list(pool)
    if not genres:
        return pool
    try:
        from kanjire.data import clusters
        members = clusters.members_of(genres)
    except Exception:  # noqa: BLE001
        return pool
    if not members:
        return pool
    kept = [w for w in pool if (w.expression, w.reading) in members]
    return kept or pool


def _glosses(meaning: str) -> frozenset[str]:
    """The individual senses of a gloss, for collision checks.

    Two words sharing *any* sense make a board unsolvable, not merely hard:
    if 閉める is "To close, to shut" and 閉まる is "To shut, to be closed",
    no player can tell which meaning card belongs to which kanji. Exact-string
    equality misses that, and the meaning-affinity dial makes such neighbours
    far more likely to be drawn together, so the check has to be per-sense.
    """
    return frozenset(
        part.strip() for part in meaning.lower().replace(";", ",").split(",")
        if part.strip()
    )


def _weight(word: Word, bias: float) -> float:
    # 10 ** (freq * bias): bias=1 -> proportional to real frequency,
    # bias=0 -> every word weight 1 (uniform).
    return 10.0 ** (max(word.freq, 0.0) * bias)


def _dedupe_by_face(words: Sequence[Word]) -> list[Word]:
    out: list[Word] = []
    seen_e, seen_r, seen_m = set(), set(), set()
    for w in words:
        senses = _glosses(w.meaning)
        if (w.expression in seen_e or w.reading in seen_r
                or senses & seen_m):
            continue
        out.append(w)
        seen_e.add(w.expression)
        seen_r.add(w.reading)
        seen_m |= senses
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
    pair_boost: dict[tuple[str, str], set[tuple[str, str]]] | None = None,
    series_map: dict[str, set[str]] | None = None,
    affinity: Affinity | None = None,
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
                                          penalize=penalize,
                                          pair_boost=pair_boost,
                                          series_map=series_map,
                                          affinity=affinity))

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
                                       penalize=penalize, pair_boost=pair_boost,
                                       series_map=series_map,
                                       affinity=affinity)
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
                                     penalize=penalize, pair_boost=pair_boost,
                                     series_map=series_map,
                                     affinity=affinity)
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
    confusable: bool = True,
    pair_boost: dict[tuple[str, str], set[tuple[str, str]]] | None = None,
    series_map: dict[str, set[str]] | None = None,
    affinity: Affinity | None = None,
) -> list[Word]:
    """Pick up to *n* distinct, mutually-unambiguous words from *pool*.

    Sequential weighted sampling without replacement: each pick draws from
    the remaining candidates proportionally to their frequency weight, then
    later picks are *boosted* toward words confusable with what's already on
    the board (shared kanji, same JLPT level, and — strongest — words the
    player has historically confused, via ``pair_boost``: a key -> partner
    keys map from the stats layer).

    ``penalize`` is a set of ``(expression, reading)`` keys to strongly
    down-weight (recently-shown words), so they rarely reappear back-to-back
    but can still be chosen if the pool would otherwise run dry.
    ``confusable=False`` disables every affinity boost (pure frequency draw).

    ``affinity`` adds the player's own clustering dials (genre / lookalike /
    soundalike) on top of the built-in confusability boosts.
    """
    rng = rng or random
    if n <= 0 or not pool:
        return []
    penalize = penalize or frozenset()
    aff = affinity if (affinity is not None and affinity.active) else None

    base: list[float] = []
    senses: list[frozenset[str]] = []
    for w in pool:
        if (w.expression, w.reading) in penalize:
            base.append(_RECENT_PENALTY)    # absolute floor — sink to the bottom
        else:
            base.append(_weight(w, bias))
        senses.append(_glosses(w.meaning))

    chosen: list[Word] = []
    seen_expr: set[str] = set()
    seen_reading: set[str] = set()
    seen_meaning: set[str] = set()
    picked_kanji: set[str] = set()
    picked_levels: set[int] = set()
    picked_partners: set[tuple[str, str]] = set()
    picked_series: set[str] = set()
    picked_genres: set[str] = set()
    picked_lookalikes: set[str] = set()
    picked_soundalikes: set[tuple[str, str]] = set()

    alive = list(range(len(pool)))
    for _ in range(n):
        weights: list[float] = []
        total = 0.0
        for i in alive:
            w = pool[i]
            key = (w.expression, w.reading)
            if (
                w.expression in seen_expr
                or w.reading in seen_reading
                or senses[i] & seen_meaning
            ):
                weights.append(0.0)
                continue
            wt = base[i]
            if confusable and chosen:
                if key in picked_partners:
                    wt *= _PAIR_BOOST
                if picked_kanji and any(ch in picked_kanji for ch in w.expression):
                    wt *= _KANJI_SHARE_BOOST
                elif picked_series and any(ch in picked_series
                                           for ch in w.expression):
                    wt *= _SERIES_BOOST
                if w.jlpt is not None and w.jlpt in picked_levels:
                    wt *= _LEVEL_MATCH_BOOST
            if aff is not None and chosen:
                # The dials stack: a word that is both on-topic and a
                # lookalike is the best distractor a board can have.
                if aff.meaning and picked_genres and (
                        set(aff.genre_map.get(key, ())) & picked_genres):
                    wt *= AFFINITY_STEPS[aff.meaning]
                if aff.looks and picked_lookalikes:
                    chars = kanji_chars(w.expression)
                    if chars:
                        # Scale by *how much* of the word is confusable. One
                        # shared component inside a two-kanji compound barely
                        # registers to the eye, so it earns a fraction of the
                        # boost; 待 next to 持 earns all of it.
                        ratio = sum(ch in picked_lookalikes
                                    for ch in chars) / len(chars)
                        if ratio:
                            wt *= AFFINITY_STEPS[aff.looks] ** ratio
                if aff.sound and key in picked_soundalikes:
                    wt *= AFFINITY_STEPS[aff.sound]
            weights.append(wt)
            total += wt
        if total <= 0.0:
            break
        pick = rng.choices(range(len(alive)), weights=weights)[0]
        idx = alive.pop(pick)
        w = pool[idx]
        chosen.append(w)
        key = (w.expression, w.reading)
        seen_expr.add(w.expression)
        seen_reading.add(w.reading)
        seen_meaning |= senses[idx]
        picked_kanji.update(kanji_chars(w.expression))
        if w.jlpt is not None:
            picked_levels.add(w.jlpt)
        if pair_boost:
            picked_partners |= pair_boost.get(key, set())
        if series_map:
            for ch in kanji_chars(w.expression):
                picked_series |= series_map.get(ch, set())
        if aff is not None:
            if aff.meaning and not picked_genres:
                # The theme is set by the first word that has one, and then
                # held. Accumulating each pick's genres instead let the theme
                # dissolve: words carry up to two genres, so after three picks
                # half the deck "matched" and the board looked random again.
                mine = aff.genre_map.get(key, ())
                if mine:
                    picked_genres.add(mine[0])
            if aff.looks:
                for ch in kanji_chars(w.expression):
                    picked_lookalikes |= aff.shape_map.get(ch, set())
            if aff.sound:
                picked_soundalikes |= aff.sound_map.get(key, set())

    return chosen
