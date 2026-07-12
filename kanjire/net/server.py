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
import time

#: 2 = lobby settings (create carries settings, start carries the pool),
#: plus pause / resume / back-to-lobby. Old clients are rejected at hello.
PROTOCOL = 2
DEFAULT_PORT = 24857

#: Per-group score, multiplied by the scorer's combo (mirrors solo play).
BASE_POINTS = 100

#: How long a completed group stays on the board before it's cleared, so every
#: player gets to see which cards went together (and read the word) rather than
#: watching them vanish the instant the clicker finished the set.
REVEAL_SECONDS = 2.0
_MAX_NAME = 18
_MAX_PLAYERS = 8
_MAX_ROOMS = 64


def _room_code(rng) -> str:
    from kanjire.net import config
    return "".join(rng.choice(string.ascii_uppercase)
                   for _ in range(config.CODE_LEN))


#: The game settings the host tunes in the lobby. Everyone sees them live,
#: and they only take effect when the host starts a game (the host's app
#: re-samples the word pool from them at that moment).
DEFAULT_SETTINGS = {
    "deck": "jlpt",
    "levels": [5],
    "board_size": 6,
    "cards": 4,          # cards per word: 2 | 3 | 4 (4 adds the romaji face)
    "turns_each": 10,
    "writing": "off",    # horizontal | mixed | vertical  ("off" == horizontal)
    "fonts": "fixed",    # fixed | random
}

#: Accepted values for the presentation settings (kept in sync with the menu's
#: WRITING_OPTIONS so multiplayer and single player speak the same language).
_WRITING_VALUES = ("off", "random", "all")
_FONT_VALUES = ("fixed", "random")


