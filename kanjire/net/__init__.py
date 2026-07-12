"""Multiplayer: a tiny room server + client for shared-board turn games.

Design (see docs/ARCHITECTURE.md):

* **Transport**: JSON objects, one per line, over plain TCP. Turn-based play
  with no latency requirements wants exactly what TCP already provides -
  ordered, reliable delivery - so there is no extra networking dependency.
* **Authority**: the server owns the room state (board, turns, scores) and
  broadcasts a **full state snapshot** after every action. Clients render
  whatever the last snapshot says; there is nothing to reconcile.
* **Data-free server**: the host's app samples the word pool and sends the
  card texts when creating a room, so the server itself needs no vocabulary
  database and can run anywhere Python runs
  (``python scripts/run_server.py``), or in-process when hosting from the
  app itself.
"""
