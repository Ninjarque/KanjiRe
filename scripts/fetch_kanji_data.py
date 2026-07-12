"""Build the kanji-knowledge sidecar DB: components, phonetic series, pitch.

Downloads three open datasets and compiles them into
``kanjire/data/kanjidata.db`` (bundled read-only, like ``glosses.db``):

* **kradfile-u** (EDRDG / KanjiCafe, CC BY-SA) — visual component
  decomposition for ~13k kanji ("試 = 言 工 弋").
* **Keisei phonetic-compound data** (mwil/wanikani-userscripts, GPL-3.0) —
  which kanji are semantic+phonetic compounds, their phonetic component,
  and each component's full series ("青 → 晴 清 精 請 情, all せい").
* **Kanjium accents** (mifunetoshiro/kanjium, CC BY-SA 4.0) — pitch-accent
  downstep numbers per expression+reading.

Run once during setup::

    python scripts/fetch_kanji_data.py [--force]
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import gzip
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

from kanjire.paths import DATA_DIR

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

OUT_PATH = DATA_DIR / "kanjidata.db"

KRADFILE_URL = ("https://raw.githubusercontent.com/jmettraux/kensaku/"
                "master/data/kradfile-u")
KRADFILE_FALLBACK = "http://ftp.edrdg.org/pub/Nihongo/kradfile-u.gz"
KEISEI_KANJI_URL = ("https://raw.githubusercontent.com/mwil/wanikani-userscripts/"
                    "master/wanikani-phonetic-compounds/db/kanji.json")
KEISEI_PHONETIC_URL = ("https://raw.githubusercontent.com/mwil/wanikani-userscripts/"
                       "master/wanikani-phonetic-compounds/db/phonetic.json")
KANJIUM_URL = ("https://raw.githubusercontent.com/mifunetoshiro/kanjium/"
               "master/data/source_files/raw/accents.txt")

SCHEMA = """
CREATE TABLE IF NOT EXISTS components (
    kanji      TEXT PRIMARY KEY,
    components TEXT NOT NULL          -- space-separated visual elements
);
CREATE TABLE IF NOT EXISTS keisei_kanji (
    kanji    TEXT PRIMARY KEY,
    type     TEXT NOT NULL,           -- comp_phonetic | hieroglyph | ...
    semantic TEXT,
    phonetic TEXT,
    readings TEXT                      -- comma-separated on'yomi
);
CREATE TABLE IF NOT EXISTS keisei_series (
    phonetic      TEXT PRIMARY KEY,
    readings      TEXT,               -- comma-separated
    compounds     TEXT,               -- kanji sharing the sound, space-sep
    non_compounds TEXT
);
CREATE TABLE IF NOT EXISTS pitch (
    expression TEXT NOT NULL,
    reading    TEXT NOT NULL,
    accent     TEXT NOT NULL,         -- downstep number(s), comma-separated
    PRIMARY KEY (expression, reading)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _fetch(url: str, timeout: int = 60) -> bytes:
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "KanjiRe-setup"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if url.endswith(".gz"):
        data = gzip.decompress(data)
    return data


def _load_kradfile(con: sqlite3.Connection) -> int:
    try:
        raw = _fetch(KRADFILE_URL)
    except Exception as exc:  # noqa: BLE001
        print(f"  mirror failed ({exc}); trying EDRDG…")
        raw = _fetch(KRADFILE_FALLBACK)
    n = 0
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or " : " not in line:
            continue
        kanji, _, comps = line.partition(" : ")
        kanji = kanji.strip()
        parts = comps.split()
        if kanji and parts:
            con.execute(
                "INSERT OR REPLACE INTO components (kanji, components) VALUES (?, ?)",
                (kanji, " ".join(parts)),
            )
            n += 1
    return n


def _load_keisei(con: sqlite3.Connection) -> tuple[int, int]:
    kanji = json.loads(_fetch(KEISEI_KANJI_URL).decode("utf-8"))
    phonetic = json.loads(_fetch(KEISEI_PHONETIC_URL).decode("utf-8"))
    nk = 0
    for ch, info in kanji.items():
        con.execute(
            "INSERT OR REPLACE INTO keisei_kanji "
            "(kanji, type, semantic, phonetic, readings) VALUES (?, ?, ?, ?, ?)",
            (ch, info.get("type") or "unknown", info.get("semantic"),
             info.get("phonetic"),
             ",".join(info.get("readings") or [])),
        )
        nk += 1
    np_ = 0
    for ch, info in phonetic.items():
        con.execute(
            "INSERT OR REPLACE INTO keisei_series "
            "(phonetic, readings, compounds, non_compounds) VALUES (?, ?, ?, ?)",
            (ch, ",".join(info.get("readings") or []),
             " ".join(info.get("compounds") or []),
             " ".join(info.get("non_compounds") or [])),
        )
        np_ += 1
    return nk, np_


def _load_pitch(con: sqlite3.Connection) -> int:
    raw = _fetch(KANJIUM_URL).decode("utf-8", errors="replace")
    n = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        expr, reading, accent = (p.strip() for p in parts)
        if not expr or not accent:
            continue
        con.execute(
            "INSERT OR REPLACE INTO pitch (expression, reading, accent) "
            "VALUES (?, ?, ?)",
            (expr, reading or expr, accent),
        )
        n += 1
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="rebuild even if kanjidata.db already exists")
    args = p.parse_args(argv)

    if OUT_PATH.exists() and not args.force:
        print(f"{OUT_PATH.name} already exists — skipping (use --force to rebuild).")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        print("[1/3] Component decomposition (kradfile-u)")
        nc = _load_kradfile(con)
        print(f"  {nc} kanji decomposed")
        print("[2/3] Phonetic series (keisei)")
        nk, np_ = _load_keisei(con)
        print(f"  {nk} kanji analysed, {np_} phonetic series")
        print("[3/3] Pitch accents (kanjium)")
        na = _load_pitch(con)
        print(f"  {na} accent entries")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('sources', ?)",
                    ("kradfile-u (EDRDG/KanjiCafe, CC BY-SA); "
                     "keisei db (mwil/wanikani-userscripts, GPL-3.0); "
                     "kanjium accents (CC BY-SA 4.0)",))
        con.commit()
    finally:
        con.close()
    if OUT_PATH.exists():
        OUT_PATH.unlink()
    tmp.rename(OUT_PATH)
    size = OUT_PATH.stat().st_size / 1_048_576
    print(f"✓ wrote {OUT_PATH}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
