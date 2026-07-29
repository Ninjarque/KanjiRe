"""The word pool a multiplayer host contributes to its room.

The relay server is deliberately data-free: it holds no dictionary, so the
host samples the words and ships them with the ``start`` message. Both UIs
did that with their own copy of the same twelve lines; this is the one copy,
and the place the room's clustering settings are honoured.

Because the host resolves everything into plain card dicts, genres and
affinity cost the protocol *nothing* — a guest on an older build receives an
ordinary word list and never needs to know why those words go together.
"""
from __future__ import annotations

import random

from kanjire.data import db
from kanjire.data.genres import valid_genres
from kanjire.kana import hira_to_romaji
from kanjire.model.sampling import (Affinity, filter_by_genres,
                                    weighted_sample_words)


def affinity_from_settings(settings: dict) -> Affinity | None:
    """The room's affinity dials as a sampler :class:`Affinity`."""
    meaning = _dial(settings, "aff_meaning")
    looks = _dial(settings, "aff_looks")
    sound = _dial(settings, "aff_sound")
    if not (meaning or looks or sound):
        return None
    try:
        from kanjire.data import clusters
        return Affinity(
            genre_map=clusters.genre_index() if meaning else {},
            shape_map=clusters.shape_map() if looks else {},
            sound_map=clusters.sound_map() if sound else {},
            meaning=meaning, looks=looks, sound=sound,
        )
    except Exception:  # noqa: BLE001 — never fail a game start over this
        return None


def _dial(settings: dict, key: str) -> int:
    try:
        return max(0, min(3, int(settings.get(key, 0) or 0)))
    except (TypeError, ValueError):
        return 0


def sample_pool(con, settings: dict, *, size: int, locale: str = "en",
                rng: random.Random | None = None) -> list[dict]:
    """``size`` cards' worth of words for a room, as JSON-ready dicts."""
    rng = rng or random.Random()
    deck = settings.get("deck") or "jlpt"
    levels = settings.get("levels") or [5]
    try:
        words = db.load_words(con, decks=[deck],
                              levels=levels if deck == "jlpt" else None,
                              require_kanji=True)
    except Exception:  # noqa: BLE001 — a bad deck must not break the lobby
        words = []
    words = filter_by_genres(words, valid_genres(settings.get("genres")))
    # confusable=False keeps the built-in shared-kanji/keisei boosts off (a
    # party board shouldn't be sneaky unless asked); the room's own dials
    # are separate and still apply.
    picked = weighted_sample_words(words, size, bias=0.4, rng=rng,
                                   confusable=False,
                                   affinity=affinity_from_settings(settings))
    return [{
        "kanji": w.expression,
        "reading": w.reading,
        "romaji": hira_to_romaji(w.reading),
        "meaning": w.get_meaning(locale),
    } for w in picked]


def host_defaults(state) -> dict:
    """The clustering a host carries into a room it creates.

    A player who has just been drilling lookalikes in the Genres browser
    almost certainly wants the same game with their friend, and the desktop
    lobby has no room for a forty-genre picker. Seeding the room from their
    solo settings makes the obvious thing happen by default; the lobby can
    still change it (and the phone lobby can pick any genre outright).
    """
    try:
        from kanjire.game.menuconfig import normalized_settings
        mode = state.last_mode or "Time Attack"
        s = normalized_settings(state.last_for_mode(mode))
    except Exception:  # noqa: BLE001
        return {}
    out = {"genres": list(s.get("genres") or [])}
    for key in ("aff_meaning", "aff_looks", "aff_sound"):
        out[key] = int(s.get(key, 0) or 0)
    return out
