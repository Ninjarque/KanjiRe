"""crash.log must capture an unhandled traceback with context, and never raise."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_record_writes_a_traceback(tmp_path, monkeypatch):
    import kanjire.paths as paths
    from kanjire import crashlog

    monkeypatch.setattr(paths, "USER_DIR", tmp_path)

    try:
        raise ValueError("boom-in-a-click")
    except ValueError:
        crashlog.record(*sys.exc_info())

    log = tmp_path / "crash.log"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "boom-in-a-click" in text
    assert "ValueError" in text
    assert "KanjiRe" in text and "crash at" in text
    assert "platform:" in text


def test_record_appends(tmp_path, monkeypatch):
    import kanjire.paths as paths
    from kanjire import crashlog

    monkeypatch.setattr(paths, "USER_DIR", tmp_path)
    for msg in ("first-crash", "second-crash"):
        try:
            raise RuntimeError(msg)
        except RuntimeError:
            crashlog.record(*sys.exc_info())
    text = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "first-crash" in text and "second-crash" in text


def test_record_never_raises(monkeypatch):
    from kanjire import crashlog

    # Even if the path can't be resolved, recording must be silent.
    monkeypatch.setattr(crashlog, "crash_log_path",
                        lambda: (_ for _ in ()).throw(OSError("nope")))
    try:
        raise ValueError("x")
    except ValueError:
        crashlog.record(*sys.exc_info())   # must not raise


def test_install_ignores_keyboard_interrupt(monkeypatch, tmp_path):
    import kanjire.paths as paths
    from kanjire import crashlog

    monkeypatch.setattr(paths, "USER_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(crashlog, "record", lambda *a: calls.append(a))
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    crashlog.install()
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert calls == [], "Ctrl-C should not be logged as a crash"
    sys.excepthook(ValueError, ValueError("real"), None)
    assert len(calls) == 1, "a real crash should be logged"
