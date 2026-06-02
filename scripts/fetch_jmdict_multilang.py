"""Fetch French JMdict glosses from scriptin/jmdict-simplified.

Downloads ``jmdict-fre-X.Y.Z.json.tgz`` from the latest release on GitHub and
builds a sidecar SQLite at ``kanjire/data/glosses.db`` keyed by
``(expression, reading)`` → French gloss string. Two pieces of code consume
this sidecar:

* :mod:`scripts.build_jlpt_dataset` looks up a French gloss for every JLPT
  word and writes it into ``words.meaning_fr``.
* :func:`kanjire.data.ingest.analyze` does the same for words extracted from
  imported corpora.

About 15k entries are covered (core JMdict vocab); higher-level / specialist
words may not have a French gloss yet and gracefully fall back to English at
runtime.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import io
import json
import sqlite3
import sys
import tarfile
import urllib.request
from pathlib import Path

from kanjire.jputil import capitalize_first
from kanjire.paths import DATA_DIR

GLOSSES_DB = DATA_DIR / "glosses.db"
GH_LATEST = "https://api.github.com/repos/scriptin/jmdict-simplified/releases/latest"

SCHEMA = """
CREATE TABLE IF NOT EXISTS glosses (
    expression TEXT NOT NULL,
    reading    TEXT NOT NULL,
    fr         TEXT NOT NULL,
    PRIMARY KEY (expression, reading)
);
CREATE INDEX IF NOT EXISTS idx_glosses_expr ON glosses(expression);
"""


def _log(msg: str) -> None:
    print(msg, flush=True)


def _find_asset(release: dict, lang_tag: str) -> str:
    pattern = f"jmdict-{lang_tag}-"
    for a in release["assets"]:
        if a["name"].startswith(pattern) and a["name"].endswith(".json.tgz"):
            return a["browser_download_url"]
    raise RuntimeError(f"no asset for {lang_tag}")


def _download(url: str) -> bytes:
    _log(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "kanjire-build/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _open_json_in_tgz(blob: bytes) -> dict:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith(".json"))
        with tar.extractfile(member) as f:
            return json.load(f)


def _build_table(con: sqlite3.Connection, entries: list[dict], lang_tag: str) -> int:
    """Insert (expression, reading, gloss) rows.

    Every (kanji, kana) cross-product per entry is stored so look-ups by exact
    pair always hit when possible.
    """
    cur = con.cursor()
    cur.execute("DELETE FROM glosses")
    count = 0
    for entry in entries:
        glosses: list[str] = []
        for sense in entry.get("sense", []):
            for g in sense.get("gloss", []):
                if g.get("lang") == lang_tag and g.get("text"):
                    glosses.append(g["text"])
        if not glosses:
            continue
        gloss_text = "; ".join(glosses[:4])
        # Capitalise the first character so meanings read as proper sentences;
        # JMdict glosses come in lower-case by convention.
        gloss_text = capitalize_first(gloss_text) or gloss_text
        kanjis = [k["text"] for k in entry.get("kanji", []) if k.get("text")]
        kanas  = [k["text"] for k in entry.get("kana",  []) if k.get("text")]
        if not kanjis:
            # Kana-only word: index by kana as expression too.
            kanjis = kanas[:1] or [""]
        for kj in kanjis:
            for kn in kanas or [""]:
                cur.execute(
                    "INSERT OR IGNORE INTO glosses (expression, reading, fr) "
                    "VALUES (?, ?, ?)",
                    (kj, kn, gloss_text),
                )
                count += 1
    con.commit()
    return count


def build(force: bool = False) -> int:
    if GLOSSES_DB.exists() and not force:
        _log(f"glosses sidecar already present: {GLOSSES_DB}")
        _log("use --force to rebuild")
        return 0
    _log("Querying GitHub for latest jmdict-simplified release")
    req = urllib.request.Request(GH_LATEST, headers={"User-Agent": "kanjire-build/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.load(resp)
    url = _find_asset(release, "fre")
    blob = _download(url)
    _log(f"  downloaded {len(blob)//1024} KB")
    data = _open_json_in_tgz(blob)
    _log(f"  parsed {len(data['words'])} JMdict entries")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(GLOSSES_DB)
    try:
        con.executescript(SCHEMA)
        rows = _build_table(con, data["words"], "fre")
    finally:
        con.close()
    _log(f"  inserted {rows} (expression, reading, fr) rows into {GLOSSES_DB}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="rebuild even if glosses.db already exists")
    args = p.parse_args(argv)
    return build(force=args.force)


# --------------------------------------------------------------------------- #
# Helper used by the dataset/corpus builders to read a French gloss.
# --------------------------------------------------------------------------- #
def open_for_lookup() -> sqlite3.Connection | None:
    if not GLOSSES_DB.exists():
        return None
    return sqlite3.connect(f"file:{GLOSSES_DB}?mode=ro", uri=True)


def lookup_fr(con: sqlite3.Connection | None, expression: str, reading: str) -> str | None:
    if con is None:
        return None
    row = con.execute(
        "SELECT fr FROM glosses WHERE expression=? AND reading=?",
        (expression, reading),
    ).fetchone()
    if row:
        return row[0]
    # Fall back to expression-only match (any reading).
    row = con.execute(
        "SELECT fr FROM glosses WHERE expression=? LIMIT 1",
        (expression,),
    ).fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    sys.exit(main())
