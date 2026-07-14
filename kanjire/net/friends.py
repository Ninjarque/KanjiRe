"""Friends: who's online, invite them, ask to join them.

Rides the same public relay as the rooms, so it needs no account and no server
of ours. Two topics per player, keyed on their **friend code** (a stable id
minted once and kept in the save file - not the display name, which anyone can
change, and not the per-session network uid, which is random every launch):

``…/user/<CODE>/presence``
    A *retained* snapshot of what they're doing: ``{name, status, room}`` where
    status is ``online`` / ``lobby`` / ``playing``. Retained means a friend who
    opens the Friends tab sees you immediately instead of waiting for your next
    heartbeat. The connection's **will** is an empty retained message on this
    topic, so a killed app clears its own presence - otherwise you'd show up
    online forever to everyone who knows you.

``…/user/<CODE>/inbox``
    Invites ("come play, the room code is ABCDE"), requests to join, and the
    answers to friend requests.

``…/user/<CODE>/req/<SENDER>``
    A friend request, **retained** and one topic per sender, so it reaches
    someone who is offline right now - they see it next time they open the app.
    (An invite is worthless five minutes later, so those are *not* retained.)
    Answering it clears the retained message.

Friendship is mutual: you ask, they accept, and only then do you appear on each
other's lists. Being someone's friend is what lets them see your presence and
send you an invite - a stranger who somehow learns your code can do neither, so
knowing a code is never enough to reach you.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid

from kanjire.net import config
from kanjire.net.transport import PahoTransport

#: Presence values.
ONLINE, LOBBY, PLAYING, OFFLINE = "online", "lobby", "playing", "offline"

#: A friend we haven't heard from in this long is treated as gone. (Retained
#: presence survives a crash, so we never trust it forever.)
STALE_AFTER = 90.0

#: How often we re-publish our own presence, so friends can tell a live client
#: from a stale retained message.
REFRESH_SECONDS = 25.0


class FriendService:
    """Presence + invites. One per app; safe to never connect."""

    def __init__(self, state, transport=None) -> None:
        self.state = state
        self.transport = transport          # injected in tests
        self.uid = uuid.uuid4().hex[:12]
        self.lock = threading.RLock()
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.connected = False
        self.status = OFFLINE
        self.room = ""
        self._last_publish = 0.0
        self._now = 0.0
        #: friend code -> {"name", "status", "room", "seen"}
        self.presence: dict[str, dict] = {}
        #: Friend requests waiting on OUR answer: code -> their name.
        self.pending_in: dict[str, str] = {}

    # ---- identity ------------------------------------------------------ #
    @property
    def code(self) -> str:
        return self.state.friend_code

    @property
    def name(self) -> str:
        return self.state.setting("mp_name", "") or "player"

    def _t(self, code: str, leaf: str) -> str:
        return f"{config.TOPIC_ROOT}/user/{code}/{leaf}"

    # ---- lifecycle ------------------------------------------------------ #
    def connect(self) -> str | None:
        """Go online. Returns an error string, or None."""
        with self.lock:
            if self.connected:
                return None
            if self.transport is None:
                self.transport = PahoTransport(
                    config.BROKER_HOST, config.BROKER_PORT,
                    client_id=f"kanjire-f-{self.uid}",
                    keepalive=config.KEEPALIVE,
                )
            self.transport.on_message(self._on_message)
            self.transport.on_connect(self._on_connect)
            # If we die without saying goodbye, the broker wipes our presence.
            self.transport.set_will(self._t(self.code, "presence"), b"",
                                    retain=True)
            err = self.transport.connect()
            if err:
                return err
            self.connected = True
            self._subscribe_all()
            self.set_status(ONLINE)
            return None

    def _on_connect(self) -> None:
        with self.lock:
            if not self.connected:
                return
            self._subscribe_all()
            self._publish_presence()

    def _subscribe_all(self) -> None:
        self.transport.subscribe(self._t(self.code, "inbox"))
        # Friend requests: one retained topic per sender, so they queue up for
        # us even if we were offline when they were sent.
        self.transport.subscribe(self._t(self.code, "req/+"))
        for f in self.state.friends:
            self.transport.subscribe(self._t(f["code"], "presence"))

    def close(self) -> None:
        with self.lock:
            if self.transport is not None and self.connected:
                # Clear our retained presence: friends must not keep seeing us.
                self.transport.publish(self._t(self.code, "presence"), b"",
                                       retain=True)
                self.transport.close()
            self.connected = False
            self.status = OFFLINE

    # ---- our presence --------------------------------------------------- #
    def set_status(self, status: str, room: str = "") -> None:
        with self.lock:
            self.status = status
            self.room = room or ""
            if self.connected:
                self._publish_presence()

    def _publish_presence(self) -> None:
        if not self.connected or self.transport is None:
            return
        payload = json.dumps({
            "name": self.name, "status": self.status, "room": self.room,
        }).encode("utf-8")
        self.transport.publish(self._t(self.code, "presence"), payload,
                               retain=True)
        self._last_publish = self._now

    def tick(self, now: float | None = None) -> None:
        """Re-announce periodically; called from the app's frame loop."""
        if now is None:
            now = time.monotonic()
        with self.lock:
            first = self._now == 0.0
            self._now = now
            # Presence that arrived before the clock started (the retained
            # snapshots delivered the instant we subscribed) is stamped 0, which
            # would read as ancient the moment the clock jumps. Date it now.
            if first:
                for p in self.presence.values():
                    if not p.get("seen"):
                        p["seen"] = now
            if not self.connected:
                return
            if now - self._last_publish >= REFRESH_SECONDS:
                self._publish_presence()

    # ---- the friend list ------------------------------------------------ #
    def add_friend(self, code: str, name: str) -> bool:
        added = self.state.add_friend(code, name)
        with self.lock:
            if self.connected:
                self.transport.subscribe(self._t(code.strip().upper(),
                                                 "presence"))
        return added

    def remove_friend(self, code: str) -> bool:
        removed = self.state.remove_friend(code)
        with self.lock:
            self.presence.pop(code, None)
        return removed

    def friends(self) -> list[dict]:
        """The friend list, each with live presence merged in."""
        with self.lock:
            out = []
            for f in self.state.friends:
                p = self.presence.get(f["code"]) or {}
                fresh = (p.get("status") and p.get("status") != OFFLINE
                         and (self._now - p.get("seen", 0)) <= STALE_AFTER)
                out.append({
                    "code": f["code"],
                    "name": p.get("name") or f.get("name") or "?",
                    "status": p.get("status") if fresh else OFFLINE,
                    "room": p.get("room") or "" if fresh else "",
                })
            return out

    # ---- friend requests (mutual consent) -------------------------------- #
    def send_friend_request(self, code: str, name: str = "") -> bool:
        """Ask to be someone's friend. They have to say yes.

        Published **retained**, on a topic of its own per sender, so a request
        reaches someone who is offline right now: they'll get it the moment they
        next open the app. (An invite, by contrast, is worthless five minutes
        later - those are not retained.)
        """
        code = (code or "").strip().upper()
        if not self.state.add_request_out(code, name):
            return False
        with self.lock:
            if self.connected and self.transport is not None:
                self.transport.publish(
                    self._t(code, f"req/{self.code}"),
                    json.dumps({"from": self.code,
                                "name": self.name}).encode("utf-8"),
                    retain=True)
        return True

    def accept_request(self, code: str, name: str = "") -> None:
        """Say yes: now you're both friends, and they're told so."""
        code = (code or "").strip().upper()
        self.add_friend(code, name)
        self._clear_request(code)
        self._send(code, {"type": "friend_accept"})

    def decline_request(self, code: str) -> None:
        code = (code or "").strip().upper()
        self._clear_request(code)
        self._send(code, {"type": "friend_decline"})

    def _clear_request(self, code: str) -> None:
        """Wipe the retained request they left in our inbox - it's answered."""
        with self.lock:
            self.pending_in.pop(code, None)
            if self.connected and self.transport is not None:
                self.transport.publish(self._t(self.code, f"req/{code}"), b"",
                                       retain=True)

    def pending_requests(self) -> list[dict]:
        with self.lock:
            return [{"code": c, "name": n} for c, n in self.pending_in.items()]

    # ---- invites / join requests ----------------------------------------- #
    def invite(self, code: str, room: str) -> None:
        """"Come play with me" - carries the room code so they can just join."""
        self._send(code, {"type": "invite", "room": room})

    def ask_to_join(self, code: str) -> None:
        """"Can I join?" - the host answers with an invite (or ignores it)."""
        self._send(code, {"type": "join_request"})

    def _send(self, code: str, msg: dict) -> None:
        with self.lock:
            if not self.connected or self.transport is None:
                return
            msg = dict(msg, **{"from": self.code, "name": self.name})
            self.transport.publish(self._t(code, "inbox"),
                                   json.dumps(msg).encode("utf-8"))

    # ---- incoming -------------------------------------------------------- #
    def _on_message(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        leaf = parts[-1]
        code = parts[-2] if len(parts) >= 2 else ""
        with self.lock:
            # ".../user/<me>/req/<them>" - a friend request, retained by sender.
            if len(parts) >= 2 and parts[-2] == "req":
                sender = leaf
                if not payload:
                    self.pending_in.pop(sender, None)   # they withdrew it
                    return
                if sender == self.code or self.state.is_friend(sender):
                    return
                try:
                    d = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return
                name = str(d.get("name") or "?")[:18]
                self.pending_in[sender] = name
                self.inbox.put({"type": "friend_request", "from": sender,
                                "name": name})
                return
            if leaf == "presence":
                if not payload:
                    self.presence.pop(code, None)   # they cleared it: offline
                    return
                try:
                    d = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return
                self.presence[code] = {
                    "name": str(d.get("name") or "?")[:18],
                    "status": str(d.get("status") or ONLINE),
                    "room": str(d.get("room") or ""),
                    "seen": self._now,
                }
            elif leaf == "inbox":
                try:
                    d = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return
                sender = str(d.get("from") or "")
                kind = d.get("type")
                if kind in ("friend_accept", "friend_decline"):
                    # The answer to a request WE sent. They aren't a friend yet,
                    # so this has to bypass the friends-only gate below - but
                    # only if we actually asked them.
                    if not self.state.requested(sender):
                        return
                    self.state.drop_request_out(sender)
                    if kind == "friend_accept":
                        self.add_friend(sender, str(d.get("name") or "?"))
                    self.inbox.put(d)
                    return
                # Everything else (invites, join requests): only people you've
                # added can reach you. Without this, knowing a code would be
                # enough to spam anyone.
                if not self.state.is_friend(sender):
                    return
                self.inbox.put(d)

    def poll(self) -> list[dict]:
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out
