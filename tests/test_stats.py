"""Pure tests for the streak/recency-aware knowledge classification."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.data.stats import classify, knowledge_score


def test_unknown_when_never_seen():
    assert classify(None) == "unknown"
    assert classify({"seen": 0}) == "unknown"


def test_clean_first_match_is_known_not_struggling():
    # Regression: seen once, matched cleanly, no mistakes used to score 0.5 and
    # land in less_known. It should be "known" now.
    assert classify({"seen": 1, "matches": 1}) == "known"


def test_recent_clean_streak_promotes_despite_old_mistakes():
    row = {"seen": 8, "matches": 5, "mistakes_reading": 3, "current_streak": 2}
    assert classify(row) == "known"


def test_still_struggling_when_recent_and_low():
    row = {"seen": 4, "matches": 1, "mistakes_kanji": 2, "current_streak": 0}
    assert classify(row) == "less_known"


def test_streak_raises_score_monotonically():
    base = {"seen": 6, "matches": 3, "mistakes_meaning": 3}
    s0 = knowledge_score({**base, "current_streak": 0})
    s1 = knowledge_score({**base, "current_streak": 2})
    s2 = knowledge_score({**base, "current_streak": 4})
    assert s0 < s1 < s2


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
