"""Tests for the IME-style romaji -> hiragana converter."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.kana import romaji_to_hira


CASES = [
    ("tabemasu", "たべます"),
    ("kyou", "きょう"),
    ("gakkou", "がっこう"),
    ("shinbun", "しんぶん"),        # n before consonant
    ("konnichiha", "こんいちは"),   # nn always makes ん (IME convention)
    ("konnnichiha", "こんにちは"),  # triple n = ん + に
    ("zenbu", "ぜんぶ"),
    ("hon", "ほん"),                # trailing n
    ("tenin", "てにん"),            # n before vowel binds forward (て+に+ん)
    ("ten'in", "てんいん"),         # apostrophe forces ん
    ("chotto", "ちょっと"),
    ("matcha", "まっちゃ"),         # tch -> っち
    ("jisho", "じしょ"),
    ("zyanru", "じゃんる"),         # kunrei alias
    ("tsuzuku", "つずく"),
    ("tsudzuku", "つづく"),
    ("ra-men", "らーめん"),          # long-vowel dash
    ("PA-THI", "ぱーてぃ"),          # case-insensitive + loan-word thi combo
]


def test_romaji_conversion_table():
    for src, want in CASES:
        got = romaji_to_hira(src)
        assert got == want, f"{src!r}: {got!r} != {want!r}"


def test_kana_passthrough():
    assert romaji_to_hira("たべる") == "たべる"
    assert romaji_to_hira("タベル") == "たべる"   # katakana folded
    assert romaji_to_hira("らーめん") == "らーめん"


def test_mixed_input():
    assert romaji_to_hira("たべmasu") == "たべます"


def test_unknown_chars_survive():
    # A wrong answer must stay wrong - no silent dropping.
    assert "q" in romaji_to_hira("qqq")


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
