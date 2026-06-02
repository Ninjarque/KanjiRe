"""Ingest a Japanese text file into a playable vocabulary deck.

Example::

    python scripts/ingest_corpus.py mytext.txt --name "My Novel"

The deck is stored under the name ``corpus:<slug>`` and becomes selectable in
the game menu. Words are weighted by how often they appear in *your* text.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

from kanjire.data import db, ingest
from kanjire.paths import DB_PATH


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _slug(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip().lower()
    name = re.sub(r"[^\w぀-ヿ一-鿿]+", "-", name)
    return name.strip("-") or "corpus"


def _progress(done: int, total: int, word: str) -> None:
    pct = 100 * done / total if total else 100
    print(f"\r  resolving {done}/{total} ({pct:4.0f}%)  ", end="", flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("file", help="path to a UTF-8 / Shift-JIS Japanese text file")
    p.add_argument("--name", help="display name for the deck", default=None)
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return 1

    text = _read_text(path)
    display_name = args.name or path.stem
    deck = f"corpus:{_slug(display_name)}"

    print(f"Ingesting {path} as deck {deck!r} ...")
    result = ingest.analyze(text, progress=_progress)
    print()  # finish progress line
    print(" ", result.summary)

    if not result.words:
        print("ERROR: no usable vocabulary found in this text.")
        return 1

    con = db.connect(args.db)
    try:
        ingest.write_deck(
            con, deck, result,
            description=f"Vocabulary from {display_name}",
            source=str(path), created_at=date.today().isoformat(),
        )
    finally:
        con.close()

    print(f"Done. Deck {deck!r} now holds {len(result.words)} words.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
