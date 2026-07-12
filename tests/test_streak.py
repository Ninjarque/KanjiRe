"""Tests for the daily streak with freeze mercy (UserState)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.userstate import UserState, STREAK_FREEZE_EVERY


def _state():
    tmp = Path(tempfile.mkdtemp()) / "state.json"
    return UserState(tmp)


D0 = date(2026, 3, 2)


def test_first_stamp_starts_streak():
    st = _state()
    assert st.streak_status(D0)["count"] == 0
    out = st.stamp_streak(D0)
    assert out["count"] == 1 and out["done_today"]
    assert st.streak_status(D0) == {"count": 1, "freezes": 0,
                                    "done_today": True}


def test_consecutive_days_grow_and_earn_freeze():
    st = _state()
    d = D0
    for i in range(STREAK_FREEZE_EVERY):
        out = st.stamp_streak(d)
        d += timedelta(days=1)
    assert out["count"] == STREAK_FREEZE_EVERY
    assert out["freezes"] == 1, "a freeze is earned at day 7"


def test_double_stamp_same_day_is_noop():
    st = _state()
    st.stamp_streak(D0)
    out = st.stamp_streak(D0)
    assert out["count"] == 1
    # and it can't farm freezes
    for i in range(1, STREAK_FREEZE_EVERY):
        out = st.stamp_streak(D0 + timedelta(days=i))
    assert out["freezes"] == 1
    out = st.stamp_streak(D0 + timedelta(days=STREAK_FREEZE_EVERY - 1))
    assert out["freezes"] == 1


def test_freeze_covers_missed_day():
    st = _state()
    d = D0
    for _ in range(STREAK_FREEZE_EVERY):   # earn 1 freeze
        st.stamp_streak(d)
        d += timedelta(days=1)
    # skip one day entirely, then come back
    d += timedelta(days=1)
    out = st.stamp_streak(d)
    assert out["count"] == STREAK_FREEZE_EVERY + 1, "freeze should bridge"
    assert out["freezes"] == 0


def test_streak_resets_without_freezes():
    st = _state()
    st.stamp_streak(D0)
    st.stamp_streak(D0 + timedelta(days=1))
    out = st.stamp_streak(D0 + timedelta(days=5))   # 3 missed, 0 freezes
    assert out["count"] == 1


def test_status_shows_zero_after_uncovered_gap_without_mutating():
    st = _state()
    st.stamp_streak(D0)
    st.stamp_streak(D0 + timedelta(days=1))
    status = st.streak_status(D0 + timedelta(days=6))
    assert status["count"] == 0
    # underlying data untouched until the next stamp
    assert st.data["settings"]["streak_count"] == 2


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
