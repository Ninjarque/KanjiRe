"""Code-only multiplayer, driven through an in-process broker (no network).

Covers the whole relay stack: host creates a room, a joiner finds it by code
alone, turns rotate, matches remove+refill for everyone, retained state gives
late joiners the live board, and a dropped player's will frees their turn.
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.net.room_client import RoomClient
from kanjire.net.transport import LoopbackBroker, LoopbackTransport


def _pool(n=20):
    return [{"kanji": f"漢{i}", "reading": f"よみ{i}",
             "meaning": f"meaning {i}"} for i in range(n)]


def _mk(broker, name, seed=0):
    c = RoomClient(transport=LoopbackTransport(broker),
                   rng=random.Random(seed))
    assert c.connect(name) is None
    return c


def _last_state(client):
    st = None
    for m in client.poll():
        if m.get("t") == "state":
            st = m["state"]
    return st


def _drain_state(client):
    """Latest state, keeping the client's own view updated."""
    st = _last_state(client)
    assert st is not None, "no state received"
    return st


#: Monotonic-ish clock for the reveal ticks; each call moves well past the hold.
_T = [1000.0]


def _finish_reveal(host):
    """Run the host's clock past the completed-group reveal.

    A finished group is now held on the board for everyone to look at, and only
    a tick clears it - so tests that complete a group must let time pass.
    """
    from kanjire.net.server import REVEAL_SECONDS

    host.tick(_T[0])                       # arms the deadline
    _T[0] += REVEAL_SECONDS + 0.5
    host.tick(_T[0])                       # deadline passed: clear + next turn
    _T[0] += 1.0


def _group_ids(state, group):
    return [c["id"] for c in state["board"] if c["group"] == group]


def test_join_by_code_alone_and_play_turns():
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 1)
    host.send({"t": "create",
               "settings": {"board_size": 4, "turns_each": 2}})
    code = host.code
    assert len(code) == 5 and code.isupper()
    msgs = host.poll()
    assert msgs[0] == {"t": "welcome", "player": 0}

    # The joiner knows NOTHING but the code - no address anywhere.
    guest = _mk(broker, "bob", 2)
    guest.send({"t": "join", "room": code.lower()})   # case-insensitive
    gmsgs = guest.poll()
    assert {"t": "welcome", "player": 1} in gmsgs
    gst = [m for m in gmsgs if m.get("t") == "state"][-1]["state"]
    assert gst["players"] == ["alice", "bob"]
    hst = _drain_state(host)
    assert hst["players"] == ["alice", "bob"]

    host.send({"t": "start", "pool": _pool(20),
               "faces": ["kanji", "reading", "meaning"],
               "board_size": 4, "turns_each": 2})
    hst = _drain_state(host)
    gst = _drain_state(guest)
    assert hst["started"] and gst["started"]
    assert hst["turns_total"] == 4
    assert len(gst["board"]) == 4 * 3
    # Everyone sees the SAME board, card for card.
    assert [c["id"] for c in gst["board"]] == [c["id"] for c in hst["board"]]
    assert gst["turn"] == 0

    # Host's turn: complete a group.
    gids = _group_ids(hst, hst["board"][0]["group"])
    for cid in gids:
        host.send({"t": "select", "card": cid})
    hst = _drain_state(host)
    gst = _drain_state(guest)
    assert hst["scores"] == [100, 0] and hst["combos"] == [1, 0]
    # The group is HELD up for everyone first - it doesn't vanish on the click.
    assert sorted(gst["revealing"]) == sorted(gids)
    assert hst["turn"] == 0, "the turn passes when the reveal ends, not before"
    _finish_reveal(host)
    hst = _drain_state(host)
    gst = _drain_state(guest)
    assert hst["turn"] == 1 and hst["turns_used"] == 1
    # Cards were removed and the board refilled for BOTH players.
    assert len(hst["board"]) == 12 and len(gst["board"]) == 12
    assert gst["scores"] == [100, 0]
    assert all(cid not in [c["id"] for c in gst["board"]] for cid in gids)
    assert gst["pool_left"] == 20 - 4 - 1

    # Guest's turn: a mismatch resets their combo and passes the turn back.
    ga = gst["board"][0]["group"]
    a_id = _group_ids(gst, ga)[0]
    b_id = next(c["id"] for c in gst["board"] if c["group"] != ga)
    guest.send({"t": "select", "card": a_id})
    _drain_state(guest); _drain_state(host)
    guest.send({"t": "select", "card": b_id})
    gst = _drain_state(guest)
    hst = _drain_state(host)
    assert gst["combos"] == [1, 0] and gst["scores"] == [100, 0]
    assert gst["turn"] == 0 and hst["turn"] == 0
    assert gst["turns_used"] == 2


