"""Report how well the example-sentence corpus covers our vocabulary.

The reading curriculum wants ~3 sentences per word. This measures where we are:
how many vocab words have 0 / 1-2 / 3+ example sentences, weighted by how common
the word is (a missing common word matters far more than a missing rare one), and
lists the worst-covered *common* words as concrete targets.

    python scripts/sentence_coverage.py
    python scripts/sentence_coverage.py --worst 40   # list the top gaps
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import sqlite3
import sys

from kanjire.data import db
from kanjire.paths import DATA_DIR

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _counts_by_headword() -> dict[str, int]:
    """headword -> distinct sentence count, across every sentence store."""
    counts: dict[str, int] = {}
    sp = DATA_DIR / "sentences.db"
    if sp.exists():
        sc = sqlite3.connect(sp)
        for hw, n in sc.execute(
            "SELECT headword, COUNT(DISTINCT sentence_id) FROM sentence_words "
            "GROUP BY headword"):
            counts[hw] = counts.get(hw, 0) + n
        sc.close()
    # Imported corpora live in the vocab DB.
    con = db.connect(read_only=True)
    try:
        for hw, n in con.execute(
            "SELECT headword, COUNT(DISTINCT sentence_id) "
            "FROM corpus_sentence_words GROUP BY headword"):
            counts[hw] = counts.get(hw, 0) + n
    except Exception:  # noqa: BLE001
        pass
    finally:
        con.close()
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worst", type=int, default=25,
                    help="list this many worst-covered common words")
    args = ap.parse_args(argv)

    counts = _counts_by_headword()
    con = db.connect(read_only=True)
    words = con.execute(
        "SELECT expression, jlpt, freq FROM words WHERE has_kanji=1").fetchall()
    con.close()

    total = len(words)
    have0 = have12 = have3 = 0
    gaps: list[tuple[float, str, int, int | None]] = []
    for w in words:
        c = counts.get(w["expression"], 0)
        if c == 0:
            have0 += 1
        elif c < 3:
            have12 += 1
        else:
            have3 += 1
        if c < 3:
            gaps.append((w["freq"] or 0.0, w["expression"], c, w["jlpt"]))

    print(f"vocab words with kanji: {total}")
    print(f"  3+ sentences : {have3:5d}  ({have3/total:5.1%})")
    print(f"  1-2 sentences: {have12:5d}  ({have12/total:5.1%})")
    print(f"  0 sentences  : {have0:5d}  ({have0/total:5.1%})")
    need = have0 + have12
    print(f"\nwords still short of 3: {need}  "
          f"(need ~{have0*3 + sum(1 for f,e,c,j in gaps if c==1)*2 + 0} new "
          f"sentences, rough)")

    print(f"\nworst-covered COMMON words (by frequency), top {args.worst}:")
    gaps.sort(key=lambda g: -g[0])
    for freq, expr, c, jlpt in gaps[:args.worst]:
        lvl = f"N{jlpt}" if jlpt else "—"
        print(f"  {expr:<8} {lvl:<3} zipf={freq:4.1f}  has {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
