"""Run the standalone KanjiRe multiplayer room server.

Usage::

    python scripts/run_server.py [port]      # default port 24857

The server is pure standard library, holds no vocabulary data (hosts send
their word pool when creating a room), and serves any number of rooms.
Point friends' apps at ``<this machine>:<port>``.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import sys

from kanjire.net.server import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
