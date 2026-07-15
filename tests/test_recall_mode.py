"""The standalone Recall mode: word sampling + scoring + results routing."""
from __future__ import annotations

import os
import sys

os.environ["KANJIRE_NO_NETWORK"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from kanjire.game.config import PRESETS, GameConfig
from kanjire.model.wordpick import sample_words


class _FakeStats:
    def confusion_partners(self):
        return {}

    def classify_words(self, pool):
        return {}


class _FakeApp:
    def __init__(self, con):
        self.con = con
        self.stats = _FakeStats()


@pytest.fixture
def app():
    from kanjire.data import db
    return _FakeApp(db.connect(read_only=True))


def test_recall_is_a_registered_mode_with_settings():
    assert "Recall" in PRESETS
    cfg = PRESETS["Recall"]()
    assert cfg.recall_mode is True
    assert cfg.recall_prompt in ("typed", "listen", "mixed")
    # It draws a learn-tuned mix, so word selection is as controllable as Learn.
    assert any((cfg.learn_known, cfg.learn_less_known, cfg.learn_unknown))


def test_sample_words_respects_the_count(app):
    cfg = GameConfig(decks=("jlpt",), levels=(5,), words_per_round=7)
    words = sample_words(app, cfg, 7)
    assert 1 <= len(words) <= 7
    # No duplicates by (expression, reading).
    keys = [(w.expression, w.reading) for w in words]
    assert len(keys) == len(set(keys))


def test_sample_words_empty_for_a_nonexistent_deck(app):
    cfg = GameConfig(decks=("corpus:does-not-exist",), levels=())
    assert sample_words(app, cfg, 5) == []


def test_prompt_style_selects_typed_listen_or_mixed():
    from kanjire.ui.scenes.recall import RecallScene as R

    # typed: always typed, even with TTS.
    assert [R._prompt_for(i, "typed", True) for i in range(4)] == ["typed"] * 4
    # listen: dictation when TTS is available, else falls back to typed.
    assert [R._prompt_for(i, "listen", True) for i in range(3)] == ["listen"] * 3
    assert [R._prompt_for(i, "listen", False) for i in range(3)] == ["typed"] * 3
    # mixed: alternates, but only when TTS exists.
    assert R._prompt_for(1, "mixed", True) == "listen"
    assert R._prompt_for(0, "mixed", True) == "typed"
    assert R._prompt_for(1, "mixed", False) == "typed"


def test_recall_engine_tracks_score():
    from kanjire.ui.scenes.recall import (_POINTS_FIRST, _POINTS_LATER,
                                          _RecallEngine)

    eng = _RecallEngine(["a", "b", "c"])
    assert eng.score == 0 and eng.matches == 0 and eng.mistakes == 0
    assert eng.session_left == 0        # not a session-mode game
    assert list(eng.seen_words) == ["a", "b", "c"]
    assert _POINTS_FIRST > _POINTS_LATER > 0

    eng.record(recalled=True, first_try=True)    # clean
    eng.record(recalled=True, first_try=True)    # clean -> combo 2
    eng.record(recalled=False, first_try=False)  # gave up -> combo resets
    eng.record(recalled=True, first_try=False)   # eventual
    assert eng.matches == 3 and eng.mistakes == 1
    assert eng.score == _POINTS_FIRST * 2 + _POINTS_LATER
    assert eng.best_combo == 2
    assert abs(eng.accuracy - 0.75) < 1e-6
    assert eng.words_learned == 3
