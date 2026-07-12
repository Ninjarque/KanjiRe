"""Defaults for code-only multiplayer over a public MQTT broker.

Using a public broker as a pure relay means both players connect *outbound*
to it (which NAT/home routers allow with no configuration), so a shared room
code is all anyone needs - no IP addresses, no port forwarding, no server to
run. One player stays authoritative in-app; the broker only carries messages.
"""
from __future__ import annotations

import os

#: Public MQTT broker used as the rendezvous relay. HiveMQ's is open, needs
#: no account, and is widely used for exactly this. Overridable so a group
#: can point at their own broker (or test.mosquitto.org) if they prefer.
BROKER_HOST = os.environ.get("KANJIRE_BROKER_HOST", "broker.hivemq.com")
BROKER_PORT = int(os.environ.get("KANJIRE_BROKER_PORT", "1883"))

#: All topics live under here so a room code never collides with unrelated
#: traffic on the shared broker.
TOPIC_ROOT = "kanjire/mp/v1"

#: Room-code length. 5 uppercase letters = ~12M combinations, so two active
#: rooms colliding on the public broker is astronomically unlikely, while a
#: code is still easy to read aloud.
CODE_LEN = 5

#: MQTT keepalive (seconds). Turn-based play has no latency needs; this only
#: governs how quickly a dropped player is noticed via the broker's will.
KEEPALIVE = 30
