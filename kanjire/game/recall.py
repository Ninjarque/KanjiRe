"""Shared typed-recall engine + prompt policy (used by the Kivy UI).

Extracted from the pyglet recall scene (which still carries its own copy
until the parity switchover). Typing the reading is *recall* — stronger
evidence than recognising a card — so scoring and SRS grading favour clean
first-try answers.
"""
from __future__ import annotations

#: Give up and show the answer after this many wrong submissions.
MAX_ATTEMPTS = 2

#: A clean first-try answer is worth the most, an eventual answer half.
POINTS_FIRST = 100
POINTS_LATER = 50


class RecallEngine:
    """Minimal engine stand-in exposing the read-only surface a results
    view expects from a real GameEngine (score / matches / accuracy / …)."""

    def __init__(self, words) -> None:
        self.score = 0
        self.matches = 0            # words recalled (first or eventual)
        self.mistakes = 0           # words given up on
        self.seen_words = list(words)
        self.pool = list(words)
        self.session_left = 0
        self.rounds_completed = 0
        self.best_combo = 0         # longest run of first-try recalls
        self._combo = 0

    def record(self, *, recalled: bool, first_try: bool) -> None:
        self.rounds_completed += 1
        if recalled:
            self.matches += 1
            self.score += POINTS_FIRST if first_try else POINTS_LATER
            if first_try:
                self._combo += 1
                self.best_combo = max(self.best_combo, self._combo)
            else:
                self._combo = 0
        else:
            self.mistakes += 1
            self._combo = 0

    @property
    def accuracy(self) -> float:
        total = self.matches + self.mistakes
        return (self.matches / total) if total else 0.0

    @property
    def words_learned(self) -> int:
        return self.matches


def prompt_for(i: int, want: str, tts_ok: bool) -> str:
    """Which prompt the *i*-th word uses: typed, listen (dictation) or both.

    Audio prompts need Japanese TTS; without it everything falls back to
    typed. 'mixed' alternates typed and listen.
    """
    if want == "listen":
        return "listen" if tts_ok else "typed"
    if want == "both":
        return "both" if tts_ok else "typed"
    if want == "typed":
        return "typed"
    return "listen" if (tts_ok and i % 2 == 1) else "typed"
