"""The Reading Room trace log.

It exists to settle "the counter moved and I didn't press Next", so the
things that matter are: a counted read always says why, a sentence change
that is NOT a read is recorded as such, and none of it can ever raise.
"""
from __future__ import annotations

import pytest

from kanjire import readdiag


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(readdiag, "path", lambda: tmp_path / "reading.log")
    monkeypatch.delenv(readdiag._ENV_OFF, raising=False)


def test_a_note_records_event_fields_and_caller():
    readdiag.note("READ-COUNTED", id=7, reason="next-button")
    line = readdiag.read_tail()[-1]
    assert "READ-COUNTED" in line
    assert "id=7" in line and "reason=next-button" in line
    assert "test_readdiag.py:" in line, "the caller is what identifies the path"


def test_the_log_can_be_silenced(monkeypatch):
    monkeypatch.setenv(readdiag._ENV_OFF, "1")
    readdiag.note("READ-COUNTED", id=1)
    assert readdiag.read_tail() == []


def test_reading_it_before_anything_is_written_is_empty():
    assert readdiag.read_tail() == []


def test_a_broken_log_never_raises(monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr("builtins.open", boom)
    readdiag.note("READ-COUNTED", id=1)      # must not propagate


def test_it_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(readdiag, "path", lambda: tmp_path / "reading.log")
    for i in range(4000):
        readdiag.note("READ-COUNTED", id=i, reason="next-button")
    assert (tmp_path / "reading.log").stat().st_size < 200 * 1024


def test_counting_a_read_is_traced(tmp_path, monkeypatch):
    """The whole point: stats.log_read leaves a line naming its reason."""
    import sqlite3

    from kanjire.data import db
    from kanjire.data.stats import StatsRecorder

    con = db.connect(tmp_path / "stats.db")
    stats = StatsRecorder(con)
    stats.log_read(42, 15, source="tanaka", reason="next-button")
    line = readdiag.read_tail()[-1]
    assert "READ-COUNTED" in line and "id=42" in line
    assert "reason=next-button" in line
    con.close()
    assert isinstance(sqlite3.connect, object)


def test_a_dial_change_is_recorded_as_not_a_read(tmp_path, monkeypatch):
    from kanjire.data import db
    from kanjire.data.reading_session import ReadingSession
    from kanjire.data.stats import StatsRecorder
    from kanjire.userstate import UserState

    vocab = db.connect(read_only=True)
    stats = StatsRecorder(db.connect(tmp_path / "stats.db"))
    session = ReadingSession(vocab, stats, UserState(tmp_path / "state.json"))
    session.set_difficulty("easier")
    lines = readdiag.read_tail()
    assert any("requeue" in ln for ln in lines)
    assert not any("READ-COUNTED" in ln for ln in lines), \
        "changing a dial must never count a sentence as read"