def test_completed_group_is_held_on_screen_for_everyone():
    """The whole point of the pause: a group used to be scored and swept off the
    board inside the same click, so nobody except the player who completed it
    ever saw which cards went together."""
    from kanjire.net.server import REVEAL_SECONDS

    broker = LoopbackBroker()
    host = _mk(broker, "alice", 50)
    host.send({"t": "create", "settings": {"board_size": 4, "cards": 2}})
    guest = _mk(broker, "bob", 51)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20), "faces": ["kanji", "meaning"],
               "board_size": 4, "turns_each": 3})
    hst = _drain_state(host); guest.poll()

    gids = _group_ids(hst, hst["board"][0]["group"])
    for cid in gids:
        host.send({"t": "select", "card": cid})
    hst = _drain_state(host)
    gst = _drain_state(guest)

    # 1) The cards are still on the board - for the GUEST too, who has to be
    #    able to look at them - flagged matched+selected so they stand out.
    board_ids = [c["id"] for c in gst["board"]]
    assert all(cid in board_ids for cid in gids), "the group vanished instantly"
    held = [c for c in gst["board"] if c["id"] in gids]
    assert all(c.get("matched") and c.get("selected") for c in held), held
    assert sorted(gst["revealing"]) == sorted(gids)
    assert sorted(hst["revealing"]) == sorted(gids)
    # 2) Scored immediately, but the turn is still the completer's.
    assert gst["scores"] == [100, 0]
    assert gst["turn"] == 0

    # 3) Nobody can click through the hold - not even the player whose turn it is.
    other = next(c["id"] for c in hst["board"] if c["id"] not in gids)
    host.send({"t": "select", "card": other})
    assert not [m for m in host.poll() if m.get("t") == "state"], \
        "a click during the reveal changed the board"

    # 4) Half-way through, it's still up.
    t = 2000.0
    host.tick(t)
    host.tick(t + REVEAL_SECONDS / 2)
    assert host.room.pending_clear, "the reveal ended early"

    # 5) Once the hold is up the group clears, the board refills and the turn
    #    passes - for everyone, at the same moment.
    host.tick(t + REVEAL_SECONDS + 0.1)
    hst = _drain_state(host)
    gst = _drain_state(guest)
    assert not hst["revealing"] and not gst["revealing"]
    assert all(cid not in [c["id"] for c in gst["board"]] for cid in gids)
    assert len(gst["board"]) == 8 and len(hst["board"]) == 8
    assert gst["turn"] == 1 and hst["turn"] == 1


