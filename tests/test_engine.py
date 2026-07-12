"""Tests for the pure game engine. Runs under pytest *or* as a plain script."""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.game.config import GameConfig
from kanjire.game.engine import GameEngine, Phase
from kanjire.model.vocab import Word


def make_pool(n: int) -> list[Word]:
    pool = []
    for i in range(n):
        pool.append(
            Word(
                id=i,
                expression=f"漢{i}",          # contains kanji
                reading=f"よみ{i}",
                meaning=f"meaning {i}",
                jlpt=5,
                freq=3.0,
                deck="test",
            )
        )
    return pool


def new_engine(**cfg) -> GameEngine:
    config = GameConfig(decks=("test",), levels=(), **cfg)
    engine = GameEngine(config, make_pool(50), rng=random.Random(1234))
    engine.start()
    return engine


def _select_group(engine: GameEngine, group: int):
    """Click every card belonging to *group* in board order; return last result."""
    result = None
    for cid in list(engine.group_cards[group]):
        result = engine.select(cid)
    return result


def test_deal_board():
    e = new_engine(words_per_round=6)
    assert e.phase is Phase.PLAYING
    assert len(e.board) == 6 * 3          # 3 faces each
    assert len(e.round_words) == 6
    # every card maps to a real word/face
    faces = {c.face for c in e.board_cards}
    assert faces == {"kanji", "reading", "meaning"}


def test_complete_group_scores_with_combo():
    e = new_engine(words_per_round=6, base_points=100)
    r1 = _select_group(e, 0)
    assert r1.kind == "group_complete"
    assert r1.combo == 1 and r1.points == 100
    r2 = _select_group(e, 1)
    assert r2.combo == 2 and r2.points == 200      # combo multiplies
    assert e.score == 300
    assert e.matches == 2


def test_mismatch_resets_combo_and_counts():
    e = new_engine(words_per_round=6)
    _select_group(e, 0)                 # combo -> 1
    # click one card of group 1, then a card of group 2 -> mismatch
    a = e.group_cards[1][0]
    b = e.group_cards[2][0]
    e.select(a)
    r = e.select(b)
    assert r.kind == "mismatch"
    assert e.combo == 0
    assert e.mistakes == 1
    # the mismatched cards are de-selected, board unchanged otherwise
    assert all(not e.cards[c].selected for c in (a, b))


def test_deselect():
    e = new_engine()
    cid = e.group_cards[0][0]
    e.select(cid)
    assert e.cards[cid].selected
    r = e.select(cid)
    assert r.kind == "deselect"
    assert not e.cards[cid].selected
    assert e.selection == []


def test_round_completes_and_deals_new_board():
    e = new_engine(words_per_round=4)
    last = None
    for g in range(4):
        last = _select_group(e, g)
    assert last.round_complete is True
    assert e.rounds_completed == 1
    e.next_round()
    assert e.remaining_groups == 4
    assert all(not c.matched for c in e.board_cards)


def test_survival_game_over():
    e = new_engine(words_per_round=6, duration=None, max_mistakes=2)
    # cause 3 mismatches (exceeds max of 2)
    for _ in range(3):
        # pick two cards from different groups
        a = next(c.id for c in e.board_cards if not c.matched and c.group == 0)
        b = next(c.id for c in e.board_cards if not c.matched and c.group == 1)
        e.select(a)
        r = e.select(b)
    assert e.mistakes == 3
    assert r.game_over is True
    assert e.phase is Phase.GAME_OVER
    # input ignored after game over
    assert e.select(e.board[0]).kind == "noop"


def test_timed_game_over():
    e = new_engine(duration=10.0, max_mistakes=None)
    assert e.update(4.0) is False
    assert abs(e.time_left - 6.0) < 1e-6
    assert e.update(6.0) is True
    assert e.phase is Phase.GAME_OVER


class _Recorder:
    """Captures every event so the test can assert what got called."""
    def __init__(self):
        self.saw_calls = []
        self.matched_calls = []
        self.clean_flags = []
        self.confused_calls = []
    def saw(self, w):                self.saw_calls.append(w)
    def matched(self, w, clean=True):
        self.matched_calls.append(w)
        self.clean_flags.append(clean)
    def confused(self, t, o, face):  self.confused_calls.append((t, o, face))


def test_recorder_sees_every_round_word():
    rec = _Recorder()
    pool = make_pool(50)
    config = GameConfig(decks=("test",), levels=(), words_per_round=6)
    engine = GameEngine(config, pool, rng=random.Random(7), recorder=rec)
    engine.start()
    assert len(rec.saw_calls) == 6
    assert {w.id for w in rec.saw_calls} == {w.id for w in engine.round_words}


