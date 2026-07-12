"""Code-only multiplayer: one client hosts, everyone relays through a broker.

The room code *is* the address. Both players connect outbound to a public
broker (no port forwarding, no IP sharing); the player who created the room
runs the authoritative :class:`~kanjire.net.server.Room` in-process and
publishes a **retained full-state snapshot** after every change, so:

* joiners get the current state the instant they subscribe (retained message),
* nobody can desync (state is never patched, only replaced),
* a reconnect re-reads the latest snapshot and carries on.

Presented through the same ``send`` / ``poll`` / ``close`` interface as the
direct-TCP :class:`~kanjire.net.client.NetClient`, so the UI is transport
agnostic.
"""
from __future__ import annotations

import json
import queue
import random
import string
import threading
import uuid

from kanjire.net import config
from kanjire.net.server import Room
from kanjire.net.transport import LoopbackTransport, PahoTransport

#: A joiner re-announces itself until the host acknowledges (in case the
#: join lands while the host is momentarily reconnecting).
_JOIN_RETRIES = 6


def _code(rng) -> str:
    return "".join(rng.choice(string.ascii_uppercase)
                   for _ in range(config.CODE_LEN))


class RoomClient:
    """Talks rooms over a pub/sub transport. ``send``-compatible with NetClient."""

    def __init__(self, transport=None, rng=None) -> None:
        self.rng = rng or random.Random()
        self.uid = uuid.uuid4().hex[:12]
        self.transport = transport      # injected in tests; built in connect()
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.lock = threading.RLock()

        self.name = "player"
        self.code = ""
        self.is_host = False
        self.room: Room | None = None      # host only
        self.uids: list[str] = []          # host only: player index -> uid
        self.me = -1
        self.connected = False
        self._join_attempts = 0
        self._last_state: dict | None = None

    # ---- topics ------------------------------------------------------- #
    def _t(self, leaf: str) -> str:
        return f"{config.TOPIC_ROOT}/{self.code}/{leaf}"

    # ---- lifecycle ---------------------------------------------------- #
    def connect(self, name: str) -> str | None:
        """Connect to the relay. Returns an error string, or None."""
        self.name = (name or "player")[:18]
        if self.transport is None:
            self.transport = PahoTransport(
                config.BROKER_HOST, config.BROKER_PORT,
                client_id=f"kanjire-{self.uid}", keepalive=config.KEEPALIVE,
            )
        self.transport.on_message(self._on_message)
        self.transport.on_connect(self._on_connect)
        err = self.transport.connect()
        if err:
            return err
        self.connected = True
        return None

    def _on_connect(self) -> None:
        # Re-announce after any (re)connect: the host re-publishes the room
        # state, a joiner re-announces itself.
        with self.lock:
            if self.is_host and self.room is not None:
                self._publish_state(None)
            elif self.code and not self.is_host:
                self._announce_join()

    def close(self) -> None:
        with self.lock:
            if self.transport is not None and self.code:
                if self.is_host:
                    # Clear the retained snapshot so the room doesn't linger
                    # on the broker after everyone has gone.
                    self.transport.publish(self._t("state"), b"", retain=True)
                else:
                    self.transport.publish(
                        self._t("bye"),
                        json.dumps({"uid": self.uid}).encode("utf-8"))
            if self.transport is not None:
                self.transport.close()
        self.connected = False

    # ---- outgoing (same message shapes as the TCP client) -------------- #
    def send(self, msg: dict) -> None:
        t = msg.get("t")
        with self.lock:
            if t == "create":
                self._create(msg)
            elif t == "join":
                self._join(msg)
            elif t == "start":
                if self.is_host and self.room is not None:
                    self.room.start()
                    self._publish_state({"type": "start"})
            elif t == "select":
                self._select(int(msg.get("card", -1)))

    def _create(self, msg: dict) -> None:
        self.code = _code(self.rng)
        self.is_host = True
        self.room = Room(self.code, msg.get("faces") or [],
                         msg.get("pool") or [],
                         msg.get("board_size") or 6,
                         msg.get("turns_each") or 10)
        self.me = self.room.add_player(None, self.name)
        self.uids = [self.uid]
        for leaf in ("join", "act", "bye"):
            self.transport.subscribe(self._t(leaf))
        # Clear any stale retained snapshot from a previous room that reused
        # this code, so a joiner can never see a ghost game.
        self.transport.publish(self._t("reject"), b"", retain=True)
        self.inbox.put({"t": "welcome", "player": self.me})
        self._publish_state({"type": "join", "player": self.me})

    def _join(self, msg: dict) -> None:
        self.code = str(msg.get("room") or "").strip().upper()
        self.is_host = False
        self.transport.subscribe(self._t("state"))
        self.transport.subscribe(self._t("reject"))
        # The broker replays the retained state immediately on subscribe, so
        # we learn the room's shape without asking anyone.
        self.transport.set_will(
            self._t("bye"), json.dumps({"uid": self.uid}).encode("utf-8"))
        self._announce_join()

    def _announce_join(self) -> None:
        self._join_attempts += 1
        self.transport.publish(
            self._t("join"),
            json.dumps({"uid": self.uid, "name": self.name}).encode("utf-8"))

    def _select(self, card_id: int) -> None:
        if self.is_host:
            if self.room is None:
                return
            event = self.room.select(self.me, card_id)
            if event is not None:
                if self.room.finished:
                    event["finished"] = True
                self._publish_state(event)
        else:
            self.transport.publish(
                self._t("act"),
                json.dumps({"uid": self.uid, "card": card_id}).encode("utf-8"))

    # ---- host: authoritative state ------------------------------------- #
    def _publish_state(self, event: dict | None) -> None:
        if self.room is None:
            return
        state = self.room.snapshot()
        state["client_ids"] = list(self.uids)
        payload = {"t": "state", "room": self.code, "state": state,
                   "event": event}
        # Retained: any (re)joiner instantly receives the live state.
        self.transport.publish(self._t("state"),
                               json.dumps(payload, ensure_ascii=False)
                               .encode("utf-8"),
                               retain=True)
        self.inbox.put(payload)      # the host renders from the same snapshot

    # ---- incoming ------------------------------------------------------ #
    def _on_message(self, topic: str, payload: bytes) -> None:
        if not payload:
            return
        try:
            msg = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        leaf = topic.rsplit("/", 1)[-1]
        with self.lock:
            if self.is_host:
                self._host_message(leaf, msg)
            elif leaf == "state":
                self._guest_state(msg)
            elif leaf == "reject" and msg.get("uid") == self.uid:
                self._join_attempts = _JOIN_RETRIES     # stop retrying
                reason = msg.get("reason")
                self.inbox.put({"t": "error", "msg": (
                    "game already started" if reason == "started"
                    else "room is full")})

    def _host_message(self, leaf: str, msg: dict) -> None:
        room = self.room
        if room is None:
            return
        uid = msg.get("uid")
        if leaf == "join":
            if not uid:
                return
            if uid in self.uids:
                self._publish_state(None)     # re-ack a retrying joiner
                return
            with room.lock:
                started, full = room.started, len(room.clients) >= 8
            if started or full:
                # Tell them why, instead of leaving them retrying blindly.
                self.transport.publish(
                    self._t("reject"),
                    json.dumps({"uid": uid,
                                "reason": "started" if started else "full"})
                    .encode("utf-8"))
                return
            idx = room.add_player(None, str(msg.get("name") or "?")[:18])
            self.uids.append(uid)
            self._publish_state({"type": "join", "player": idx})
        elif leaf == "act":
            if uid not in self.uids:
                return
            player = self.uids.index(uid)
            event = room.select(player, int(msg.get("card", -1)))
            if event is not None:
                if room.finished:
                    event["finished"] = True
                self._publish_state(event)
        elif leaf == "bye":
            if uid in self.uids:
                idx = self.uids.index(uid)
                room.drop_player(idx)
                self._publish_state({"type": "leave", "player": idx})

    def _guest_state(self, msg: dict) -> None:
        state = msg.get("state") or {}
        ids = state.get("client_ids") or []
        if self.me < 0:
            if self.uid in ids:
                self.me = ids.index(self.uid)
                self.inbox.put({"t": "welcome", "player": self.me})
            elif self._join_attempts < _JOIN_RETRIES:
                self._announce_join()      # host hasn't seen us yet - retry
                return
            else:
                self.inbox.put({"t": "error", "msg": "room not found"})
                return
        self.inbox.put(msg)

    # ---- polled by the UI each frame ----------------------------------- #
    def poll(self) -> list[dict]:
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out
