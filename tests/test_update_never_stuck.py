"""The update check must never leave the UI stuck on 'Checking…'.

Regression for the on-device bug: PyNaCl was missing from the APK, the
call-time import inside verify_manifest raised, the worker thread died
outside its try/except, and the phone showed a forever-spinning check.
"""
from __future__ import annotations

import time

from kanjire.update import checker
from kanjire.update.controller import CHECKING, ERROR, UP_TO_DATE, UpdateController


class _State:
    update_last_check = 0.0

    def set_update_last_check(self, ts):
        pass


def _run_to_completion(c: UpdateController) -> None:
    c._run()


def test_crashing_checker_ends_in_error_not_checking(monkeypatch):
    def explode(*a, **kw):
        raise ModuleNotFoundError("No module named 'nacl'")

    monkeypatch.setattr(checker, "check_for_update", explode)
    c = UpdateController(_State())
    _run_to_completion(c)
    assert c.status == ERROR
    assert c.status != CHECKING
    assert "nacl" in (c.error or "")


def test_verify_failure_is_benign_none(monkeypatch):
    from kanjire.update import verify

    monkeypatch.setattr(checker, "fetch_manifest",
                        lambda *a, **kw: {"version": "99.0.0",
                                          "signature": "xx"})
    monkeypatch.setattr(
        verify, "verify_manifest",
        lambda *a, **kw: (_ for _ in ()).throw(
            ModuleNotFoundError("No module named 'nacl'")))
    assert checker.check_for_update("0.1.0") is None


def test_healthy_path_reaches_up_to_date(monkeypatch):
    monkeypatch.setattr(checker, "check_for_update", lambda *a, **kw: None)
    c = UpdateController(_State())
    _run_to_completion(c)
    assert c.status == UP_TO_DATE


def test_wedged_network_hits_the_deadline(monkeypatch):
    # The on-device symptom: the checker blocks forever somewhere none of
    # the socket timeouts cover. The watchdog must conclude with ERROR and
    # leave the controller restartable (the zombie may linger; the UI must
    # not care).
    def wedge(*a, **kw):
        time.sleep(60)

    monkeypatch.setattr(checker, "check_for_update", wedge)
    c = UpdateController(_State())
    c.CHECK_DEADLINE = 0.5
    t0 = time.time()
    _run_to_completion(c)
    assert c.status == ERROR
    assert "timed out" in (c.error or "")
    assert time.time() - t0 < 5
    # A fresh manual check must be able to start (no live-thread block).
    assert c._thread is None or not c._thread.is_alive()