def test_hover_pointer_is_shown_to_everyone_but_only_on_your_turn():
    """Dwelling on a card shows it to the whole room, so the player on turn can
    think out loud. It's theirs alone, and it must not outlive their turn."""
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 60)
    host.send({"t": "create", "settings": {"board_size": 4, "cards": 2}})
    guest = _mk(broker, "bob", 61)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20), "faces": ["kanji", "meaning"],
               "board_size": 4, "turns_each": 3})
    hst = _drain_state(host); guest.poll()
    assert hst["turn"] == 0 and hst["pointer"] is None

    target = hst["board"][2]["id"]
    host.send({"t": "point", "card": target})
    assert _drain_state(host)["pointer"] == target
    assert _drain_state(guest)["pointer"] == target, \
        "the other player never saw what the current one is looking at"

    # Moving off the card withdraws it.
    host.send({"t": "point", "card": None})
    assert _drain_state(guest)["pointer"] is None

    # It's the turn-holder's pointer: bob can't point on alice's turn.
    guest.send({"t": "point", "card": target})
    assert not [m for m in guest.poll() if m.get("t") == "state"]
    assert host.room.pointer is None

    # And it never survives the turn changing.
    host.send({"t": "point", "card": target})
    _drain_state(host); _drain_state(guest)
    ga = hst["board"][0]["group"]
    a_id = _group_ids(hst, ga)[0]
    b_id = next(c["id"] for c in hst["board"] if c["group"] != ga)
    host.send({"t": "select", "card": a_id})
    host.poll(); guest.poll()
    host.send({"t": "select", "card": b_id})       # mismatch -> turn passes
    hst2 = _drain_state(host)
    assert hst2["turn"] == 1
    assert hst2["pointer"] is None, "alice's pointer stayed up on bob's turn"


def test_out_of_turn_clicks_are_ignored():
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 3)
    host.send({"t": "create",
               "settings": {"board_size": 4, "cards": 2, "turns_each": 5}})
    guest = _mk(broker, "bob", 4)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20),
               "faces": ["kanji", "reading", "meaning"],
               "board_size": 4, "turns_each": 2})
    hst = _drain_state(host); _drain_state(guest)
    assert hst["turn"] == 0

    # It is the host's turn; the guest clicking must change nothing at all.
    before = hst["turns_used"]
    guest.send({"t": "select", "card": hst["board"][0]["id"]})
    assert _last_state(guest) is None      # no broadcast -> no state
    assert host.room.turns_used == before
    assert not any(c.get("selected") for c in host.room.cards.values())


def test_reconnecting_player_resyncs_from_retained_state():
    """The retained snapshot is what makes reconnects trivial: re-subscribe
    and the live board arrives with no handshake."""
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 5)
    host.send({"t": "create",
               "settings": {"board_size": 4, "cards": 2, "turns_each": 5}})
    guest = _mk(broker, "bob", 6)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20),
               "faces": ["kanji", "reading", "meaning"],
               "board_size": 4, "turns_each": 2})
    hst = _drain_state(host); guest.poll()

    # Host scores, so the room state is no longer the opening board.
    gids = _group_ids(hst, hst["board"][0]["group"])
    for cid in gids:
        host.send({"t": "select", "card": cid})
    hst = _drain_state(host); guest.poll()

    # A fresh transport subscribing to the room receives the CURRENT state
    # immediately (retained), not the opening one.
    watcher = LoopbackTransport(broker)
    seen = []
    watcher.on_message(lambda t, p: seen.append(p))
    watcher.connect()
    watcher.subscribe(f"kanjire/mp/v1/{host.code}/state")
    import json as _json
    assert seen, "retained state was not replayed to a new subscriber"
    state = _json.loads(seen[-1].decode("utf-8"))["state"]
    assert state["started"] and state["scores"][0] == 100
    assert [c["id"] for c in state["board"]] == [c["id"] for c in hst["board"]]


def test_joining_a_started_game_is_rejected_clearly():
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 7)
    host.send({"t": "create",
               "settings": {"board_size": 4, "cards": 2, "turns_each": 5}})
    guest = _mk(broker, "bob", 8)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20),
               "faces": ["kanji", "reading", "meaning"],
               "board_size": 4, "turns_each": 2})
    host.poll(); guest.poll()

    late = _mk(broker, "carol", 9)
    late.send({"t": "join", "room": host.code})
    errs = [m for m in late.poll() if m.get("t") == "error"]
    assert errs and "already started" in errs[-1]["msg"]
    assert late.me == -1
    assert host.room.snapshot()["players"] == ["alice", "bob"]


