"""Fetch and install the jamdict (JMdict + KanjiDic2) SQLite database.

The PyPI package ``jamdict-data`` ships the dictionary as an ``.xz`` archive that
is decompressed *during* the build.  On Windows that build frequently fails with
``WinError 32`` (the file is briefly locked by the OS/anti-virus while it is
written).  Rather than fight the broken build, this script downloads the source
distribution straight from PyPI, extracts the compressed database, and writes the
decompressed copy to the location jamdict looks for by default
(``~/.jamdict/data/jamdict.db``).

Run once before using the corpus-ingestion pipeline::

    python scripts/fetch_jamdict_data.py

It is idempotent: if the database already exists and looks valid it does nothing
unless ``--force`` is given.
"""
from __future__ import annotations

import argparse
import io
import json
import lzma
import sqlite3
import sys
import tarfile
import urllib.request
from pathlib import Path

PYPI_JSON = "https://pypi.org/pypi/jamdict-data/json"
DB_PATH = Path.home() / ".jamdict" / "data" / "jamdict.db"
MEMBER_SUFFIX = "jamdict.db.xz"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _find_sdist_url() -> str:
    _log(f"Querying PyPI metadata: {PYPI_JSON}")
    req = urllib.request.Request(PYPI_JSON, headers={"User-Agent": "kanjire-setup/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        meta = json.load(resp)
    for entry in meta["urls"]:
        if entry.get("packagetype") == "sdist":
            return entry["url"]
    # Fall back to scanning every release file.
    for files in meta.get("releases", {}).values():
        for entry in files:
            if entry["url"].endswith((".tar.gz", ".zip")):
                return entry["url"]
    raise RuntimeError("Could not find a jamdict-data source distribution on PyPI")


def _download(url: str) -> bytes:
    _log(f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "kanjire-setup/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _extract_xz_bytes(sdist: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(sdist), mode="r:*") as tar:
        member = next(
            (m for m in tar.getmembers() if m.name.endswith(MEMBER_SUFFIX)), None
        )
        if member is None:
            raise RuntimeError(f"{MEMBER_SUFFIX} not found inside the sdist")
        _log(f"Extracting {member.name} ({member.size/1_048_576:.1f} MB compressed)")
        fh = tar.extractfile(member)
        assert fh is not None
        return fh.read()


def _looks_valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1_000_000:
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            con.execute("SELECT 1 FROM Kanji LIMIT 1").fetchone()
            return True
        finally:
            con.close()
    except sqlite3.Error:
        return False


def install(force: bool = False) -> Path:
    if not force and _looks_valid(DB_PATH):
        _log(f"jamdict database already present and valid: {DB_PATH}")
        return DB_PATH

    sdist = _download(_find_sdist_url())
    compressed = _extract_xz_bytes(sdist)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _log("Decompressing database (this can take a moment)...")
    data = lzma.decompress(compressed)
    DB_PATH.write_bytes(data)
    _log(f"Wrote {DB_PATH} ({len(data)/1_048_576:.1f} MB)")

    if not _looks_valid(DB_PATH):
        raise RuntimeError("Decompressed database failed validation")
    _log("jamdict database installed successfully.")
    return DB_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="re-download even if a DB exists"
    )
    args = parser.parse_args(argv)
    try:
        install(force=args.force)
    except Exception as exc:  # noqa: BLE001 - top-level setup script
        _log(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
