"""Multiplayer clustering: room settings, host pool, and old-client safety.

The whole point of resolving genres host-side is that the wire format never
changes, so these tests pin both halves: the room accepts and validates the
new settings, and what leaves the host is still an ordinary word list.
"""
from __future__ import annotations

import sqlite3

import pytest

from kanjire.game.mppool import affinity_from_settings, host_defaults
from kanjire.net.server import DEFAULT_SETTINGS, PROTOCOL, Room


def test_protocol_did_not_change():
    # Clustering is resolved before the pool ships, so guests need no new
    # protocol. If this ever has to change, old clients get kicked at hello
    # — make that a deliberate decision, not a side effect.
    assert PROTOCOL == 3


def test_defaults_are_inert():
    assert DEFAULT_SETTINGS["genres"] == []
    assert DEFAULT_SETTINGS["aff_meaning"] == 0
    assert DEFAULT_SETTINGS["aff_looks"] == 0
    assert DEFAULT_SETTINGS["aff_sound"] == 0


def test_room_accepts_and_validates_clustering_settings():
    room = Room("ABCDE")
    room.set_settings({"genres": ["food", "not_a_genre"], "aff_meaning": 3,
                       "aff_looks": 99, "aff_sound": 1})
    s = room.settings
    assert s["genres"] == ["food"]          # unknown genre dropped
    assert s["aff_meaning"] == 3
    assert s["aff_looks"] == 0              # out of range -> untouched
    assert s["aff_sound"] == 1


def test_settings_are_locked_once_started():
    room = Room("ABCDE")
    room.set_settings({"aff_looks": 3})
    room.started = True
    room.set_settings({"aff_looks": 0})
    assert room.settings["aff_looks"] == 3


def test_room_ignores_junk_clustering_values():
    room = Room("ABCDE")
    room.set_settings({"genres": "food", "aff_meaning": "lots"})
    assert room.settings["genres"] == []
    assert room.settings["aff_meaning"] == 0


def test_affinity_is_none_when_every_dial_is_off():
    assert affinity_from_settings(dict(DEFAULT_SETTINGS)) is None
    assert affinity_from_settings({}) is None


def test_affinity_survives_nonsense_dials():
    assert affinity_from_settings({"aff_meaning": None}) is None
    assert affinity_from_settings({"aff_meaning": "x"}) is None


class _FakeState:
    def __init__(self, settings):
        self.last_mode = "Time Attack"
        self._settings = settings

    def last_for_mode(self, mode):
        return self._settings


def test_host_defaults_carry_the_solo_clustering():
    out = host_defaults(_FakeState({"genres": ["food", "bogus"],
                                    "aff_looks": 2}))
    assert out["genres"] == ["food"]
    assert out["aff_looks"] == 2
    assert out["aff_meaning"] == 0


def test_host_defaults_never_raise_on_a_broken_state():
    class Broken:
        last_mode = "Time Attack"

        def last_for_mode(self, mode):
            raise RuntimeError("no state")

    assert host_defaults(Broken()) == {}


# --------------------------------------------------------------------------- #
needs_db = pytest.mark.skipif(
    not __import__("kanjire.paths", fromlist=["DATA_DIR"]).DATA_DIR
    .joinpath("kanjire.db").exists(),
    reason="vocabulary DB not built")


@needs_db
def test_host_pool_is_plain_cards_whatever_the_clustering():
    """A guest on an older build must see an ordinary word list."""
    from kanjire.game.mppool import sample_pool
    from kanjire.paths import DATA_DIR

    con = sqlite3.connect(DATA_DIR / "kanjire.db")
    con.row_factory = sqlite3.Row
    settings = {"deck": "jlpt", "levels": [5, 4, 3, 2, 1],
                "genres": ["food"], "aff_meaning": 3, "aff_sound": 2}
    pool = sample_pool(con, settings, size=20, locale="en")
    con.close()

    assert pool, "clustered room produced no words"
    for card in pool:
        assert set(card) == {"kanji", "reading", "romaji", "meaning"}
        assert all(isinstance(v, str) and v for v in card.values())
