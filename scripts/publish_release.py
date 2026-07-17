"""Publish already-built release artifacts to GitHub Releases.

The publish half of release.py, standalone — for when the artifacts were
built and signed locally (release.py --no-publish) and the upload happens
as its own deliberate step:

    python scripts/publish_release.py

Finds dist/KanjiRe-<version>-* for the CURRENT __version__ plus the signed
dist/latest.json, creates/updates the GitHub release via gh, then audits
the live update channel.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import importlib
import sys
from pathlib import Path

from kanjire import __version__
from kanjire.paths import PROJECT_ROOT

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    import build_release

    dist = PROJECT_ROOT / "dist"
    manifest = dist / "latest.json"
    if not manifest.exists():
        print("ERROR: dist/latest.json missing — run release.py first.")
        return 1

    artifacts: list[Path] = []
    for tag in ("windows", "linux", "android"):
        p = dist / build_release.artifact_name(tag)
        if p.exists():
            artifacts.append(p)
        else:
            print(f"note: no {tag} artifact ({p.name})")
    if not artifacts:
        print("ERROR: no artifacts for v" + __version__)
        return 1

    notes = build_release.notes_from_changelog(__version__)
    print(f"Publishing v{__version__}: "
          + ", ".join(p.name for p in artifacts))
    rc = build_release.publish_assets(artifacts, manifest, notes)
    if rc != 0:
        return rc

    print("\nAuditing the live update channel…")
    import audit_update
    importlib.reload(audit_update)
    return audit_update.main([])


if __name__ == "__main__":
    sys.exit(main())
