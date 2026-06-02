"""Download a curated set of free Japanese fonts and place them in the repo.

Bundling fonts means familiarization mode has the same visual variety on every
machine, regardless of what the OS provides. All chosen fonts are licensed
under the SIL Open Font License (OFL) — the licence file is downloaded
alongside each font and kept in ``kanjire/fonts/``.

Curated picks (one per visual style):

* **DotGothic16**     – pixel / 8-bit blocky
* **Klee One**        – neat handwriting / schoolbook (kaisho)
* **Yuji Boku**       – bold brush calligraphy
* **Hachi Maru Pop**  – round pop handwritten
* **Reggae One**      – comic / manga heavy display
* **Zen Maru Gothic** – rounded modern gothic

Usage::

    python scripts/fetch_fonts.py
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import sys
import urllib.request
from pathlib import Path

from kanjire.paths import PACKAGE_DIR

FONTS_DIR = PACKAGE_DIR / "fonts"
RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/{slug}/{name}"

# (URL slug on google/fonts, the .ttf filename, the pyglet family name)
FONTS: list[tuple[str, str, str]] = [
    ("dotgothic16",    "DotGothic16-Regular.ttf",   "DotGothic16"),
    ("kleeone",        "KleeOne-Regular.ttf",       "Klee One"),
    ("yujiboku",       "YujiBoku-Regular.ttf",      "Yuji Boku"),
    ("hachimarupop",   "HachiMaruPop-Regular.ttf",  "Hachi Maru Pop"),
    ("reggaeone",      "ReggaeOne-Regular.ttf",     "Reggae One"),
    ("zenmarugothic",  "ZenMaruGothic-Regular.ttf", "Zen Maru Gothic"),
]


def _download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    req = urllib.request.Request(url, headers={"User-Agent": "kanjire-fonts/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {url}: {exc}")
        return False
    dest.write_bytes(data)
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true", help="re-download even if present")
    args = p.parse_args(argv)

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for slug, fname, family in FONTS:
        dest = FONTS_DIR / fname
        lic = FONTS_DIR / f"OFL-{slug}.txt"
        if args.force:
            for f in (dest, lic):
                if f.exists():
                    f.unlink()

        ttf_ok = _download(RAW.format(slug=slug, name=fname), dest)
        _download(RAW.format(slug=slug, name="OFL.txt"), lic)
        if ttf_ok and dest.stat().st_size > 1024:
            ok += 1
            print(f"  OK    {family:<18} {dest.relative_to(PACKAGE_DIR.parent)} "
                  f"({dest.stat().st_size/1024:.0f} KB)")

    if ok == 0:
        print("\nNo fonts downloaded. The game falls back to system fonts.")
        return 1
    print(f"\nDownloaded {ok}/{len(FONTS)} fonts to {FONTS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