def test_recorder_matched_called_per_group_completed():
    rec = _Recorder()
    pool = make_pool(50)
    config = GameConfig(decks=("test",), levels=(), words_per_round=4)
    engine = GameEngine(config, pool, rng=random.Random(7), recorder=rec)
    engine.start()
    _select_group(engine, 0)
    _select_group(engine, 1)
    assert len(rec.matched_calls) == 2
    assert rec.matched_calls[0] is engine.round_words[0]
    assert rec.matched_calls[1] is engine.round_words[1]


def test_recorder_confused_uses_offending_face_and_only_the_wrong():
    """Mismatch records (target_word, offending_word, offending_face). The
    pre-existing correctly-selected cards stay positive evidence."""
    rec = _Recorder()
    pool = make_pool(50)
    config = GameConfig(decks=("test",), levels=(), words_per_round=4,
                        duration=None, max_mistakes=None)
    engine = GameEngine(config, pool, rng=random.Random(7), recorder=rec)
    engine.start()
    # Build a correct partial selection for group 0 (kanji + reading), then
    # click group 1's *meaning* card -> mismatch on the meaning face.
    g0_ids = engine.group_cards[0]
    target_word = engine.round_words[0]
    offending_word = engine.round_words[1]
    kanji_id   = next(c for c in g0_ids if engine.cards[c].face == "kanji")
    reading_id = next(c for c in g0_ids if engine.cards[c].face == "reading")
    bad_meaning_id = next(
        c for c in engine.group_cards[1] if engine.cards[c].face == "meaning"
    )
    engine.select(kanji_id)
    engine.select(reading_id)
    assert rec.confused_calls == []  # haven't messed up yet
    engine.select(bad_meaning_id)
    assert len(rec.confused_calls) == 1
    t, o, face = rec.confused_calls[0]
    assert face == "meaning"
    assert t is target_word
    assert o is offending_word
    # No spurious "matched" or extra "confused" events for the previously-
    # correct kanji/reading clicks - only the wrong one got flagged.
    assert rec.matched_calls == []


def test_no_face_collisions_in_round():
    e = new_engine(words_per_round=8)
    exprs = [w.expression for w in e.round_words]
    readings = [w.reading for w in e.round_words]
    meanings = [w.meaning.lower() for w in e.round_words]
    assert len(set(exprs)) == len(exprs)
    assert len(set(readings)) == len(readings)
    assert len(set(meanings)) == len(meanings)


# --------------------------------------------------------------------------- #
# Gamified lives (Survival / lives_mode)
# --------------------------------------------------------------------------- #
def _lives_engine(meta, **cfg):
    base = dict(decks=("test",), levels=(), words_per_round=4, lives_mode=True,
                start_lives=2, max_lives=5, heart_chance=1.0)
    base.update(cfg)
    config = GameConfig(**base)
    e = GameEngine(config, make_pool(40), rng=random.Random(7), meta_provider=meta)
    e.start()
    return e


def test_lives_heart_bounty_gain():
    # group 1 is the (learned) bounty candidate; below half + heart_chance=1.
    e = _lives_engine(lambda w: ([False] * len(w), 1))
    assert e.bounty_type[1] == "heart"
    r = _select_group(e, 1)
    assert e.lives == 3 and r.life_delta == 1 and r.bounty_type == "heart"


def test_lives_coin_bounty_when_full():
    # At/above half hearts, the bounty becomes a score coin, not a heart.
    import kanjire.game.engine as _eng
    old = _eng._COIN_CHANCE
    _eng._COIN_CHANCE = 1.0  # make the coin deterministic for the test
    try:
        e = _lives_engine(lambda w: ([False] * len(w), 1),
                          start_lives=5, max_lives=5)
        assert e.bounty_type[1] == "coin"
        before = e.score
        r = _select_group(e, 1)
        assert e.lives == 5 and r.life_delta == 0
        assert r.bonus_points > 0 and r.bounty_type == "coin"
        assert e.score == before + r.points + r.bonus_points
    finally:
        _eng._COIN_CHANCE = old


def test_lives_new_target_mismatch_is_free():
    # Building a NEW (新) target word costs no heart on a misclick.
    e = _lives_engine(lambda w: ([i == 0 for i in range(len(w))], None))
    e.select(e.group_cards[0][0])               # target = new group 0
    r = e.select(e.group_cards[2][0])           # wrong card -> mismatch
    assert r.kind == "mismatch" and r.life_delta == 0 and e.lives == 2


def test_lives_learned_target_mismatch_costs_heart():
    e = _lives_engine(lambda w: ([False] * len(w), None))  # all learned
    e.select(e.group_cards[1][0])
    r = e.select(e.group_cards[2][0])
    assert r.life_delta == -1 and e.lives == 1


