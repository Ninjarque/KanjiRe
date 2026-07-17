"""Two devices pair and converge over the in-process loopback broker."""
from __future__ import annotations

import sqlite3

import pytest

from kanjire.data import db
from kanjire.data.syncmerge import digest, export_snapshot
from kanjire.net.syncsvc import SyncService
from kanjire.net.transport import LoopbackBroker, LoopbackTransport


class _State:
    def __init__(self):
        self.data = {"high_scores": {}, "settings": {}, "presets": []}
        self._settings = {}

    def setting(self, key, default=""):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value

    def save(self):
        pass


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.STATS_SCHEMA)
    return con


def _svc(broker):
    st = _State()
    svc = SyncService(st, _con(), transport=LoopbackTransport(broker))
    assert svc.connect() is None
    return svc


def _pump(*svcs, times=6):
    for _ in range(times):
        for s in svcs:
            s.tick()


def _seed(svc, expr, seen, matches):
    svc.con.execute(
        "INSERT INTO word_stats (expression, reading, seen, matches,"
        " last_seen_at) VALUES (?, 'よみ', ?, ?, '2026-07-01T00:00:00')",
        (expr, seen, matches))
    svc.con.commit()


def test_pair_and_converge():
    broker = LoopbackBroker()
    a, b = _svc(broker), _svc(broker)
    _seed(a, "時間", 10, 8)
    _seed(b, "学校", 5, 4)

    code = a.start_pairing()
    assert len(code) == 8
    assert b.join(code) is None
    _pump(a, b)
    assert b.linked and b.key == a.key
    # status may already have advanced from "linked!" to a merge result
    assert b.status

    _pump(a, b, times=8)
    da = digest(export_snapshot(a.con, a.state))
    db_ = digest(export_snapshot(b.con, b.state))
    assert da == db_, "devices did not converge"
    # both sides now hold both words
    for svc in (a, b):
        n = svc.con.execute("SELECT COUNT(*) c FROM word_stats").fetchone()["c"]
        assert n == 2


def test_wrong_code_fails_cleanly():
    broker = LoopbackBroker()
    a, b = _svc(broker), _svc(broker)
    a.start_pairing()
    assert b.join("WRONGCOD") is None
    _pump(a, b)
    assert not b.linked
    # host cancelling clears the retained offer
    a.cancel_pairing()
    assert not any("pairx" in t and v for t, v in broker.retained.items())


def test_third_device_joins_later_from_retained_snapshot():
    broker = LoopbackBroker()
    a, b = _svc(broker), _svc(broker)
    _seed(a, "時間", 10, 8)
    code = a.start_pairing()
    b.join(code)
    _pump(a, b, times=8)

    # a goes offline; a NEW device joins via b and still gets the data —
    # retained snapshots serve it even though a is gone.
    a.close()
    c = _svc(broker)
    code2 = b.start_pairing()
    assert c.join(code2) is None
    _pump(b, c, times=8)
    n = c.con.execute("SELECT COUNT(*) c FROM word_stats").fetchone()["c"]
    assert n == 1
    row = dict(c.con.execute("SELECT * FROM word_stats").fetchone())
    assert row["expression"] == "時間" and row["seen"] == 10


def test_forged_payloads_are_ignored():
    broker = LoopbackBroker()
    a, b = _svc(broker), _svc(broker)
    code = a.start_pairing()
    b.join(code)
    _pump(a, b)
    # An attacker who knows the topic but not the key publishes garbage.
    evil = LoopbackTransport(broker)
    evil.connect()
    acct = a.account_id()
    from kanjire.net import config
    evil.publish(f"{config.TOPIC_ROOT}/syncx/{acct}/dev/evil/snap/0",
                 b"not-encrypted-garbage", retain=True)
    before = digest(export_snapshot(b.con, b.state))
    _pump(a, b)
    assert digest(export_snapshot(b.con, b.state)) == before
