"""Pub/sub transports for code-only multiplayer.

Two implementations behind one tiny interface:

* :class:`PahoTransport` - a real MQTT connection to a public broker.
* :class:`LoopbackTransport` + :class:`LoopbackBroker` - an in-process
  broker used by the tests, so the whole multiplayer stack can be driven
  deterministically with no network at all.

Both remember their subscriptions and re-apply them on (re)connect, and
support *retained* messages (the broker replays the last retained message
on a topic to any new subscriber) - that's what lets a joiner receive the
room's current state the instant they subscribe.
"""
from __future__ import annotations

import threading


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT topic match supporting the '+' single-level wildcard."""
    if pattern == topic:
        return True
    p, t = pattern.split("/"), topic.split("/")
    if len(p) != len(t):
        return False
    return all(a == "+" or a == b for a, b in zip(p, t))


class _BaseTransport:
    def __init__(self) -> None:
        self._subs: list[str] = []
        self._on_message = None
        self._on_connect = None
        self._will: tuple[str, bytes, bool] | None = None
        self.connected = False

    def on_message(self, cb) -> None:
        self._on_message = cb

    def on_connect(self, cb) -> None:
        self._on_connect = cb

    def set_will(self, topic: str, payload: bytes, retain: bool = False) -> None:
        """Message the broker publishes if we vanish without saying goodbye.

        ``retain`` matters for presence: an *empty retained* will is what wipes
        a player's "online" flag when their app is killed. Without it their
        friends would see them online forever.
        """
        self._will = (topic, payload, retain)

    def _deliver(self, topic: str, payload: bytes) -> None:
        if self._on_message is not None:
            try:
                self._on_message(topic, payload)
            except Exception:      # a bad message must never kill the loop
                pass


# --------------------------------------------------------------------------- #
# Real MQTT
# --------------------------------------------------------------------------- #
class PahoTransport(_BaseTransport):
    def __init__(self, host: str, port: int, client_id: str,
                 keepalive: int = 30) -> None:
        super().__init__()
        self.host, self.port, self.keepalive = host, port, keepalive
        self.client_id = client_id
        self._client = None

    def connect(self) -> str | None:
        """Start the connection. Returns an error string, or None if the
        attempt started (success is reported via the on_connect callback)."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            return "paho-mqtt is not installed"
        try:
            try:      # paho 2.x requires an explicit callback API version
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                     client_id=self.client_id,
                                     clean_session=True)
                v2 = True
            except AttributeError:                       # paho 1.x
                client = mqtt.Client(client_id=self.client_id,
                                     clean_session=True)
                v2 = False

            if self._will is not None:
                client.will_set(self._will[0], self._will[1], qos=1,
                                retain=self._will[2])

            def _connected():
                self.connected = True
                for t in list(self._subs):
                    client.subscribe(t, qos=1)
                if self._on_connect is not None:
                    try:
                        self._on_connect()
                    except Exception:
                        pass

            if v2:
                client.on_connect = lambda c, u, f, rc, props=None: _connected()
                client.on_disconnect = \
                    lambda c, u, f, rc, props=None: setattr(self, "connected", False)
            else:
                client.on_connect = lambda c, u, f, rc: _connected()
                client.on_disconnect = \
                    lambda c, u, rc: setattr(self, "connected", False)
            client.on_message = lambda c, u, msg: self._deliver(msg.topic,
                                                                msg.payload)
            client.connect_async(self.host, self.port,
                                 keepalive=self.keepalive)
            client.loop_start()      # background network thread + reconnects
            self._client = client
            return None
        except Exception as exc:     # noqa: BLE001 - surfaced in the UI
            return str(exc)

    def subscribe(self, topic: str) -> None:
        if topic not in self._subs:
            self._subs.append(topic)
        if self._client is not None and self.connected:
            self._client.subscribe(topic, qos=1)

    def publish(self, topic: str, payload: bytes, retain: bool = False) -> None:
        if self._client is not None:
            self._client.publish(topic, payload, qos=1, retain=retain)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self.connected = False


# --------------------------------------------------------------------------- #
# In-process broker (tests)
# --------------------------------------------------------------------------- #
class LoopbackBroker:
    """A minimal in-memory MQTT-ish broker: topics, retained messages, wills."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.retained: dict[str, bytes] = {}
        self.transports: list["LoopbackTransport"] = []

    def register(self, tr: "LoopbackTransport") -> None:
        with self.lock:
            self.transports.append(tr)

    def publish(self, topic: str, payload: bytes, retain: bool) -> None:
        with self.lock:
            if retain:
                if payload:
                    self.retained[topic] = payload
                else:
                    self.retained.pop(topic, None)   # empty payload clears it
            targets = [t for t in self.transports
                       if t.connected and any(topic_matches(p, topic)
                                              for p in t._subs)]
        for t in targets:
            t._deliver(topic, payload)

    def deliver_retained(self, tr: "LoopbackTransport", pattern: str) -> None:
        with self.lock:
            hits = [(t, p) for t, p in self.retained.items()
                    if topic_matches(pattern, t)]
        for topic, payload in hits:
            tr._deliver(topic, payload)

    def disconnect(self, tr: "LoopbackTransport") -> None:
        """Ungraceful drop: fire the client's will, like a real broker."""
        with self.lock:
            if tr in self.transports:
                self.transports.remove(tr)
            will = tr._will
        tr.connected = False
        if will is not None:
            self.publish(will[0], will[1], will[2])


class LoopbackTransport(_BaseTransport):
    def __init__(self, broker: LoopbackBroker) -> None:
        super().__init__()
        self.broker = broker

    def connect(self) -> str | None:
        self.broker.register(self)
        self.connected = True
        for t in list(self._subs):
            self.broker.deliver_retained(self, t)
        if self._on_connect is not None:
            self._on_connect()
        return None

    def subscribe(self, topic: str) -> None:
        if topic not in self._subs:
            self._subs.append(topic)
        if self.connected:
            self.broker.deliver_retained(self, topic)

    def publish(self, topic: str, payload: bytes, retain: bool = False) -> None:
        self.broker.publish(topic, payload, retain)

    def close(self) -> None:
        with self.broker.lock:
            if self in self.broker.transports:
                self.broker.transports.remove(self)
        self.connected = False