def test_lives_game_over_at_zero():
    e = _lives_engine(lambda w: ([False] * len(w), None), start_lives=1, max_lives=4)
    e.select(e.group_cards[1][0])
    r = e.select(e.group_cards[2][0])
    assert e.lives == 0 and r.game_over is True and e.phase is Phase.GAME_OVER


def test_lives_max_cap():
    e = _lives_engine(lambda w: ([False] * len(w), 1), start_lives=2, max_lives=2)
    # below half? 2*2 < 2 is False -> not heart, so it's a coin; lives stay capped
    assert e.bounty_type[1] in (None, "coin")
    assert e.lives == 2


def test_lives_new_sticker_clears_only_on_clean_match():
    e = _lives_engine(lambda w: ([i == 0 for i in range(len(w))], None))
    assert e.is_new[0] is True
    # dirty it: a mismatch touching group 0, then complete it
    e.select(e.group_cards[0][0])
    e.select(e.group_cards[2][0])               # mismatch marks group 0 errored
    r = _select_group(e, 0)
    assert r.sticker_cleared is False           # not clean -> sticker stays
    # a different new group completed cleanly clears its sticker
    e2 = _lives_engine(lambda w: ([i == 0 for i in range(len(w))], None))
    r2 = _select_group(e2, 0)
    assert r2.sticker_cleared is True and e2.is_new[0] is False


def test_recent_words_cooldown():
    # A word shouldn't be re-proposed within the cooldown window of rounds, so
    # review cycles through the pool instead of repeating the same words.
    e = new_engine(words_per_round=6)
    hist = []
    for _ in range(8):
        hist.append({(w.expression, w.reading) for w in e.round_words})
        e.next_round()
    for i in range(len(hist)):
        for j in range(i + 1, min(i + 4, len(hist))):  # next 3 rounds
            assert not (hist[i] & hist[j]), \
                f"word repeated within cooldown window (rounds {i},{j})"


def test_matched_clean_flag_reflects_group_errors():
    # A group the player errored on still completes, but the recorder is told
    # the match wasn't clean (so the scheduler rates it Hard, not Good).
    rec = _Recorder()
    config = GameConfig(decks=("test",), levels=(), words_per_round=4,
                        duration=None)
    e = GameEngine(config, make_pool(30), rng=random.Random(9), recorder=rec)
    e.start()
    # Mismatch touching groups 0 (target) and 1 (offending)
    e.select(e.group_cards[0][0])
    e.select(e.group_cards[1][0])
    # Now complete group 0 (errored) and group 2 (untouched)
    _select_group(e, 0)
    _select_group(e, 2)
    assert rec.clean_flags == [False, True], rec.clean_flags


def test_session_mode_finishes_when_pool_cleared():
    pool = make_pool(5)
    config = GameConfig(decks=("test",), levels=(), words_per_round=3,
                        duration=None, session_mode=True)
    e = GameEngine(config, pool, rng=random.Random(5))
    e.start()
    assert e.session_left == 5
    cleared = set()
    last = None
    for _ in range(6):  # safety bound
        # clear the whole current board
        for g in range(len(e.round_words)):
            last = _select_group(e, g)
            cleared.add((e.round_words[g].expression, e.round_words[g].reading))
        if last.game_over:
            break
        assert last.round_complete
        e.advance()
    assert last is not None and last.game_over and last.session_complete
    assert e.session_left == 0
    assert e.phase is Phase.GAME_OVER
    assert cleared == {(w.expression, w.reading) for w in pool}


def test_session_mode_last_board_is_smaller():
    pool = make_pool(4)
    config = GameConfig(decks=("test",), levels=(), words_per_round=3,
                        duration=None, session_mode=True)
    e = GameEngine(config, pool, rng=random.Random(2))
    e.start()
    assert len(e.round_words) == 3
    for g in range(3):
        r = _select_group(e, g)
    assert r.round_complete and not r.game_over
    e.advance()
    assert len(e.round_words) == 1          # only one word left
    r = _select_group(e, 0)
    assert r.game_over and r.session_complete


def test_session_mode_words_do_not_repeat_after_clear():
    pool = make_pool(6)
    config = GameConfig(decks=("test",), levels=(), words_per_round=3,
                        duration=None, session_mode=True)
    e = GameEngine(config, pool, rng=random.Random(3))
    e.start()
    first = {(w.expression, w.reading) for w in e.round_words}
    for g in range(len(e.round_words)):
        _select_group(e, g)
    e.advance()
    second = {(w.expression, w.reading) for w in e.round_words}
    assert not (first & second), "cleared words re-dealt within a session"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
