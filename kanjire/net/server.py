"""The KanjiRe room server: rooms, turns, and a shared card board.

Run standalone (``python scripts/run_server.py [port]``) or in-process via
:func:`start_in_thread` when hosting from inside the app.

Protocol (JSON per line, UTF-8):

Client -> server
    {"t": "hello",  "name": str, "proto": 1}
    {"t": "create", "pool": [{"kanji":..,"reading":..,"meaning":..}, ...],
                    "faces": [...], "board_size": int, "turns_each": int}
    {"t": "join",   "room": "ABCD"}
    {"t": "start"}                      (host only)
    {"t": "select", "card": int}        (current player only)

Server -> client
    {"t": "welcome", "player": int}
    {"t": "state",  "room": "ABCD", "state": {...}, "event": {...}|null}
    {"t": "error",  "msg": str}

The state snapshot carries everything a client needs to render:
players/scores/combos, whose turn it is, turns left, the finished flag and
the full board (id/group/face/text/matched/selected per card).
"""
from __future__ import annotations

import json
import random
import socket
import socketserver
import string
import threading

PROTOCOL = 1
DEFAULT_PORT = 24857

#: Per-group score, multiplied by the scorer's combo (mirrors solo play).
BASE_POINTS = 100
_MAX_NAME = 18
_MAX_PLAYERS = 8
_MAX_ROOMS = 64


def _room_code(rng) -> str:
    from kanjire.net import config
    return "".join(rng.choice(string.ascii_uppercase)
                   for _ in range(config.CODE_LEN))


class Room:
    """One shared game. All mutation happens under ``self.lock``."""

    def __init__(self, code: str, faces, pool, board_size: int,
                 turns_each: int, rng=None) -> None:
        self.code = code
        self.lock = threading.RLock()
        self.rng = rng or random.Random()
        self.faces = [f for f in faces if f] or ["kanji", "reading", "meaning"]
        #: Remaining word entries ({face: text}), drawn from as groups clear.
        self.pool = list(pool)
        self.rng.shuffle(self.pool)
        self.board_size = max(2, min(12, int(board_size)))
        self.turns_each = max(1, min(50, int(turns_each)))

        self.clients: list["Handler"] = []
        self.names: list[str] = []
        self.scores: list[int] = []
        self.combos: list[int] = []
        self.connected: list[bool] = []

        self.started = False
        self.finished = False
        self.turn = 0                  # index into players
        self.turns_used = 0
        self.turns_total = 0

        self.cards: dict[int, dict] = {}
        self.board: list[int] = []
        self.selection: list[int] = []
        self.current_group: int | None = None
        self._next_card = 0
        self._next_group = 0

    # ---- membership -------------------------------------------------- #
    def add_player(self, handler: "Handler", name: str) -> int:
        with self.lock:
            self.clients.append(handler)
            self.names.append(name)
            self.scores.append(0)
            self.combos.append(0)
            self.connected.append(True)
            return len(self.clients) - 1

    def drop_player(self, idx: int) -> None:
        with self.lock:
            if 0 <= idx < len(self.connected):
                self.connected[idx] = False
                self.clients[idx] = None
            if (self.started and not self.finished
                    and self.turn == idx and any(self.connected)):
                self._advance_turn(consume=False)

    def alive(self) -> bool:
        with self.lock:
            return any(self.connected)

    # ---- game flow ---------------------------------------------------- #
    def _deal_group(self, entry: dict) -> None:
        g = self._next_group
        self._next_group += 1
        for face in self.faces:
            self.cards[self._next_card] = {
                "id": self._next_card, "group": g, "face": face,
                "text": str(entry.get(face) or "?"),
            }
            self.board.append(self._next_card)
            self._next_card += 1

    def start(self) -> None:
        with self.lock:
            if self.started:
                return
            self.started = True
            self.turns_total = self.turns_each * len(self.clients)
            for _ in range(min(self.board_size, len(self.pool))):
                self._deal_group(self.pool.pop())
            self.rng.shuffle(self.board)

    def _advance_turn(self, consume: bool = True) -> None:
        if consume:
            self.turns_used += 1
        self.selection.clear()
        self.current_group = None
        for cid in self.cards:
            self.cards[cid]["selected"] = False
        if self.turns_used >= self.turns_total or not self.board:
            self.finished = True
            return
        n = len(self.clients)
        for step in range(1, n + 1):
            cand = (self.turn + step) % n
            if self.connected[cand]:
                self.turn = cand
                return
        self.finished = True   # nobody left

    def select(self, player: int, card_id: int) -> dict | None:
        """Apply one click. Returns the event dict (or None for a no-op)."""
        with self.lock:
            if (not self.started or self.finished or player != self.turn):
                return None
            card = self.cards.get(card_id)
            if card is None or card.get("matched"):
                return None
            if card_id in self.selection:
                self.selection.remove(card_id)
                card["selected"] = False
                if not self.selection:
                    self.current_group = None
                return {"type": "deselect", "cards": [card_id],
                        "player": player}
            if not self.selection:
                self.selection.append(card_id)
                card["selected"] = True
                self.current_group = card["group"]
                return {"type": "select", "cards": [card_id],
                        "player": player}
            if card["group"] == self.current_group:
                self.selection.append(card_id)
                card["selected"] = True
                group_ids = [c["id"] for c in self.cards.values()
                             if c["group"] == self.current_group]
                if set(self.selection) == set(group_ids):
                    return self._complete_group(player, group_ids)
                return {"type": "select", "cards": [card_id],
                        "player": player}
            return self._mismatch(player, card_id)

    def _complete_group(self, player: int, group_ids: list[int]) -> dict:
        self.combos[player] += 1
        points = BASE_POINTS * self.combos[player]
        self.scores[player] += points
        texts = {self.cards[c]["face"]: self.cards[c]["text"]
                 for c in group_ids}
        for cid in group_ids:
            self.cards.pop(cid, None)
            if cid in self.board:
                self.board.remove(cid)
        if self.pool:
            self._deal_group(self.pool.pop())
        self.rng.shuffle(self.board)
        event = {"type": "complete", "player": player, "points": points,
                 "combo": self.combos[player], "word": texts}
        self._advance_turn()
        return event

    def _mismatch(self, player: int, offending: int) -> dict:
        affected = list(self.selection) + [offending]
        self.combos[player] = 0
        event = {"type": "mismatch", "player": player, "cards": affected}
        self._advance_turn()
        return event

    # ---- snapshots ---------------------------------------------------- #
    def snapshot(self) -> dict:
        with self.lock:
            return {
                "players": list(self.names),
                "connected": list(self.connected),
                "scores": list(self.scores),
                "combos": list(self.combos),
                "host": 0,
                "started": self.started,
                "finished": self.finished,
                "turn": self.turn,
                "turns_used": self.turns_used,
                "turns_total": self.turns_total,
                "faces": list(self.faces),
                "pool_left": len(self.pool),
                "board": [dict(self.cards[cid]) for cid in self.board],
            }

    def broadcast(self, event: dict | None = None) -> None:
        msg = {"t": "state", "room": self.code,
               "state": self.snapshot(), "event": event}
        with self.lock:
            handlers = [h for h in self.clients if h is not None]
        for h in handlers:
            h.send(msg)


