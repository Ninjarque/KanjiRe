"""The genre taxonomy: named meaning-buckets every word can belong to.

A *genre* is a human-readable topic ("Food & Drink", "Weather & Sky") that
words are sorted into once, offline, by ``scripts/build_clusters.py``. The
assignment lands in the bundled ``clusters.db`` sidecar; this module holds
the part the running game needs: the stable ordered list of genres, their
icons and their translation keys.

Two rules shaped this table:

* **Icons are kanji, not emoji.** The bundled Japanese fonts have no emoji
  coverage, so an emoji renders as tofu on Linux and Android. A kanji icon
  always draws, and reads better in a kanji game anyway.
* **Keys are stable forever.** They are written into ``clusters.db``, into
  saved presets and into multiplayer room settings, so renaming one breaks
  other people's saved games. Add genres freely; never rename one. (The
  *order* is display-only and safe to change — nothing persists it.)
"""
from __future__ import annotations

from typing import NamedTuple


class Genre(NamedTuple):
    key: str        #: stable identifier — persisted, never rename
    icon: str       #: single kanji shown as the genre's badge
    tr: str         #: i18n key for the display label


#: Every genre, in display order. The Genres browser lays these out 5-wide
#: (like the Journey road), so multiples of 5 make for tidy rows.
GENRES: tuple[Genre, ...] = (
    Genre("food",          "食", "GENRE_FOOD"),
    Genre("animals",       "犬", "GENRE_ANIMALS"),
    Genre("plants",        "花", "GENRE_PLANTS"),
    Genre("nature",        "山", "GENRE_NATURE"),
    Genre("weather",       "雨", "GENRE_WEATHER"),

    Genre("body",          "体", "GENRE_BODY"),
    Genre("health",        "薬", "GENRE_HEALTH"),
    Genre("family",        "親", "GENRE_FAMILY"),
    Genre("people",        "人", "GENRE_PEOPLE"),
    Genre("emotion",       "心", "GENRE_EMOTION"),

    Genre("mind",          "考", "GENRE_MIND"),
    Genre("speech",        "話", "GENRE_SPEECH"),
    Genre("expressions",   "挨", "GENRE_EXPRESSIONS"),
    Genre("communication", "便", "GENRE_COMMUNICATION"),
    Genre("school",        "学", "GENRE_SCHOOL"),

    Genre("work",          "仕", "GENRE_WORK"),
    Genre("money",         "円", "GENRE_MONEY"),
    Genre("home",          "家", "GENRE_HOME"),
    Genre("clothing",      "服", "GENRE_CLOTHING"),
    Genre("tools",         "具", "GENRE_TOOLS"),

    Genre("technology",    "機", "GENRE_TECHNOLOGY"),
    Genre("transport",     "車", "GENRE_TRANSPORT"),
    Genre("travel",        "旅", "GENRE_TRAVEL"),
    Genre("city",          "街", "GENRE_CITY"),
    Genre("geography",     "国", "GENRE_GEOGRAPHY"),

    Genre("time",          "時", "GENRE_TIME"),
    Genre("numbers",       "数", "GENRE_NUMBERS"),
    Genre("quantity",      "多", "GENRE_QUANTITY"),
    Genre("colors",        "色", "GENRE_COLORS"),
    Genre("position",      "方", "GENRE_POSITION"),

    Genre("movement",      "走", "GENRE_MOVEMENT"),
    Genre("actions",       "手", "GENRE_ACTIONS"),
    Genre("social",        "友", "GENRE_SOCIAL"),
    Genre("culture",       "神", "GENRE_CULTURE"),
    Genre("arts",          "音", "GENRE_ARTS"),

    Genre("sports",        "球", "GENRE_SPORTS"),
    Genre("society",       "政", "GENRE_SOCIETY"),
    Genre("qualities",     "良", "GENRE_QUALITIES"),
    Genre("manner",        "様", "GENRE_MANNER"),
    Genre("grammar",       "語", "GENRE_GRAMMAR"),
)

#: key -> Genre, for the many lookups by stable key.
BY_KEY: dict[str, Genre] = {g.key: g for g in GENRES}

#: The set of valid keys — use to validate anything read from disk or the
#: network, since a stale client may send a genre this build doesn't know.
KEYS: frozenset[str] = frozenset(BY_KEY)


def search(query: str, label_of=None) -> list[Genre]:
    """The genres matching *query*, in taxonomy order.

    Matches the localised label, the stable key, and the icon glyph, so
    "food", "man" (Manger) and 食 all find Food & Drink. An empty query
    returns everything — forty topics is a lot to thumb past, but the
    unfiltered grid is still the default view.

    *label_of* maps a Genre to its displayed label (the UI passes ``tr``'s
    result); it defaults to the translation key so this stays importable
    without the i18n layer.
    """
    q = (query or "").strip().lower()
    if not q:
        return list(GENRES)
    label_of = label_of or (lambda g: g.tr)
    out = []
    for g in GENRES:
        if q in (label_of(g) or "").lower() or q in g.key or q == g.icon:
            out.append(g)
    return out


def valid_genres(values) -> tuple[str, ...]:
    """The recognised genre keys in *values*, deduped, in taxonomy order.

    Anything unknown (an older save, a newer peer) is dropped rather than
    raising: an unrecognised genre should narrow a game, never break it.
    """
    if not values:
        return ()
    picked = {v for v in values if isinstance(v, str) and v in KEYS}
    return tuple(g.key for g in GENRES if g.key in picked)
