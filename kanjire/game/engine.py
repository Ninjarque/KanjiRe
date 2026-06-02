"""The match engine: all game rules, no rendering.

Lifecycle
---------
``start()`` resets stats and deals the first board. The player calls
``select(card_id)`` for each click; the engine returns a :class:`SelectResult`
describing what happened so the UI can animate it. ``update(dt)`` advances the
timer. When a board is cleared the result has ``round_complete=True``; the UI
plays its clear animation and then calls ``next_round()``.

Rules
-----
* Each word becomes one card per configured face (kanji / reading / meaning).
* Clicking cards selects them. The first click fixes the *target* word.
* Selecting every face of the target word completes the group: it locks, scores
  ``base_points * combo``, and the combo grows.
* Clicking a card from a different word is a *mismatch*: the selection clears,
  the combo resets, and a mistake is recorded.
* The game ends when the timer runs out or mistakes exceed ``max_mistakes``.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from kanjire import i18n
from kanjire.game.config import GameConfig
from kanjire.model.sampling import weighted_sample_words
from kanjire.model.vocab import Word


class Phase(Enum):
    IDLE = "idle"
    PLAYING = "playing"
    GAME_OVER = "game_over"


#: When a board has a bounty candidate but the player isn't owed a heart, the
#: slot becomes a score *coin* with this probability (so coins are a frequent-
#: but-not-every-board reward, independent of difficulty).
_COIN_CHANCE = 0.5

#: A word that just appeared won't be re-proposed for this many rounds (it's
#: strongly down-weighted), so review cycles through your vocabulary instead of
#: re-showing the same common word every round.
_COOLDOWN_ROUNDS = 3


@dataclass
class Card:
    """One face of one word, placed on the board."""

    id: int
    group: int          # index of the owning word within the current round
    face: str           # 'kanji' | 'reading' | 'meaning'
    text: str
    matched: bool = False
    selected: bool = False


@dataclass
class SelectResult:
    """What a single :meth:`GameEngine.select` call produced."""

    kind: str                       # select|deselect|group_complete|mismatch|noop
    cards: list[int] = field(default_factory=list)
    group: int | None = None
    word: Word | None = None
    points: int = 0
    combo: int = 0
    round_complete: bool = False    # board cleared (any pass)
    set_complete: bool = False      # *final* pass of a familiarization set
    game_over: bool = False
    # ---- gamified lives (lives_mode) ---------------------------------- #
    life_delta: int = 0             # +1 heart bounty, -1 penalised mismatch
    bonus_points: int = 0           # extra score from a coin bounty
    bounty_type: str | None = None  # 'heart' | 'coin' | None (drives sfx/popup)
    group_was_new: bool = False     # the completed group wore a 新 sticker
    sticker_cleared: bool = False   # 新 removed (clean match of a new group)
    lives_left: int = 0             # current hearts, for the HUD


class GameEngine:
    def __init__(
        self,
        config: GameConfig,
        pool: list[Word],
        *,
        rng: random.Random | None = None,
        recorder=None,
        sampler=None,
        meta_provider=None,
    ) -> None:
        self.config = config
        self.pool = pool
        self.rng = rng or random.Random()
        # Optional sink for stats events. Anything quacking with saw / matched
        # / confused works; ``None`` disables recording entirely (tests / CLI).
        self.recorder = recorder
        # Sampler used to pick the next word set. Defaults to plain frequency-
        # weighted sampling; Learn / Survival swap in a bucket-aware one.
        self.sampler = sampler or (
            lambda pool, n, *, bias, rng, penalize=None:
                weighted_sample_words(pool, n, bias=bias, rng=rng, penalize=penalize)
        )
        #: Word keys shown in the last few rounds, for the re-proposal cooldown.
        self._recent_rounds: deque = deque(maxlen=_COOLDOWN_ROUNDS)
        # Per-deal metadata for lives_mode (Survival). Returns
        # ``(is_new: list[bool], bounty_candidate: int | None)`` — newness per
        # round word, and the index of the single hardest bounty candidate (or
        # None). The engine decides whether/which type the bounty becomes.
        self.meta_provider = meta_provider or (
            lambda words: ([False] * len(words), None)
        )

        # stats
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.matches = 0          # groups completed
        self.mistakes = 0
        self.rounds_completed = 0
        self.elapsed = 0.0
        self.time_left = float(config.duration) if config.timed else 0.0
        #: Hearts for lives_mode (moves up via bounties, down via penalised
        #: mismatches). 0 and unused outside lives_mode.
        self.lives = config.start_lives if config.lives_mode else 0

        # review: every distinct word the player has cleared
        self._seen_ids: set[int] = set()
        self.seen_words: list[Word] = []

        #: Per-group Adventure metadata, rebuilt each :meth:`_deal_board`.
        self.is_new: list[bool] = []
        self.bounty_type: list[str | None] = []   # 'heart' | 'coin' | None
        self._group_errored: list[bool] = []      # an error touched this group

        # round state
        self.phase = Phase.IDLE
        self.cards: dict[int, Card] = {}
        self.board: list[int] = []          # card ids in display order
        self.round_words: list[Word] = []   # index == group
        self.group_cards: list[list[int]] = []
        self.selection: list[int] = []
        self.current_group: int | None = None
        self.remaining_groups = 0
        self._next_id = 0
        #: 0..repetitions-1; familiarization mode increments through these
        #: passes over the same word set, advancing rounds_completed only when
        #: the last pass finishes.
        self.subround = 0

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.matches = 0
        self.mistakes = 0
        self.rounds_completed = 0
        self.elapsed = 0.0
        self.time_left = float(self.config.duration) if self.config.timed else 0.0
        self.lives = self.config.start_lives if self.config.lives_mode else 0
        self._seen_ids.clear()
        self.seen_words.clear()
        self._recent_rounds.clear()
        self.subround = 0
        self.phase = Phase.PLAYING
        self.next_round()

    def _deal_board(self) -> None:
        """(Re)build cards from ``self.round_words`` and shuffle the board."""
        self.cards.clear()
        self.board.clear()
        self.group_cards.clear()
        self.selection.clear()
        self.current_group = None
        self._next_id = 0
        loc = i18n.get_locale()
        for group_index, word in enumerate(self.round_words):
            ids: list[int] = []
            for face in self.config.faces:
                card = Card(
                    id=self._next_id,
                    group=group_index,
                    face=face,
                    text=word.face_text(face, locale=loc),
                )
                self.cards[card.id] = card
                ids.append(card.id)
                self._next_id += 1
            self.group_cards.append(ids)
        self.board = list(self.cards.keys())
        self.rng.shuffle(self.board)
        self.remaining_groups = len(self.round_words)
        # Compute per-group lives_mode metadata BEFORE saw() (which bumps
        # ``seen``). Newness keys on ``matches==0`` so saw() can't disturb it,
        # but we read pre-deal anyway for cleanliness.
        self._compute_group_meta()
        # Each board appearance is a "seen" event - including for words you
        # never get a chance to match (incomplete games still count).
        if self.recorder is not None:
            for w in self.round_words:
                try:
                    self.recorder.saw(w)
                except Exception:  # noqa: BLE001 - stats must never crash gameplay
                    pass

    def _compute_group_meta(self) -> None:
        """Set ``is_new`` / ``bounty_type`` / reset ``_group_errored`` for the
        freshly dealt board (lives_mode only)."""
        n = len(self.round_words)
        self._group_errored = [False] * n
        self.bounty_type = [None] * n
        if not self.config.lives_mode:
            self.is_new = [False] * n
            return
        is_new, cand = self.meta_provider(self.round_words)
        self.is_new = list(is_new) if len(is_new) == n else [False] * n
        # One bounty slot per board: the hardest qualifying (non-new) group,
        # if the provider found one. A heart when the player is below half
        # their max and the difficulty's heart_chance roll passes; otherwise a
        # score coin (occasionally), so there's always something to chase.
        if cand is not None and 0 <= cand < n and not self.is_new[cand]:
            below_half = self.lives * 2 < self.config.max_lives
            if (below_half and self.config.heart_chance > 0.0
                    and self.rng.random() < self.config.heart_chance):
                self.bounty_type[cand] = "heart"
            elif self.rng.random() < _COIN_CHANCE:
                self.bounty_type[cand] = "coin"

    def next_round(self) -> None:
        """Pick a *new* set of words and deal a fresh board."""
        penalize = frozenset().union(*self._recent_rounds)
        self.round_words = self.sampler(
            self.pool,
            self.config.words_per_round,
            bias=self.config.frequency_bias,
            rng=self.rng,
            penalize=penalize,
        )
        # Remember this set so its words aren't re-proposed for a few rounds.
        self._recent_rounds.append(
            frozenset((w.expression, w.reading) for w in self.round_words)
        )
        self.subround = 0
        self._deal_board()

    def advance(self) -> bool:
        """Move on after a board clear.

        Returns ``True`` if the same words were redealt (a familiarization sub
        pass) and ``False`` when a new set of words was drawn.
        """
        if self.subround + 1 < self.config.repetitions:
            self.subround += 1
            self._deal_board()
            return True
        self.subround = 0
        self.next_round()
        return False

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    @property
    def board_cards(self) -> list[Card]:
        return [self.cards[cid] for cid in self.board]

    @property
    def accuracy(self) -> float:
        total = self.matches + self.mistakes
        return self.matches / total if total else 1.0

    @property
    def words_learned(self) -> int:
        return len(self.seen_words)

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #
    def select(self, card_id: int) -> SelectResult:
        if self.phase is not Phase.PLAYING:
            return SelectResult("noop")
        card = self.cards.get(card_id)
        if card is None or card.matched:
            return SelectResult("noop")

        # Clicking a selected card de-selects it.
        if card.selected:
            card.selected = False
            self.selection.remove(card_id)
            if not self.selection:
                self.current_group = None
            return SelectResult("deselect", cards=[card_id], group=card.group)

        # Starting a fresh selection.
        if not self.selection:
            card.selected = True
            self.selection.append(card_id)
            self.current_group = card.group
            return SelectResult("select", cards=[card_id], group=card.group)

        # Adding to an in-progress selection.
        if card.group == self.current_group:
            card.selected = True
            self.selection.append(card_id)
            if self._selection_completes_group():
                return self._complete_group()
            return SelectResult("select", cards=[card_id], group=card.group)

        # Different word -> mismatch.
        return self._mismatch(card_id)

    def _selection_completes_group(self) -> bool:
        if self.current_group is None:
            return False
        return set(self.selection) == set(self.group_cards[self.current_group])

    def _complete_group(self) -> SelectResult:
        group = self.current_group
        assert group is not None
        ids = self.group_cards[group]
        for cid in ids:
            self.cards[cid].matched = True
            self.cards[cid].selected = False

        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.matches += 1
        points = self.config.base_points * self.combo
        self.score += points

        word = self.round_words[group]
        if word.id not in self._seen_ids:
            self._seen_ids.add(word.id)
            self.seen_words.append(word)
        if self.recorder is not None:
            try:
                self.recorder.matched(word)
            except Exception:  # noqa: BLE001
                pass

        # Gamified lives: a clean match of a bounty group pays out, and clearing
        # a 新 group cleanly retires its sticker. "Clean" = no error touched the
        # group this deal.
        life_delta = 0
        bonus_points = 0
        bounty_type = None
        group_was_new = self.config.lives_mode and self.is_new[group]
        sticker_cleared = False
        if self.config.lives_mode:
            clean = not self._group_errored[group]
            bt = self.bounty_type[group]
            if bt == "heart" and clean and self.lives < self.config.max_lives:
                self.lives += 1
                life_delta = 1
                bounty_type = "heart"
            elif bt == "coin" and clean:
                bonus_points = self.config.base_points * max(1, self.combo)
                self.score += bonus_points
                bounty_type = "coin"
            if group_was_new and clean:
                sticker_cleared = True
                self.is_new[group] = False

        self.selection = []
        self.current_group = None
        self.remaining_groups -= 1

        result = SelectResult(
            "group_complete",
            cards=list(ids),
            group=group,
            word=word,
            points=points,
            combo=self.combo,
            life_delta=life_delta,
            bonus_points=bonus_points,
            bounty_type=bounty_type,
            group_was_new=group_was_new,
            sticker_cleared=sticker_cleared,
            lives_left=self.lives,
        )
        if self.remaining_groups == 0:
            self.score += self.config.round_bonus
            result.points += self.config.round_bonus
            result.round_complete = True
            # A "round" in stats is one full pass over a word set; only count
            # it on the last familiarization repetition.
            if self.subround + 1 >= self.config.repetitions:
                self.rounds_completed += 1
                result.set_complete = True
        return result

    def _mismatch(self, offending_id: int) -> SelectResult:
        affected = list(self.selection)
        affected.append(offending_id)

        # Capture *before* tearing down state - we need these intact to record
        # who the player was confusing with whom and on which face dimension.
        offending_card = self.cards.get(offending_id)
        target_group = self.current_group
        target_word = (
            self.round_words[target_group] if target_group is not None else None
        )
        offending_group = offending_card.group if offending_card else None
        offending_word = (
            self.round_words[offending_group] if offending_group is not None else None
        )
        offending_face = offending_card.face if offending_card else None

        for cid in self.selection:
            self.cards[cid].selected = False
        self.selection = []
        self.current_group = None

        self.combo = 0
        self.mistakes += 1
        self.score = max(0, self.score - self.config.mismatch_penalty)

        # "Only the wrong" semantics: the pre-existing correctly-selected cards
        # were *right*, so they don't get penalised. Only the offending click
        # against the in-progress target word gets flagged - and only on the
        # face dimension that actually misled the player.
        if (
            self.recorder is not None
            and target_word is not None
            and offending_word is not None
            and offending_face is not None
        ):
            try:
                self.recorder.confused(target_word, offending_word, offending_face)
            except Exception:  # noqa: BLE001
                pass

        result = SelectResult("mismatch", cards=affected)

        if self.config.lives_mode:
            # Any group an error touched is no longer "clean" this deal, so its
            # 新 sticker won't retire and a bounty on it won't pay out.
            if target_group is not None:
                self._group_errored[target_group] = True
            if offending_group is not None:
                self._group_errored[offending_group] = True
            # Heart-loss rule: you only lose a heart when the word you were
            # *assembling* (the target) is already learned. Fumbling a 新 word
            # is free - you're still learning it.
            target_is_new = (
                target_group is not None and self.is_new[target_group]
            )
            if target_group is not None and not target_is_new:
                self.lives -= 1
                result.life_delta = -1
            result.lives_left = self.lives
            if self.lives <= 0:
                self.phase = Phase.GAME_OVER
                result.game_over = True
        elif self._mistakes_exhausted():
            self.phase = Phase.GAME_OVER
            result.game_over = True
        return result

    # ------------------------------------------------------------------ #
    # Time
    # ------------------------------------------------------------------ #
    def update(self, dt: float) -> bool:
        """Advance time. Returns True if the game just ended."""
        if self.phase is not Phase.PLAYING:
            return False
        self.elapsed += dt
        if self.config.timed:
            self.time_left -= dt
            if self.time_left <= 0.0:
                self.time_left = 0.0
                self.phase = Phase.GAME_OVER
                return True
        return False

    def _mistakes_exhausted(self) -> bool:
        return (
            self.config.max_mistakes is not None
            and self.mistakes > self.config.max_mistakes
        )

    @property
    def mistakes_left(self) -> int | None:
        if self.config.max_mistakes is None:
            return None
        return max(0, self.config.max_mistakes - self.mistakes)
