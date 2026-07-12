"""The core vocabulary record shared by every layer of the game."""
from __future__ import annotations

from dataclasses import dataclass

from kanjire.jputil import capitalize_first

# JLPT level constants. Higher number = easier (N5 is the beginner level).
JLPT_LEVELS = (5, 4, 3, 2, 1)


def jlpt_label(level: int | None) -> str:
    return f"N{level}" if level else "-"


@dataclass(frozen=True)
class Word:
    """A single vocabulary item, with one writing per "face" of a card group.

    A word produces up to three cards in the game: its kanji writing
    (``expression``), its reading (``reading``) and its locale-aware
    ``meaning``.  The English gloss is always present; ``meaning_fr`` is
    populated for entries that exist in JMdict's French dataset (covers the
    majority of common vocabulary) and the game falls back to English for
    anything missing.
    """

    id: int
    expression: str               # writing containing kanji, e.g. 食べる
    reading: str                  # hiragana reading, e.g. たべる
    meaning: str                  # short English gloss, e.g. "to eat"
    jlpt: int | None              # 5..1 (N5..N1) or None if unknown
    freq: float                   # zipf-like frequency, ~0..7
    deck: str                     # source deck name
    pos: str | None = None        # part of speech (corpus decks)
    count: int | None = None      # raw occurrences (corpus decks)
    meaning_fr: str | None = None # French gloss from JMdict, if available

    @property
    def jlpt_label(self) -> str:
        return jlpt_label(self.jlpt)

    def get_meaning(self, locale: str = "en") -> str:
        """Pick the gloss appropriate for ``locale``, with English fallback.

        Always capitalises the first character so the card reads properly
        even when the underlying data file has the gloss in lower-case
        (which is the JMdict convention)."""
        raw = self.meaning_fr if (locale == "fr" and self.meaning_fr) else self.meaning
        return capitalize_first(raw) or raw

    def face_text(self, face: str, locale: str = "en") -> str:
        """Return the text shown on a card of the given ``face``."""
        if face == "kanji":
            return self.expression
        if face == "reading":
            return self.reading
        if face == "romaji":
            # Lazy import: kana.py imports Word from this module at load time.
            from kanjire.kana import hira_to_romaji
            return hira_to_romaji(self.reading)
        if face == "meaning":
            return self.get_meaning(locale)
        raise ValueError(f"unknown face: {face!r}")
