"""Client side of the room protocol: a socket + reader thread.

The pyglet scene calls :meth:`NetClient.poll` each frame to drain incoming
messages; sends are small JSON lines written directly (thread-safe). All
errors funnel into a ``{"t": "error"}`` message so the UI has one place to
show "connection lost".
"""
from __future__ import annotations

import json
import queue
import socket
import threading

from kanjire.net.server import DEFAULT_PORT, PROTOCOL

CONNECT_TIMEOUT = 8.0


class NetClient:
    def __init__(self) -> None:
        self.sock: socket.socket | None = None
        self._file = None
        self._send_lock = threading.Lock()
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.connected = False

    # ------------------------------------------------------------------ #
    def connect(self, address: str, name: str) -> str | None:
        """Connect + say hello. Returns an error string or None on success."""
        host, _, port_s = address.partition(":")
        host = host.strip() or "127.0.0.1"
        try:
            port = int(port_s) if port_s.strip() else DEFAULT_PORT
        except ValueError:
            return "bad port"
        try:
            self.sock = socket.create_connection((host, port),
                                                 timeout=CONNECT_TIMEOUT)
            self.sock.settimeout(None)
            self._file = self.sock.makefile("rb")
        except OSError as exc:
            self.sock = None
            return str(exc)
        self.connected = True
        threading.Thread(target=self._reader, daemon=True,
                         name="kanjire-net-reader").start()
        self.send({"t": "hello", "name": name, "proto": PROTOCOL})
        return None

    def _reader(self) -> None:
        try:
            for raw in self._file:
                try:
                    self.inbox.put(json.loads(raw.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    continue
        except OSError:
            pass
        self.connected = False
        self.inbox.put({"t": "error", "msg": "connection lost"})

    # ------------------------------------------------------------------ #
    def send(self, obj: dict) -> None:
        if self.sock is None:
            return
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._send_lock:
                self.sock.sendall(data)
        except OSError:
            self.connected = False
            self.inbox.put({"t": "error", "msg": "connection lost"})

    def poll(self) -> list[dict]:
        """Drain everything received since the last call."""
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def tick(self, now: float | None = None) -> None:
        """No-op: TCP notices a dead peer itself (the socket closes), so only
        the relay transport needs heartbeats. Defined so the scene can call
        ``client.tick()`` without caring which transport it got."""

    def close(self) -> None:
        if self.sock is not None:
            self.send({"t": "bye"})
            # makefile() duplicates the socket handle - close both, or the
            # server never sees the connection end.
            for obj in (self._file, self.sock):
                try:
                    if obj is not None:
                        obj.close()
                except OSError:
                    pass
        self.sock = None
        self._file = None
        self.connected = False
