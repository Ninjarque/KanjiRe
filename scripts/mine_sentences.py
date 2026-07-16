"""Top up the example-sentence corpus with real, translated sentences for the
words that are short of examples.

The Tanaka corpus (see fetch_sentences.py) leaves ~30% of our kanji words with
fewer than three example sentences. Blindly adding a bigger corpus barely helps:
those words are short because they're *rare*, so even 90k more real sentences
only reach a few hundred of them, at a large bundle cost. This instead mines a
second real, CC-BY source (the ManyThings Tatoeba JA-EN pairs) and keeps ONLY
the sentences that raise an under-covered word toward three — a few hundred KB
that close the reachable part of the gap. The rest of the gap is genuinely rare
vocabulary that no real corpus covers; that's the data behind whether to ever
synthesise examples.

Merges into ``kanjire/data/sentences.db`` (idempotent - skips sentences already
present). Needs fugashi (MeCab + UniDic) to index the new sentences' words.

    python scripts/mine_sentences.py            # top up to 3 per word
    python scripts/mine_sentences.py --target 5 # be more generous
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import io
import sqlite3
import sys
import urllib.request
import zipfile

from kanjire.data import db
from kanjire.jputil import has_kanji
from kanjire.paths import DATA_DIR

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

OUT_PATH = DATA_DIR / "sentences.db"
SOURCE_URL = "https://www.manythings.org/anki/jpn-eng.zip"
SOURCE_CREDIT = "Tatoeba (via ManyThings jpn-eng, CC BY 2.0 FR)"
MIN_LEN, MAX_LEN = 8, 64


def _download_pairs() -> list[tuple[str, str]]:
    print(f"Downloading {SOURCE_URL} …")
    data = urllib.request.urlopen(
        urllib.request.Request(SOURCE_URL, headers={"User-Agent": "KanjiRe-setup"}),
        timeout=180).read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = next(n for n in zf.namelist() if n.endswith(".txt"))
    pairs = []
    for ln in zf.read(name).decode("utf-8").splitlines():
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        en, ja = parts[0].strip(), parts[1].strip()
        if MIN_LEN <= len(ja) <= MAX_LEN and en:
            pairs.append((ja, en))
    print(f"  {len(pairs)} usable pairs")
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=int, default=3,
                    help="aim for this many sentences per word (default 3)")
    args = ap.parse_args(argv)

    if not OUT_PATH.exists():
        print("sentences.db not found - run fetch_sentences.py first.")
        return 1

    con = sqlite3.connect(OUT_PATH)
    existing_ja = {r[0] for r in con.execute("SELECT ja FROM sentences")}
    counts: dict[str, int] = {
        hw: n for hw, n in con.execute(
            "SELECT headword, COUNT(DISTINCT sentence_id) FROM sentence_words "
            "GROUP BY headword")}
    next_id = (con.execute("SELECT COALESCE(MAX(id), -1) FROM sentences")
               .fetchone()[0] + 1)

    vcon = db.connect(read_only=True)
    vocab = {r[0] for r in vcon.execute(
        "SELECT expression FROM words WHERE has_kanji=1")}
    vcon.close()
    need = {w: args.target - counts.get(w, 0) for w in vocab
            if counts.get(w, 0) < args.target}
    print(f"vocab words short of {args.target}: {len(need)}")

    from kanjire.data.ingest import (CONTENT_POS, SKIP_POS2, _base_form,
                                     _reading_hint, get_tagger)
    tagger = get_tagger()
    pairs = _download_pairs()

    added_sent = 0
    added_word_rows: list[tuple[int, str, str | None, int]] = []
    sent_rows: list[tuple[int, str, str, int]] = []
    for ja, en in pairs:
        if ja in existing_ja:
            continue
        heads: dict[str, str | None] = {}
        for tok in tagger(ja):
            f = tok.feature
            if getattr(f, "pos1", None) not in CONTENT_POS:
                continue
            if getattr(f, "pos2", None) in SKIP_POS2:
                continue
            base = _base_form(tok)
            if base and has_kanji(base) and base not in heads:
                heads[base] = _reading_hint(tok)
        if not any(need.get(h, 0) > 0 for h in heads):
            continue
        sid = next_id
        next_id += 1
        existing_ja.add(ja)
        n_kanji = sum(1 for h in heads if has_kanji(h))
        sent_rows.append((sid, ja, en, n_kanji))
        for h, r in heads.items():
            added_word_rows.append((sid, h, r, 0))
            if need.get(h, 0) > 0:
                need[h] -= 1
        added_sent += 1

    if not added_sent:
        print("nothing to add - corpus already covers the reachable words.")
        con.close()
        return 0

    con.executemany(
        "INSERT INTO sentences (id, ja, en, n_kanji_words) VALUES (?,?,?,?)",
        sent_rows)
    con.executemany(
        "INSERT INTO sentence_words (sentence_id, headword, reading, good) "
        "VALUES (?,?,?,?)", added_word_rows)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('source_topup', ?)",
                (SOURCE_CREDIT,))
    con.commit()
    con.execute("VACUUM")
    con.close()

    reached = sum(1 for w, n in need.items() if n <= 0)
    print(f"✓ added {added_sent} sentences ({len(added_word_rows)} word rows)")
    print(f"  words brought to {args.target}+: {reached}")
    print(f"  sentences.db now {OUT_PATH.stat().st_size / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
