"""One-shot data setup: dictionary, fonts, French glosses, JLPT deck, corpus.

Runs the five preparation steps in order so a fresh checkout is ready to play::

    python scripts/setup_data.py

Steps: jamdict dictionary DB -> bundled fonts -> French gloss sidecar ->
JLPT deck (the only fatal step) -> Wikipedia sample corpus.
Pass ``--no-corpus`` to skip the (slower) Wikipedia sample ingestion.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-corpus", action="store_true",
                   help="skip downloading/ingesting the Wikipedia sample")
    args = p.parse_args(argv)

    import fetch_jamdict_data
    import fetch_jmdict_multilang
    import build_jlpt_dataset
    import fetch_fonts

    print("=" * 60)
    print("[1/7] Installing jamdict dictionary database")
    print("=" * 60)
    if fetch_jamdict_data.main([]) != 0:
        print("Dictionary install failed; corpus ingestion will not work.")

    print("\n" + "=" * 60)
    print("[2/7] Downloading bundled Japanese fonts (familiarization mode)")
    print("=" * 60)
    if fetch_fonts.main([]) != 0:
        print("Font download failed (non-fatal); the game falls back to system fonts.")

    print("\n" + "=" * 60)
    print("[3/7] Fetching French gloss sidecar (translations)")
    print("=" * 60)
    if fetch_jmdict_multilang.main([]) != 0:
        print("French gloss fetch failed (non-fatal); the game will only have English.")

    print("\n" + "=" * 60)
    print("[4/7] Building JLPT vocabulary deck (with French meanings)")
    print("=" * 60)
    if build_jlpt_dataset.main([]) != 0:
        print("JLPT build failed; aborting.")
        return 1

    print("\n" + "=" * 60)
    print("[5/7] Kanji knowledge sidecar (components / phonetic series / pitch)")
    print("=" * 60)
    import fetch_kanji_data
    if fetch_kanji_data.main([]) != 0:
        print("Kanji data fetch failed (non-fatal); anatomy panels stay empty.")

    print("\n" + "=" * 60)
    print("[6/7] Example sentences sidecar (Tanaka corpus)")
    print("=" * 60)
    import fetch_sentences
    if fetch_sentences.main([]) != 0:
        print("Sentence fetch failed (non-fatal); no example sentences.")

    if args.no_corpus:
        print("\n[7/7] Skipped sample corpus (--no-corpus).")
        return 0

    print("\n" + "=" * 60)
    print("[7/7] Fetching & ingesting Wikipedia sample corpus")
    print("=" * 60)
    import fetch_sample_corpus

    if fetch_sample_corpus.main([]) != 0:
        print("Sample corpus failed (non-fatal); you can still play the JLPT deck.")

    print("\nAll set! Launch the game with:  python main.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