class Room:
    """One shared game. All mutation happens under ``self.lock``."""

    def __init__(self, code: str, settings: dict | None = None,
                 rng=None) -> None:
        self.code = code
        self.lock = threading.RLock()
        self.rng = rng or random.Random()

        self.started = False       # set_settings() reads this, so it comes first
        self.finished = False
        self.paused = False

        #: Lobby-editable settings, broadcast to everyone as the host tweaks.
        self.settings = dict(DEFAULT_SETTINGS)
        if settings:
            self.set_settings(settings)

        # Configured at start() from the host's freshly-sampled pool.
        self.faces = ["kanji", "reading", "meaning"]
        self.pool: list[dict] = []
        self.board_size = 6
        self.turns_each = 10

        self.clients: list["Handler"] = []
        self.names: list[str] = []
        self.scores: list[int] = []
        self.combos: list[int] = []
        self.connected: list[bool] = []
        self.turns_taken: list[int] = []

        self.turn = 0                  # index into players
        self.turns_used = 0
        self.turns_total = 0

        self.cards: dict[int, dict] = {}
        self.board: list[int] = []
        self.selection: list[int] = []
        self.current_group: int | None = None
        #: A completed group being shown to everyone before it's cleared.
        self.pending_clear: list[int] = []
        self.pending_at: float | None = None
        #: The card the player on turn is dwelling on (shown to everyone).
        self.pointer: int | None = None
        self._next_card = 0
        self._next_group = 0

    # ---- lobby settings ------------------------------------------------ #
    def set_settings(self, d: dict) -> None:
        """Host-only, lobby-only. Unknown keys and bad values are ignored."""
        with self.lock:
            if self.started:
                return
            s = self.settings
            if isinstance(d.get("deck"), str):
                s["deck"] = d["deck"][:40]
            if isinstance(d.get("levels"), list):
                lv = [int(x) for x in d["levels"]
                      if isinstance(x, (int, float)) and 1 <= int(x) <= 5]
                s["levels"] = sorted(set(lv)) or [5]
            if d.get("board_size") in (4, 6, 8, 12):
                s["board_size"] = int(d["board_size"])
            if d.get("cards") in (2, 3, 4):
                s["cards"] = int(d["cards"])
            if d.get("turns_each") in (5, 10, 15):
                s["turns_each"] = int(d["turns_each"])
            if d.get("writing") in _WRITING_VALUES:
                s["writing"] = str(d["writing"])
            if d.get("fonts") in _FONT_VALUES:
                s["fonts"] = str(d["fonts"])

    # ---- membership -------------------------------------------------- #
    def add_player(self, handler: "Handler", name: str) -> int:
        with self.lock:
            self.clients.append(handler)
            self.names.append(name)
            self.scores.append(0)
            self.combos.append(0)
            self.connected.append(True)
            self.turns_taken.append(0)
            return len(self.clients) - 1

    def drop_player(self, idx: int) -> None:
        """Take a player out of the game (quit, crash, or heartbeat timeout).

        Their unplayed turns leave with them - the game ends when everyone
        *still here* has had their turns, not when a fixed total is reached
        (which used to hand a departed player's turns to whoever remained).
        """
        with self.lock:
            if 0 <= idx < len(self.connected):
                self.connected[idx] = False
                self.clients[idx] = None
            if not self.started or self.finished:
                return
            if not any(self.connected):
                self.finished = True
                return
            if self._turns_left() <= 0:
                self.finished = True
            elif self.turn == idx:
                self._advance_turn(consume=False)

    def _turns_left(self) -> int:
        """Turns still owed to players who are still connected."""
        return sum(max(0, self.turns_each - self.turns_taken[i])
                   for i in range(len(self.connected)) if self.connected[i])

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

    def start(self, pool, faces=None, board_size=None,
              turns_each=None) -> None:
        """Begin a game from a freshly-sampled *pool* (the host builds it from
        the current lobby settings, so a settings change always takes effect)."""
        with self.lock:
            if self.started:
                return
            self.faces = ([f for f in (faces or []) if f]
                          or ["kanji", "reading", "meaning"])
            self.pool = list(pool or [])
            self.rng.shuffle(self.pool)
            self.board_size = max(2, min(12, int(
                board_size or self.settings["board_size"])))
            self.turns_each = max(1, min(50, int(
                turns_each or self.settings["turns_each"])))
            if len(self.pool) < 2:
                return                       # nothing to play with
            self.started = True
            self.paused = False
            self.pending_clear = []
            self.pending_at = None
            self.turns_taken = [0] * len(self.clients)
            self.turns_used = 0
            self.turn = next((i for i in range(len(self.connected))
                              if self.connected[i]), 0)
            self.turns_total = self.turns_each * sum(
                1 for c in self.connected if c)
            for _ in range(min(self.board_size, len(self.pool))):
                self._deal_group(self.pool.pop())
            self.rng.shuffle(self.board)

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            if self.started and not self.finished:
                self.paused = bool(paused)

    def back_to_lobby(self) -> None:
        """Abandon the current game and return everyone to the lobby, where
        the settings live. Players stay; scores start fresh next game."""
        with self.lock:
            self.started = False
            self.finished = False
            self.paused = False
            self.cards.clear()
            self.board.clear()
            self.selection.clear()
            self.current_group = None
            self.pending_clear = []
            self.pending_at = None
            self.pointer = None
            self.pool = []
            self.turn = 0
            self.turns_used = 0
            self.turns_total = 0
            self._next_card = 0
            self._next_group = 0
            for i in range(len(self.scores)):
                self.scores[i] = 0
                self.combos[i] = 0
                self.turns_taken[i] = 0

    def _advance_turn(self, consume: bool = True) -> None:
        if consume:
            self.turns_used += 1
            if 0 <= self.turn < len(self.turns_taken):
                self.turns_taken[self.turn] += 1
        self.selection.clear()
        self.current_group = None
        self.pointer = None      # never leave one player's pointer on another's turn
        for cid in self.cards:
            self.cards[cid]["selected"] = False
        if self._turns_left() <= 0 or not self.board:
            self.finished = True
            return
        # Next connected player who still has turns owed to them.
        n = len(self.clients)
        for step in range(1, n + 1):
            cand = (self.turn + step) % n
            if (self.connected[cand]
                    and self.turns_taken[cand] < self.turns_each):
                self.turn = cand
                return
        self.finished = True   # nobody left with turns

    def point_at(self, player: int, card_id: int | None) -> bool:
        """The current player has been hovering *card_id* - show everyone.

        Lets the player whose turn it is think out loud: the card they're
        dwelling on lights up on every screen, so the others can follow what
        they're considering instead of staring at a still board. Only the
        player on turn can point, and only at a card that's actually in play.
        Returns True when the pointer moved (so the caller broadcasts).
        """
        with self.lock:
            if (not self.started or self.finished or self.paused
                    or player != self.turn or self.pending_clear):
                return False
            if card_id is not None:
                card = self.cards.get(card_id)
                if card is None or card.get("matched"):
                    card_id = None
            if card_id == self.pointer:
                return False
            self.pointer = card_id
            return True

    def select(self, player: int, card_id: int) -> dict | None:
        """Apply one click. Returns the event dict (or None for a no-op)."""
        with self.lock:
            if (not self.started or self.finished or self.paused
                    or player != self.turn):
                return None
            if self.pending_clear:
                return None      # a group is being shown: nobody clicks through it
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
        """Score the group, then HOLD it on the board for everyone to see.

        The cards used to be removed and the turn advanced in this same call,
        so a completed group blinked out of existence before anyone but the
        clicker could read it. Now the group stays up, flagged ``matched``, and
        :meth:`tick` clears it after :data:`REVEAL_SECONDS` - which keeps the
        pause server-side, so every player's board holds and resumes together
        instead of each client guessing.
        """
        self.combos[player] += 1
        points = BASE_POINTS * self.combos[player]
        self.scores[player] += points
        texts = {self.cards[c]["face"]: self.cards[c]["text"]
                 for c in group_ids}
        for cid in group_ids:
            self.cards[cid]["matched"] = True
            self.cards[cid]["selected"] = True
        self.pending_clear = list(group_ids)
        self.pending_at = None          # deadline is set on the next tick
        return {"type": "complete", "player": player, "points": points,
                "combo": self.combos[player], "word": texts,
                "cards": list(group_ids)}

    def tick(self, now: float) -> bool:
        """Finish a revealed group once its 2 seconds are up.

        Returns True when the board changed (the caller then broadcasts). The
        host's client and the standalone server both drive this.
        """
        with self.lock:
            if not self.pending_clear or self.finished:
                return False
            if self.paused:
                self.pending_at = None      # don't burn the reveal while paused
                return False
            if self.pending_at is None:
                self.pending_at = now + REVEAL_SECONDS
                return False
            if now < self.pending_at:
                return False
            self._clear_revealed()
            return True

    def _clear_revealed(self) -> None:
        for cid in self.pending_clear:
            self.cards.pop(cid, None)
            if cid in self.board:
                self.board.remove(cid)
        self.pending_clear = []
        self.pending_at = None
        if self.pool:
            self._deal_group(self.pool.pop())
        self.rng.shuffle(self.board)
        self._advance_turn()

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
                "settings": dict(self.settings),
                "paused": self.paused,
                "started": self.started,
                "finished": self.finished,
                # The group currently held up for everyone to look at (empty
                # most of the time). Clients pulse these and block input.
                "revealing": list(self.pending_clear),
                # The card the player on turn is dwelling on, or None.
                "pointer": self.pointer,
                "turn": self.turn,
                "turns_used": self.turns_used,
                "turns_total": self.turns_total,
                # Authoritative "how many turns are actually left": a dropped
                # player's unplayed turns don't count, so total-minus-used lies.
                "turns_left": self._turns_left() if self.started else 0,
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

    def create(self, settings: dict | None = None) -> Room:
        with self.lock:
            self._reap()
            if len(self.rooms) >= _MAX_ROOMS:
                raise RuntimeError("server full")
            code = _room_code(self.rng)
            while code in self.rooms:
                code = _room_code(self.rng)
            room = Room(code, settings)
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
                if self.room is not None:
                    self.send({"t": "error", "msg": "bad create"})
                    continue
                try:
                    room = hub.create(msg.get("settings"))
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
            elif t == "config":
                if self.room is None or self.player != 0:
                    self.send({"t": "error", "msg": "not host"})
                    continue
                self.room.set_settings(msg.get("settings") or {})
                self.room.broadcast({"type": "config"})
            elif t == "start":
                if self.room is None or self.player != 0:
                    self.send({"t": "error", "msg": "not host"})
                    continue
                pool = msg.get("pool") or []
                if not (2 <= len(pool) <= 500):
                    self.send({"t": "error", "msg": "bad pool"})
                    continue
                self.room.start(pool, msg.get("faces"),
                                msg.get("board_size"), msg.get("turns_each"))
                self.room.broadcast({"type": "start"})
            elif t in ("pause", "resume"):
                if self.room is None or self.player != 0:
                    self.send({"t": "error", "msg": "not host"})
                    continue
                self.room.set_paused(t == "pause")
                self.room.broadcast({"type": t})
            elif t == "lobby":
                if self.room is None or self.player != 0:
                    self.send({"t": "error", "msg": "not host"})
                    continue
                self.room.back_to_lobby()
                self.room.broadcast({"type": "lobby"})
            elif t == "select":
                if self.room is None:
                    continue
                event = self.room.select(self.player,
                                         int(msg.get("card", -1)))
                if event is not None:
                    if self.room.finished:
                        event["finished"] = True
                    self.room.broadcast(event)
            elif t == "point":
                if self.room is None:
                    continue
                card = msg.get("card")
                if self.room.point_at(self.player,
                                      None if card is None else int(card)):
                    self.room.broadcast({"type": "point"})
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
        # Rooms are otherwise only ever driven by an incoming message, and the
        # end of a group reveal isn't one: without this the board would sit on
        # the completed group forever, waiting for a click it refuses to accept.
        self._ticking = True
        threading.Thread(target=self._tick_rooms, daemon=True,
                         name="kanjire-room-ticker").start()

    def _tick_rooms(self) -> None:
        while self._ticking:
            time.sleep(0.05)
            with self.hub.lock:
                rooms = list(self.hub.rooms.values())
            now = time.monotonic()
            for room in rooms:
                if room.tick(now):
                    room.broadcast({"type": "reveal_end"})

    def server_close(self) -> None:
        self._ticking = False
        super().server_close()


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
