"""Build the example-sentence sidecar DB from the Tanaka Corpus.

Downloads the WWWJDIC ``examples.utf`` file (~150k Japanese-English pairs,
CC BY 2.0 FR, maintained inside the Tatoeba project) whose B-lines index
every dictionary word appearing in each sentence — exactly what we need to
show a word *in context* and, later, to serve readable sentences.

Compiled into ``kanjire/data/sentences.db``:

* ``sentences(id, ja, en)`` — kept sentences (length-filtered, deduped)
* ``sentence_words(sentence_id, headword, reading, good)`` — the index

To keep the bundle lean, a sentence is kept only if it's 8-64 Japanese
characters and needed by some word's example list (each headword keeps its
``~``-checked examples first, up to a cap).

Run once during setup::

    python scripts/fetch_sentences.py [--force]
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import gzip
import re
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from kanjire.paths import DATA_DIR

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

OUT_PATH = DATA_DIR / "sentences.db"
EXAMPLES_URL = "http://ftp.edrdg.org/pub/Nihongo/examples.utf.gz"

#: Keep at most this many example sentences per headword.
PER_WORD_CAP = 5
MIN_LEN, MAX_LEN = 8, 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS sentences (
    id INTEGER PRIMARY KEY,
    ja TEXT NOT NULL,
    en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sentence_words (
    sentence_id INTEGER NOT NULL,
    headword    TEXT NOT NULL,
    reading     TEXT,
    good        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sw_head ON sentence_words(headword);
CREATE INDEX IF NOT EXISTS idx_sw_sent ON sentence_words(sentence_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

#: B-line token: headword + optional (reading|#seq) + [sense] + {surface} + ~
_TOKEN = re.compile(
    r"^(?P<head>[^(\[{~]+)"
    r"(?:\((?P<paren>[^)]*)\))?"
    r"(?:\((?P<paren2>[^)]*)\))?"
    r"(?:\[(?P<sense>\d+)\])?"
    r"(?:\{(?P<surface>[^}]*)\})?"
    r"(?P<good>~)?$"
)


def _parse_examples(raw: str):
    """Yield (ja, en, [(headword, reading, good), ...]) per A/B pair."""
    ja = en = None
    for line in raw.splitlines():
        if line.startswith("A: "):
            body = line[3:]
            if "#ID=" in body:
                body = body.split("#ID=", 1)[0]
            if "\t" in body:
                ja, en = body.split("\t", 1)
            else:
                ja = en = None
        elif line.startswith("B: ") and ja and en:
            words = []
            for token in line[3:].split():
                m = _TOKEN.match(token)
                if not m:
                    continue
                head = m.group("head")
                reading = None
                for paren in (m.group("paren"), m.group("paren2")):
                    if paren and not paren.startswith("#"):
                        reading = paren
                words.append((head, reading, bool(m.group("good"))))
            yield ja.strip(), en.strip(), words
            ja = en = None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    if OUT_PATH.exists() and not args.force:
        print(f"{OUT_PATH.name} already exists — skipping (use --force).")
        return 0

    print(f"Downloading {EXAMPLES_URL} (~10 MB)…")
    req = urllib.request.Request(EXAMPLES_URL,
                                 headers={"User-Agent": "KanjiRe-setup"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = gzip.decompress(resp.read()).decode("utf-8", errors="replace")

    # Pass 1: collect candidate sentences + the per-word index.
    sentences: list[tuple[str, str]] = []
    seen_ja: dict[str, int] = {}
    by_word: dict[tuple[str, str | None], list[tuple[int, bool]]] = defaultdict(list)
    for ja, en, words in _parse_examples(raw):
        if not (MIN_LEN <= len(ja) <= MAX_LEN) or not en:
            continue
        if ja in seen_ja:
            sid = seen_ja[ja]
        else:
            sid = len(sentences)
            seen_ja[ja] = sid
            sentences.append((ja, en))
        for head, reading, good in words:
            by_word[(head, reading)].append((sid, good))

    # Pass 2: per headword keep checked examples first, then shortest.
    keep_rows: list[tuple[int, str, str | None, int]] = []
    needed: set[int] = set()
    for (head, reading), refs in by_word.items():
        refs.sort(key=lambda r: (not r[1], len(sentences[r[0]][0])))
        chosen: list[tuple[int, bool]] = []
        used = set()
        for sid, good in refs:
            if sid in used:
                continue
            used.add(sid)
            chosen.append((sid, good))
            if len(chosen) >= PER_WORD_CAP:
                break
        for sid, good in chosen:
            keep_rows.append((sid, head, reading, int(good)))
            needed.add(sid)

    print(f"  parsed {len(sentences)} candidate sentences; "
          f"keeping {len(needed)} for {len(by_word)} indexed words")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        remap: dict[int, int] = {}
        for new_id, sid in enumerate(sorted(needed)):
            remap[sid] = new_id
            ja, en = sentences[sid]
            con.execute("INSERT INTO sentences (id, ja, en) VALUES (?, ?, ?)",
                        (new_id, ja, en))
        con.executemany(
            "INSERT INTO sentence_words (sentence_id, headword, reading, good) "
            "VALUES (?, ?, ?, ?)",
            ((remap[sid], head, reading, good)
             for sid, head, reading, good in keep_rows),
        )
        con.execute("INSERT OR REPLACE INTO meta VALUES ('source', "
                    "'Tanaka Corpus via WWWJDIC examples.utf (CC BY 2.0 FR)')")
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    if OUT_PATH.exists():
        OUT_PATH.unlink()
    tmp.rename(OUT_PATH)
    print(f"✓ wrote {OUT_PATH}  ({OUT_PATH.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