class Hub:
    """All rooms on this server."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rooms: dict[str, Room] = {}
        self.rng = random.Random()

    def create(self, faces, pool, board_size, turns_each) -> Room:
        with self.lock:
            self._reap()
            if len(self.rooms) >= _MAX_ROOMS:
                raise RuntimeError("server full")
            code = _room_code(self.rng)
            while code in self.rooms:
                code = _room_code(self.rng)
            room = Room(code, faces, pool, board_size, turns_each)
            self.rooms[code] = room
            return room

    def get(self, code: str) -> Room | None:
        with self.lock:
            return self.rooms.get((code or "").strip().upper())

    def _reap(self) -> None:
        dead = [c for c, r in self.rooms.items() if not r.alive()]
        for c in dead:
            del self.rooms[c]


class Handler(socketserver.StreamRequestHandler):
    """One connected client."""

    def setup(self) -> None:
        super().setup()
        self.name = "?"
        self.room: Room | None = None
        self.player = -1
        self.send_lock = threading.Lock()
        try:
            self.request.settimeout(600)   # drop truly dead sockets
        except OSError:
            pass

    def send(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self.send_lock:
                self.wfile.write(data)
                self.wfile.flush()
        except OSError:
            pass

    def handle(self) -> None:
        hub: Hub = self.server.hub
        for raw in self.rfile:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.send({"t": "error", "msg": "bad message"})
                continue
            t = msg.get("t")
            if t == "hello":
                if msg.get("proto") != PROTOCOL:
                    self.send({"t": "error", "msg": "version mismatch"})
                    return
                self.name = str(msg.get("name") or "?")[:_MAX_NAME] or "?"
                self.send({"t": "welcome"})
            elif t == "create":
                pool = msg.get("pool") or []
                if self.room is not None or not (4 <= len(pool) <= 500):
                    self.send({"t": "error", "msg": "bad create"})
                    continue
                try:
                    room = hub.create(msg.get("faces") or [],
                                      pool,
                                      msg.get("board_size") or 6,
                                      msg.get("turns_each") or 10)
                except RuntimeError as exc:
                    self.send({"t": "error", "msg": str(exc)})
                    continue
                self.room = room
                self.player = room.add_player(self, self.name)
                self.send({"t": "welcome", "player": self.player})
                room.broadcast({"type": "join", "player": self.player})
            elif t == "join":
                room = hub.get(msg.get("room"))
                if self.room is not None or room is None:
                    self.send({"t": "error", "msg": "no such room"})
                    continue
                with room.lock:
                    full = (len(room.clients) >= _MAX_PLAYERS or room.started)
                if full:
                    self.send({"t": "error", "msg": "room closed"})
                    continue
                self.room = room
                self.player = room.add_player(self, self.name)
                self.send({"t": "welcome", "player": self.player})
                room.broadcast({"type": "join", "player": self.player})
            elif t == "start":
                if self.room is None or self.player != 0:
                    self.send({"t": "error", "msg": "not host"})
                    continue
                self.room.start()
                self.room.broadcast({"type": "start"})
            elif t == "select":
                if self.room is None:
                    continue
                event = self.room.select(self.player,
                                         int(msg.get("card", -1)))
                if event is not None:
                    if self.room.finished:
                        event["finished"] = True
                    self.room.broadcast(event)
            elif t == "bye":
                break

    def finish(self) -> None:
        if self.room is not None:
            self.room.drop_player(self.player)
            if self.room.alive():
                self.room.broadcast({"type": "leave", "player": self.player})
        super().finish()


class RoomServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        super().__init__((host, port), Handler)
        self.hub = Hub()


def start_in_thread(host: str = "0.0.0.0",
                    port: int = DEFAULT_PORT) -> RoomServer:
    """Run a server on a daemon thread (in-app hosting). Returns it."""
    server = RoomServer(host, port)
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="kanjire-room-server").start()
    return server


def main(argv=None) -> int:
    import sys
    args = argv if argv is not None else sys.argv[1:]
    port = int(args[0]) if args else DEFAULT_PORT
    server = RoomServer(port=port)
    print(f"KanjiRe room server listening on 0.0.0.0:{port} "
          f"(protocol {PROTOCOL}). Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
