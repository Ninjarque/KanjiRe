"""Headless tests for the multiplayer room server (raw-socket clients)."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.net.server import PROTOCOL, RoomServer


def _pool(n=20):
    return [{"kanji": f"漢{i}", "reading": f"よみ{i}",
             "meaning": f"meaning {i}"} for i in range(n)]


class _Client:
    def __init__(self, port: int, name: str):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(5)
        self.file = self.sock.makefile("rb")
        self.name = name
        self.send({"t": "hello", "name": name, "proto": PROTOCOL})
        w = self.recv()
        assert w["t"] == "welcome", w

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def recv(self):
        line = self.file.readline()
        assert line, f"{self.name}: connection closed"
        return json.loads(line.decode("utf-8"))

    def recv_state(self):
        """Skip to the next state message."""
        while True:
            m = self.recv()
            if m["t"] == "state":
                return m
            if m["t"] == "error":
                raise AssertionError(f"{self.name}: server error {m}")

    def close(self):
        # Close BOTH the file wrapper and the socket: makefile() duplicates
        # the handle, so closing only one never sends FIN to the server.
        for closer in (self.file.close, self.sock.close):
            try:
                closer()
            except OSError:
                pass


def _server():
    srv = RoomServer(host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _group_ids(state, group):
    return [c["id"] for c in state["board"] if c["group"] == group]


def test_full_two_player_game_flow():
    srv, port = _server()
    try:
        a = _Client(port, "alice")
        b = _Client(port, "bob")

        a.send({"t": "create",
                "settings": {"board_size": 4, "turns_each": 2}})
        w = a.recv()
        assert w["t"] == "welcome" and w["player"] == 0
        st = a.recv_state()
        room = st["room"]
        assert len(room) == 5 and room.isupper()

        b.send({"t": "join", "room": room.lower()})   # case-insensitive
        wb = b.recv()
        assert wb["t"] == "welcome" and wb["player"] == 1
        st = a.recv_state()
        assert st["state"]["players"] == ["alice", "bob"]
        b.recv_state()

        # Only the host can start.
        b.send({"t": "start"})
        err = b.recv()
        assert err["t"] == "error"
        a.send({"t": "start", "pool": _pool(20),
                "faces": ["kanji", "reading", "meaning"],
                "board_size": 4, "turns_each": 2})
        st = a.recv_state()
        s = st["state"]
        b.recv_state()
        assert s["started"] and not s["finished"]
        assert s["turns_total"] == 4
        assert len(s["board"]) == 4 * 3
        assert s["turn"] == 0

        # Alice completes group 0: three selects, last one completes.
        gids = _group_ids(s, s["board"][0]["group"])
        assert len(gids) == 3
        for cid in gids:
            a.send({"t": "select", "card": cid})
        events = [a.recv_state() for _ in range(3)]
        b_events = [b.recv_state() for _ in range(3)]
        last = events[-1]
        assert last["event"]["type"] == "complete"
        assert last["event"]["points"] == 100
        s = last["state"]
        assert s["scores"] == [100, 0]
        assert s["combos"] == [1, 0]
        assert s["turn"] == 1 and s["turns_used"] == 1
        # Board refilled back to 4 groups from the pool.
        assert len(s["board"]) == 12
        assert s["pool_left"] == 20 - 4 - 1

        # Bob mismatches: two cards of different groups.
        g_first = s["board"][0]["group"]
        other = next(c for c in s["board"] if c["group"] != g_first)
        b.send({"t": "select", "card": _group_ids(s, g_first)[0]})
        b.recv_state(); a.recv_state()
        b.send({"t": "select", "card": other["id"]})
        st = b.recv_state(); a.recv_state()
        assert st["event"]["type"] == "mismatch"
        s = st["state"]
        assert s["combos"] == [1, 0] and s["scores"] == [100, 0]
        assert s["turn"] == 0 and s["turns_used"] == 2

        # Out-of-turn selects are silently ignored (no broadcast).
        b.send({"t": "select", "card": s["board"][0]["id"]})
        # Alice acts; the first state anyone receives must still show
        # turns_used == 2 -> bob's out-of-turn click changed nothing.
        a.send({"t": "select", "card": s["board"][0]["id"]})
        st = a.recv_state(); b.recv_state()
        assert st["event"]["type"] in ("select", "deselect")
        assert st["state"]["turns_used"] == 2

        # Finish the game: alice mismatches, bob mismatches -> turns done.
        s = st["state"]
        cur = next(c for c in s["board"] if c.get("selected"))
        other = next(c for c in s["board"] if c["group"] != cur["group"])
        a.send({"t": "select", "card": other["id"]})
        st = a.recv_state(); b.recv_state()
        assert st["state"]["turns_used"] == 3 and st["state"]["turn"] == 1
        s = st["state"]
        b.send({"t": "select", "card": s["board"][0]["id"]})
        b.recv_state(); a.recv_state()
        other = next(c for c in s["board"]
                     if c["group"] != s["board"][0]["group"])
        b.send({"t": "select", "card": other["id"]})
        st = b.recv_state(); a.recv_state()
        assert st["state"]["finished"], st["state"]
        assert st["event"].get("finished") is True

        a.close(); b.close()
    finally:
        srv.shutdown()


def test_disconnect_advances_turn_and_reaps_room():
    srv, port = _server()
    try:
        a = _Client(port, "alice")
        b = _Client(port, "bob")
        a.send({"t": "create",
                "settings": {"board_size": 4, "cards": 2, "turns_each": 5}})
        a.recv()
        st = a.recv_state()
        room = st["room"]
        b.send({"t": "join", "room": room})
        b.recv(); b.recv_state(); a.recv_state()
        a.send({"t": "start", "pool": _pool(12),
                "faces": ["kanji", "meaning"],
                "board_size": 3, "turns_each": 5})
        st = a.recv_state(); b.recv_state()
        assert st["state"]["turn"] == 0

        # Alice (current player) disconnects -> bob gets the turn.
        a.close()
        deadline = time.time() + 5
        s = None
        while time.time() < deadline:
            m = b.recv_state()
            if m["event"] and m["event"].get("type") == "leave":
                s = m["state"]
                break
        assert s is not None and s["turn"] == 1
        assert s["connected"] == [False, True]

        # Bob can still play.
        gid = s["board"][0]["group"]
        ids = [c["id"] for c in s["board"] if c["group"] == gid]
        for cid in ids:
            b.send({"t": "select", "card": cid})
        for _ in range(len(ids)):
            st = b.recv_state()
        assert st["event"]["type"] == "complete"
        assert st["state"]["scores"][1] == 100
        b.close()

        # Room reaps once everyone is gone (checked via a new create).
        time.sleep(0.3)
        c = _Client(port, "carol")
        c.send({"t": "create", "settings": {"board_size": 4}})
        c.recv(); c.recv_state()
        assert room not in srv.hub.rooms or not srv.hub.rooms[room].alive()
        c.close()
    finally:
        srv.shutdown()


def test_version_mismatch_rejected():
    srv, port = _server()
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.settimeout(5)
        f = s.makefile("rb")
        s.sendall(b'{"t": "hello", "name": "x", "proto": 999}\n')
        msg = json.loads(f.readline())
        assert msg["t"] == "error" and "version" in msg["msg"]
        s.close()
    finally:
        srv.shutdown()


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
