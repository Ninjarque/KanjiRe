"""Recall v2: alternative readings, choice options, prompt policy."""
from __future__ import annotations

import random

from kanjire.game.recall import (
    acceptable_readings,
    choice_options,
    is_correct_reading,
    prompt_for,
)


class _W:
    def __init__(self, expression, reading):
        self.expression = expression
        self.reading = reading


def test_alternative_readings_all_accepted():
    # The dataset holds ~20 words like this; an exact match made them
    # unanswerable ("nan; nani").
    r = "かねもち; おかねもち"
    assert is_correct_reading("かねもち", r)
    assert is_correct_reading("おかねもち", r)
    assert is_correct_reading(" かねもち ", r)     # stray spaces forgiven
    assert is_correct_reading(r, r)                # the joined form too
    assert not is_correct_reading("かねもちお", r)


def test_single_reading_unchanged():
    assert acceptable_readings("じかん") == ["じかん"]
    assert is_correct_reading("じかん", "じかん")
    assert not is_correct_reading("じか", "じかん")


def test_choice_options_exactly_one_correct():
    rng = random.Random(7)
    w = _W("時間", "じかん")
    pool = [_W("学校", "がっこう"), _W("時計", "とけい"),
            _W("人間", "にんげん"), _W("自分", "じぶん"),
            _W("電話", "でんわ")]
    opts = choice_options(w, pool, rng, k=4)
    assert len(opts) == 4
    assert len(set(opts)) == 4
    correct = [o for o in opts if is_correct_reading(o, w.reading)]
    assert correct == ["じかん"]


def test_choice_options_small_pool_still_works():
    rng = random.Random(1)
    w = _W("時間", "じかん")
    opts = choice_options(w, [_W("学校", "がっこう")], rng, k=4)
    assert "じかん" in opts and len(opts) == 2


def test_choice_options_skips_duplicate_readings():
    rng = random.Random(3)
    w = _W("時間", "じかん")
    pool = [_W("自分", "じかん"),      # same reading: never a distractor
            _W("学校", "がっこう")]
    opts = choice_options(w, pool, rng, k=4)
    assert opts.count("じかん") == 1


def test_prompt_for_choice_needs_no_tts():
    assert prompt_for(0, "choice", tts_ok=False) == "choice"
    assert prompt_for(5, "choice", tts_ok=True) == "choice"
