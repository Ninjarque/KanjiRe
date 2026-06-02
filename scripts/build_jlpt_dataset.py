"""Build the JLPT vocabulary deck into the game database.

Downloads the N5-N1 word lists from the open-source ``open-anki-jlpt-decks``
project (expression / reading / meaning / JLPT tags), cleans them, weights each
word by its real-world frequency (via :mod:`wordfreq`, no MeCab needed) and
writes everything into the ``jlpt`` deck of ``kanjire/data/kanjire.db``.

Usage::

    python scripts/build_jlpt_dataset.py            # all levels
    python scripts/build_jlpt_dataset.py --levels 5 4

Data source: https://github.com/jamsinclair/open-anki-jlpt-decks (MIT). The word
lists themselves derive from JMdict-based JLPT lists.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (sys.path side effect)

import argparse
import csv
import html
import io
import math
import re
import sys
import urllib.request
from datetime import date

from kanjire.data import db
from kanjire.jputil import capitalize_first, has_kanji
from kanjire.paths import DB_PATH

RAW_URL = (
    "https://raw.githubusercontent.com/jamsinclair/open-anki-jlpt-decks/"
    "{branch}/src/n{level}.csv"
)
LEVELS = (5, 4, 3, 2, 1)  # process easiest first so the easiest level "wins"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
MAX_MEANING = 120


def _log(msg: str) -> None:
    print(msg, flush=True)


def _download_csv(level: int, branch: str) -> str:
    url = RAW_URL.format(branch=branch, level=level)
    _log(f"  downloading N{level}: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "kanjire-build/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def _clean_meaning(raw: str) -> str:
    text = html.unescape(_TAG_RE.sub("", raw))
    text = _WS_RE.sub(" ", text).strip(" ;,")
    text = capitalize_first(text) or text
    if len(text) > MAX_MEANING:
        text = text[: MAX_MEANING - 1].rstrip() + "…"
    return text


def _zipf(freq: float) -> float:
    """Convert a wordfreq fraction to a zipf-like value (higher = commoner)."""
    return math.log10(freq) + 9.0 if freq > 0 else 0.0


def build(db_path=DB_PATH, levels=LEVELS, branch: str = "main") -> int:
    try:
        from wordfreq import get_frequency_dict
    except ImportError:
        _log("ERROR: wordfreq is required (pip install wordfreq).")
        return 1

    _log("Loading Japanese frequency table (wordfreq)...")
    freq_dict = get_frequency_dict("ja")

    # Optional French gloss sidecar from scripts/fetch_jmdict_multilang.py
    try:
        import fetch_jmdict_multilang as _fr
        fr_con = _fr.open_for_lookup()
        if fr_con is not None:
            _log("  found French gloss sidecar - JLPT words will get meaning_fr")
    except Exception:
        fr_con = None
        _fr = None

    # (expression, reading) -> record. Easiest level processed first wins.
    records: dict[tuple[str, str], dict] = {}
    for level in levels:
        try:
            data = _download_csv(level, branch)
        except Exception as exc:  # noqa: BLE001
            _log(f"  WARNING: could not fetch N{level}: {exc}")
            continue
        reader = csv.DictReader(io.StringIO(data))
        added = 0
        for row in reader:
            expr = (row.get("expression") or "").strip()
            reading = (row.get("reading") or "").strip()
            meaning = _clean_meaning(row.get("meaning") or "")
            if not expr or not reading or not meaning:
                continue
            key = (expr, reading)
            if key in records:
                continue  # keep the easier level already recorded
            freq = freq_dict.get(expr, 0.0)
            meaning_fr = _fr.lookup_fr(fr_con, expr, reading) if _fr else None
            meaning_fr = capitalize_first(meaning_fr)
            records[key] = {
                "expression": expr,
                "reading": reading,
                "meaning": meaning,
                "meaning_fr": meaning_fr,
                "jlpt": level,
                "freq": _zipf(freq),
            }
            added += 1
        _log(f"    N{level}: {added} new entries")

    if not records:
        _log("ERROR: no entries downloaded; aborting.")
        return 1

    _log(f"Writing {len(records)} words to {db_path} ...")
    con = db.connect(db_path)
    try:
        db.init_db(con)
        db.upsert_deck(
            con,
            "jlpt",
            "jlpt",
            description="JLPT N5-N1 vocabulary, frequency-weighted",
            source="open-anki-jlpt-decks",
            created_at=date.today().isoformat(),
        )
        with_kanji = 0
        for rec in records.values():
            db.upsert_word(con, deck="jlpt", **rec)
            if has_kanji(rec["expression"]):
                with_kanji += 1
        db.refresh_deck_counts(con)
        con.commit()
    finally:
        con.close()

    _log(f"Done. {len(records)} words ({with_kanji} contain kanji).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--levels", nargs="*", type=int, default=list(LEVELS))
    p.add_argument("--branch", default="main")
    args = p.parse_args(argv)
    # Keep the canonical easiest-first order regardless of CLI order.
    levels = [l for l in LEVELS if l in set(args.levels)]
    return build(db_path=args.db, levels=levels, branch=args.branch)


if __name__ == "__main__":
    sys.exit(main())
