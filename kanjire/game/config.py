"""Game configuration and a few ready-made presets.

A :class:`GameConfig` fully describes a session: what vocabulary to draw from,
how a round is shaped, how the game ends, and how points are scored. The pyglet
menu builds one of these from the player's choices; the engine consumes it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

#: The card "faces" a single word can be split into.
ALL_FACES = ("kanji", "reading", "meaning")
DEFAULT_FACES = ("kanji", "reading", "meaning")


#: Allowed values for ``GameConfig.vertical_writing``.
VERTICAL_MODES = ("off", "random", "all")


@dataclass
class GameConfig:
    # ---- content -------------------------------------------------------- #
    decks: tuple[str, ...] = ("jlpt",)
    levels: tuple[int, ...] = (5,)           # JLPT levels; empty = any level
    faces: tuple[str, ...] = DEFAULT_FACES   # cards produced per word
    words_per_round: int = 6                 # groups on the board at once
    frequency_bias: float = 0.4              # 0=uniform .. 1=true frequency

    # ---- pacing / end conditions --------------------------------------- #
    duration: float | None = 120.0          # seconds; None = untimed
    max_mistakes: int | None = None          # None = unlimited

    # ---- scoring -------------------------------------------------------- #
    base_points: int = 100                    # per completed group, x combo
    mismatch_penalty: int = 0                 # points removed per mismatch
    round_bonus: int = 200                    # awarded when a board is cleared

    # ---- familiarization / visual variety ------------------------------ #
    #: How many times each word-set repeats before fresh words are drawn.
    #: 1 = normal play; >1 = recognition drill (same words, different fonts).
    repetitions: int = 1
    #: When True every kanji/reading card picks a random font from JP_FONTS.
    random_fonts: bool = False
    #: 'off' | 'random' (per-card 50/50) | 'all' (every kanji/reading vertical).
    vertical_writing: str = "off"

    # ---- learn mode (bucket mix; 0-3 ≈ None/Few/Some/Many) ------------- #
    learn_known:      int = 0
    learn_less_known: int = 0
    learn_unknown:    int = 0

    # ---- gamified lives (Survival) ------------------------------------- #
    #: When True the engine uses a real hearts counter that moves UP (bounties)
    #: and DOWN (errors on already-learned words), instead of the one-way
    #: max_mistakes counter. New (新) words are stake-free.
    lives_mode:   bool = False
    start_lives:  int = 3
    max_lives:    int = 5
    #: Probability that the single per-board bounty becomes a *heart* (rather
    #: than a score *coin*) when the player is below half their max hearts.
    #: 0.0 = "None" (heart bounties never spawn).
    heart_chance: float = 0.0

    # ---- kana training (only used when the "kana" synthetic deck is on) #
    #: How many kana syllables to stitch into each synthetic "word".
    kana_length: int = 1
    #: 'hira' | 'kata' | 'both'.  Decides which scripts appear as cards.
    kana_script: str = "both"

    name: str = "Custom"

    def __post_init__(self) -> None:
        if not self.faces:
            raise ValueError("a config needs at least one face")
        for f in self.faces:
            if f not in ALL_FACES:
                raise ValueError(f"unknown face: {f!r}")
        self.frequency_bias = max(0.0, min(1.0, self.frequency_bias))
        if self.vertical_writing not in VERTICAL_MODES:
            raise ValueError(f"unknown vertical_writing: {self.vertical_writing!r}")
        self.repetitions = max(1, int(self.repetitions))
        self.start_lives = max(1, int(self.start_lives))
        self.max_lives = max(self.start_lives, int(self.max_lives))
        self.heart_chance = max(0.0, min(1.0, float(self.heart_chance)))

    @property
    def group_size(self) -> int:
        return len(self.faces)

    @property
    def cards_per_round(self) -> int:
        return self.words_per_round * self.group_size

    @property
    def timed(self) -> bool:
        return self.duration is not None

    def with_(self, **changes) -> "GameConfig":
        """Return a copy with the given fields replaced."""
        return replace(self, **changes)


#: Named presets surfaced in the menu. Each is a factory so callers get a fresh,
#: independently-mutable config.
def _time_attack() -> GameConfig:
    return GameConfig(
        name="Time Attack",
        duration=120.0,
        max_mistakes=None,
        words_per_round=6,
    )


def _survival() -> GameConfig:
    """Gamified, fair learning. New (新) words are stake-free; you lose a heart
    only when you flub a word you've already learned; you win hearts (or bonus
    score) from a per-board bounty on your hardest words. Difficulty (starting
    hearts + bounty frequency) is chosen in the menu."""
    return GameConfig(
        name="Survival",
        duration=None,
        max_mistakes=None,        # hearts are handled by lives_mode now
        words_per_round=6,
        mismatch_penalty=0,
        repetitions=1,
        lives_mode=True,
        start_lives=3,
        max_lives=5,
        heart_chance=0.35,
        learn_known=1,            # already-learned words (the stakes)
        learn_less_known=2,       # struggle words
        learn_unknown=3,          # plenty of fresh (新) words
    )


def _zen() -> GameConfig:
    return GameConfig(
        name="Zen",
        duration=None,
        max_mistakes=None,
        words_per_round=8,
        mismatch_penalty=0,
    )


def _familiarize() -> GameConfig:
    """Recognition drill: same words three times, new fonts/direction each pass."""
    return GameConfig(
        name="Familiarize",
        duration=None,
        max_mistakes=None,
        words_per_round=5,
        repetitions=3,
        random_fonts=True,
        vertical_writing="random",
        mismatch_penalty=0,
    )


def _learn() -> GameConfig:
    """Pulls a curated mix of known / less-known / unknown words from the
    cross-deck stats so each round is tuned to where the player actually is."""
    return GameConfig(
        name="Learn",
        duration=None,
        max_mistakes=None,
        words_per_round=6,
        mismatch_penalty=0,
        learn_known=1,        # a light anchor of familiar words
        learn_less_known=2,   # the struggle words
        learn_unknown=3,      # plenty of fresh growth - the point of Learn
    )


PRESETS: dict[str, "callable[[], GameConfig]"] = {
    "Time Attack": _time_attack,
    "Survival": _survival,
    "Zen": _zen,
    "Familiarize": _familiarize,
    "Learn": _learn,
}
