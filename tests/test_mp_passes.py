"""Multiplayer passes: blanks shuffle with the cards, board re-deals per pass.

Drives net.server.Room directly (no sockets): with passes > 1 a cleared
group leaves None blanks (no rolling refill), clearing the whole board
re-deals the SAME words for the next pass, and after the last pass a fresh
word-set is drawn. passes == 1 must keep the historical rolling behaviour.
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.net.server import Room


class _FakeHandler:
    def send(self, obj):
        pass


def _pool(n):
    return [{"kanji": f"漢{i}", "reading": f"かん{i}", "meaning": f"m{i}"}
            for i in range(n)]


def _make_room(passes, board_size=3, players=2):
    room = Room("TEST", rng=random.Random(7))
    for i in range(players):
        room.add_player(_FakeHandler(), f"p{i}")
    room.set_settings({"passes": passes, "board_size": board_size,
                       "turns_each": 15})
    room.start(_pool(20), faces=["kanji", "reading"],
               board_size=board_size, turns_each=15)
    assert room.started
    return room


def _complete_one_group(room):
    """The player on turn clicks out one full group; then time passes."""
    player = room.turn
    gid = next(c["group"] for cid in room.board if cid is not None
               for c in [room.cards[cid]] if not c.get("matched"))
    ids = [cid for cid, c in room.cards.items() if c["group"] == gid]
    for cid in ids:
        room.select(player, cid)
    assert room.pending_clear, "group did not complete"
    room.tick(100.0)          # arms the reveal deadline
    room.tick(200.0)          # past REVEAL_SECONDS: clears
    return ids


def _board_words(room):
    return {c["text"] for c in room.cards.values() if c["face"] == "kanji"}


def test_single_pass_keeps_rolling_refill():
    room = _make_room(passes=1)
    n0 = len(room.board)
    _complete_one_group(room)
    assert None not in room.board
    assert len(room.board) == n0          # cleared group replaced from pool
    assert len(room.cards) == n0


def test_multi_pass_blanks_and_redeal():
    room = _make_room(passes=2, board_size=3)
    n0 = len(room.board)                  # 3 groups x 2 faces
    words_pass1 = _board_words(room)
    assert room.pass_no == 1

    _complete_one_group(room)
    # blanks, not refills: same slot count, 2 fewer cards, no new group
    assert len(room.board) == n0
    assert room.board.count(None) == 2
    assert len(room.cards) == n0 - 2
    assert _board_words(room) < words_pass1

    _complete_one_group(room)
    _complete_one_group(room)
    # whole board cleared -> SAME words re-dealt, next pass
    assert room.pass_no == 2
    assert room.board.count(None) == 0
    assert len(room.board) == n0
    assert _board_words(room) == words_pass1
    assert not room.finished

    # clear the second (last) pass -> fresh words from the pool, pass resets
    for _ in range(3):
        _complete_one_group(room)
    assert room.pass_no == 1
    words_round2 = _board_words(room)
    assert words_round2 and words_round2.isdisjoint(words_pass1)
    assert not room.finished


def test_multi_pass_snapshot_carries_blanks_and_pass():
    room = _make_room(passes=3, board_size=3)
    _complete_one_group(room)
    snap = room.snapshot()
    assert snap["passes"] == 3 and snap["pass_no"] == 1
    assert snap["board"].count(None) == 2
    live = [c for c in snap["board"] if c is not None]
    assert all("id" in c for c in live)


def test_multi_pass_pool_exhaustion_finishes():
    room = Room("TEST", rng=random.Random(3))
    room.add_player(_FakeHandler(), "p0")
    room.add_player(_FakeHandler(), "p1")
    room.set_settings({"passes": 2, "board_size": 4, "turns_each": 50})
    room.start(_pool(2), faces=["kanji", "reading"],
               board_size=4, turns_each=50)   # pool == one 2-group round
    for _ in range(2):                        # pass 1
        _complete_one_group(room)
    assert room.pass_no == 2
    for _ in range(2):                        # pass 2 — pool now empty
        _complete_one_group(room)
    assert room.finished
