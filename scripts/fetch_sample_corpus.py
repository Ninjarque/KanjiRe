"""Download some real Japanese text and ingest it as a sample corpus deck.

Pulls the plain-text extracts of a handful of Japanese Wikipedia articles via
the MediaWiki API (modern, real-world prose, no Shift-JIS/ruby cleanup needed),
saves them under ``corpora/`` for reference, and builds the ``corpus:wikipedia``
deck so the corpus feature works out of the box.

Usage::

    python scripts/fetch_sample_corpus.py
    python scripts/fetch_sample_corpus.py --titles 日本 寿司 富士山
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import date

from kanjire.data import db, ingest
from kanjire.paths import CORPORA_DIR, DB_PATH

API = "https://ja.wikipedia.org/w/api.php"
DEFAULT_TITLES = ["日本", "東京", "寿司", "富士山", "漫画", "音楽", "鉄道", "祭り"]


def _fetch_extract(title: str) -> str:
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "format": "json",
        "titles": title,
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "kanjire-sample/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if "extract" in page:
            return page["extract"]
    return ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--titles", nargs="*", default=DEFAULT_TITLES)
    p.add_argument("--name", default="Wikipedia sample")
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args(argv)

    print("Downloading Japanese Wikipedia extracts ...")
    chunks = []
    for title in args.titles:
        try:
            text = _fetch_extract(title)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: {title}: {exc}")
            continue
        if text:
            print(f"  {title}: {len(text)} chars")
            chunks.append(text)

    if not chunks:
        print("ERROR: could not download any article text.")
        return 1

    corpus = "\n".join(chunks)
    CORPORA_DIR.mkdir(parents=True, exist_ok=True)
    saved = CORPORA_DIR / "wikipedia_sample.txt"
    saved.write_text(corpus, encoding="utf-8")
    print(f"Saved raw text to {saved} ({len(corpus)} chars)")

    def progress(done, total, word):
        print(f"\r  resolving {done}/{total}  ", end="", flush=True)

    print("Analysing ...")
    result = ingest.analyze(corpus, progress=progress)
    print("\n ", result.summary)

    con = db.connect(args.db)
    try:
        ingest.write_deck(
            con, "corpus:wikipedia", result,
            description="Vocabulary mined from Japanese Wikipedia articles",
            source="ja.wikipedia.org", created_at=date.today().isoformat(),
        )
    finally:
        con.close()

    print(f"Done. Deck 'corpus:wikipedia' holds {len(result.words)} words "
          f"and {len(result.kanji)} kanji.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
