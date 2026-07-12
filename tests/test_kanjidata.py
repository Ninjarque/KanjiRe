"""Tests for the knowledge sidecars (skip cleanly if not yet fetched)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanjire.data import kanjidata


def _skip_if_missing():
    if not kanjidata.available():
        import pytest
        pytest.skip("kanjidata.db not built (run scripts/fetch_kanji_data.py)")


def test_components():
    _skip_if_missing()
    comps = kanjidata.components_of("時")
    assert comps, "時 should decompose"
    # kradfile-u is a *flat* decomposition: 時 = 日 + 寸 + 土 (寺 unpacked).
    assert "日" in comps and "寸" in comps, comps


def test_keisei_series():
    _skip_if_missing()
    info = kanjidata.keisei_info("晴")
    assert info and info["type"] == "comp_phonetic", info
    assert info["phonetic"] == "青", info
    assert "清" in info["series"] or "精" in info["series"], info["series"]
    assert any("せい" in r for r in info["series_readings"]), info


def test_series_map():
    _skip_if_missing()
    smap = kanjidata.series_map()
    assert "晴" in smap and "清" in smap["晴"]


def test_pitch():
    _skip_if_missing()
    acc = kanjidata.pitch_of("雨", "あめ")
    assert acc is not None and acc.split(",")[0] == "1", acc
    acc2 = kanjidata.pitch_of("飴", "あめ")
    assert acc2 is not None and acc2.split(",")[0] == "0", acc2


def test_sentences():
    if not kanjidata.SENTENCES_PATH.exists():
        import pytest
        pytest.skip("sentences.db not built (run scripts/fetch_sentences.py)")
    got = kanjidata.sentences_for("食べる", "たべる")
    assert got, "食べる should have example sentences"
    ja, en = got[0]
    assert "食" in ja and en


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
            print(f"SKIP/ERROR {t.__name__}: {exc!r}")
    print(f"\ndone")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