def test_dropped_player_frees_the_turn():
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 8)
    host.send({"t": "create",
               "settings": {"board_size": 4, "cards": 2, "turns_each": 5}})
    guest = _mk(broker, "bob", 9)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20),
               "faces": ["kanji", "reading", "meaning"],
               "board_size": 4, "turns_each": 2})
    hst = _drain_state(host); guest.poll()

    # Hand the turn to bob (host mismatches), then bob vanishes ungracefully.
    ga = hst["board"][0]["group"]
    a_id = _group_ids(hst, ga)[0]
    b_id = next(c["id"] for c in hst["board"] if c["group"] != ga)
    host.send({"t": "select", "card": a_id})
    host.poll(); guest.poll()
    host.send({"t": "select", "card": b_id})
    hst = _drain_state(host); guest.poll()
    assert hst["turn"] == 1

    broker.disconnect(guest.transport)     # fires bob's will
    hst = _drain_state(host)
    assert hst["connected"] == [True, False]
    assert hst["turn"] == 0, "the turn must come back to the remaining player"

    # And the host can keep playing alone.
    gids = _group_ids(hst, hst["board"][0]["group"])
    for cid in gids:
        host.send({"t": "select", "card": cid})
    hst = _drain_state(host)
    assert hst["scores"][0] >= 100


def test_silent_player_is_timed_out_and_their_turns_leave_with_them():
    """A killed app / dead wifi sends no 'bye' - the broker's will only fires on
    a *clean* disconnect. Without heartbeats the room sat forever on the turn of
    someone who was never coming back."""
    from kanjire.net import config

    broker = LoopbackBroker()
    host = _mk(broker, "alice", 30)
    host.send({"t": "create", "settings": {"board_size": 4, "cards": 2}})
    guest = _mk(broker, "bob", 31)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20), "faces": ["kanji", "meaning"],
               "board_size": 4, "turns_each": 3})
    hst = _drain_state(host); guest.poll()
    assert hst["connected"] == [True, True]
    assert hst["turns_left"] == 6          # 3 turns each, two players

    t = 100.0
    host.tick(t)
    guest.tick(t)          # bob is alive and pinging
    guest.poll()
    assert not [m for m in host.poll() if m.get("t") == "state"], \
        "a live player must not trigger any state change"
    assert host.room.connected == [True, True]

    # Bob's machine dies: no bye, no will, just silence. Only the host ticks.
    for step in range(1, 8):
        host.tick(t + step * config.HEARTBEAT_SECONDS)
    hst = _drain_state(host)
    assert hst["connected"] == [True, False], \
        "a silent player was never dropped - the game would stall on their turn"
    assert hst["turn"] == 0, "the turn must come back to the player still here"
    # Bob's 3 unplayed turns leave with him instead of being handed to alice.
    assert hst["turns_left"] == 3, hst["turns_left"]


def test_guest_notices_a_vanished_host():
    from kanjire.net import config

    broker = LoopbackBroker()
    host = _mk(broker, "alice", 40)
    host.send({"t": "create", "settings": {"board_size": 4}})
    guest = _mk(broker, "bob", 41)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()

    t = 500.0
    guest.tick(t)
    guest.poll()
    for step in range(1, 8):               # host never speaks again
        guest.tick(t + step * config.HEARTBEAT_SECONDS)
    errs = [m for m in guest.poll()
            if m.get("t") == "error" and "host" in m.get("msg", "")]
    assert errs, "guest sat forever on a dead room instead of being told"


def test_host_settings_are_broadcast_live_and_guests_cannot_change_them():
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 20)
    host.send({"t": "create", "settings": {"board_size": 4}})
    guest = _mk(broker, "bob", 21)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()

    # The host tweaks the room; the guest sees it WITHOUT doing anything.
    host.send({"t": "config", "settings": {"cards": 4, "levels": [5, 4],
                                           "board_size": 8, "turns_each": 15}})
    gst = _drain_state(guest)
    assert gst["settings"]["cards"] == 4
    assert gst["settings"]["levels"] == [4, 5]
    assert gst["settings"]["board_size"] == 8
    assert gst["settings"]["turns_each"] == 15

    # A guest sending config must not be able to change the room.
    guest.send({"t": "config", "settings": {"cards": 2}})
    host.poll(); guest.poll()
    assert host.room.settings["cards"] == 4, "a guest changed the settings!"

    # Settings actually shape the game: 4 cards/word, 8 words on the board.
    host.send({"t": "start", "pool": _pool(30),
               "faces": ["kanji", "reading", "romaji", "meaning"],
               "board_size": 8, "turns_each": 15})
    hst = _drain_state(host)
    gst = _drain_state(guest)
    assert len(hst["board"]) == 8 * 4
    assert {c["face"] for c in gst["board"]} == {"kanji", "reading",
                                                 "romaji", "meaning"}
    assert hst["turns_total"] == 15 * 2

    # ...and are locked once the game is running.
    host.send({"t": "config", "settings": {"cards": 2}})
    assert host.room.settings["cards"] == 4


def test_pause_blocks_play_and_back_to_lobby_resets_for_a_new_game():
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 22)
    host.send({"t": "create", "settings": {"board_size": 4, "turns_each": 5}})
    guest = _mk(broker, "bob", 23)
    guest.send({"t": "join", "room": host.code})
    host.poll(); guest.poll()
    host.send({"t": "start", "pool": _pool(20),
               "faces": ["kanji", "reading", "meaning"],
               "board_size": 4, "turns_each": 5})
    hst = _drain_state(host); guest.poll()

    # Pause: everyone is told, and clicks do nothing at all.
    host.send({"t": "pause"})
    gst = _drain_state(guest)
    hst = _drain_state(host)          # drain, so the next poll is only moves
    assert gst["paused"] is True and hst["paused"] is True
    gids = _group_ids(hst, hst["board"][0]["group"])
    for cid in gids:
        host.send({"t": "select", "card": cid})
    assert _last_state(host) is None, "a paused game accepted a move"
    assert _last_state(guest) is None
    assert host.room.scores[0] == 0

    # Resume: play works again.
    host.send({"t": "resume"})
    hst = _drain_state(host); guest.poll()
    assert hst["paused"] is False
    for cid in gids:
        host.send({"t": "select", "card": cid})
    hst = _drain_state(host); guest.poll()
    assert hst["scores"][0] == 100

    # Back to the lobby: game gone, scores cleared, players kept, settings
    # editable again - ready for a differently-configured rematch.
    host.send({"t": "lobby"})
    hst = _drain_state(host)
    gst = _drain_state(guest)
    assert not hst["started"] and not hst["finished"] and not hst["paused"]
    assert hst["board"] == [] and hst["scores"] == [0, 0]
    assert hst["players"] == ["alice", "bob"] and gst["players"] == hst["players"]

    host.send({"t": "config", "settings": {"cards": 2}})
    hst = _drain_state(host)
    assert hst["settings"]["cards"] == 2, "settings must unlock in the lobby"
    host.send({"t": "start", "pool": _pool(20), "faces": ["kanji", "meaning"],
               "board_size": 4, "turns_each": 5})
    hst = _drain_state(host)
    assert hst["started"] and len(hst["board"]) == 4 * 2
    assert hst["scores"] == [0, 0]


def test_wrong_code_reports_not_found():
    broker = LoopbackBroker()
    host = _mk(broker, "alice", 10)
    host.send({"t": "create", "settings": {"board_size": 4}})
    lost = _mk(broker, "bob", 11)
    lost.send({"t": "join", "room": "ZZZZZ"})
    # Retries a few times, then gives up with a clear error.
    errs = [m for m in lost.poll() if m.get("t") == "error"]
    for _ in range(10):
        if errs:
            break
        lost._guest_state({"state": {"client_ids": []}})
        errs = [m for m in lost.poll() if m.get("t") == "error"]
    assert errs and "not found" in errs[-1]["msg"]


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
