"""One-command release: bump version → stamp CHANGELOG → build → publish.

This is the normal way to ship an update. During development, jot player-facing
bullets under the ``## [Unreleased]`` heading in ``CHANGELOG.md``; when the
change is fully validated, run::

    python scripts/release.py patch    # 0.1.0 -> 0.1.1  (bug fixes, polish)
    python scripts/release.py minor    # 0.1.x -> 0.2.0  (new feature / mode)
    python scripts/release.py major    # 0.x   -> 1.0.0  (deliberate milestone)

It bumps ``kanjire/__init__.py``'s ``__version__``, moves the Unreleased notes
into a dated section, then builds the EXE and publishes it to GitHub Releases
(signed) using those notes as the in-app banner text.

    --no-publish   build + bump locally but don't upload (no gh needed)
    --dry-run      show what would change; touch nothing
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from kanjire.paths import PACKAGE_DIR, PROJECT_ROOT

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

INIT_PATH = PACKAGE_DIR / "__init__.py"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"


def _read_version() -> tuple[int, int, int]:
    text = INIT_PATH.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text)
    if not m:
        raise SystemExit(f"ERROR: couldn't find __version__ in {INIT_PATH}")
    return int(m[1]), int(m[2]), int(m[3])


def _bump(ver: tuple[int, int, int], level: str) -> tuple[int, int, int]:
    major, minor, patch = ver
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def _write_version(new: str) -> None:
    text = INIT_PATH.read_text(encoding="utf-8")
    text = re.sub(r'(__version__\s*=\s*)"\d+\.\d+\.\d+"', rf'\1"{new}"', text, count=1)
    INIT_PATH.write_text(text, encoding="utf-8")


def _unreleased_body() -> str:
    """The bullet lines currently under ## [Unreleased] (for an empty-check)."""
    lines = CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()
    body, capturing = [], False
    for line in lines:
        if line.startswith("## "):
            if capturing:
                break
            capturing = line.strip() == "## [Unreleased]"
            continue
        if capturing:
            body.append(line)
    return "\n".join(body).strip()


def _stamp_changelog(new: str, today: str) -> None:
    """Insert a dated heading right after ## [Unreleased] so its current bullets
    become that version's notes and [Unreleased] is left empty for next time."""
    lines = CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()
    out, inserted = [], False
    for line in lines:
        out.append(line)
        if not inserted and line.strip() == "## [Unreleased]":
            out.append("")
            out.append(f"## {new} — {today}")
            inserted = True
    if not inserted:
        raise SystemExit("ERROR: no '## [Unreleased]' section in CHANGELOG.md")
    CHANGELOG_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("level", choices=("patch", "minor", "major"))
    p.add_argument("--no-publish", action="store_true",
                   help="build + bump but don't upload to GitHub")
    p.add_argument("--dry-run", action="store_true",
                   help="print the planned bump and notes, change nothing")
    args = p.parse_args(argv)

    cur = _read_version()
    new = _bump(cur, args.level)
    cur_s = ".".join(map(str, cur))
    new_s = ".".join(map(str, new))
    today = date.today().isoformat()

    body = _unreleased_body()
    print(f"Version: {cur_s} → {new_s}  ({args.level})")
    print(f"Date:    {today}")
    print("Notes (from [Unreleased]):")
    print("  " + (body.replace("\n", "\n  ") if body else "(empty!)"))
    if not body:
        print("WARNING: [Unreleased] is empty — players will see a generic note.")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    _write_version(new_s)
    _stamp_changelog(new_s, today)
    print(f"\n✓ Bumped {INIT_PATH.name} and stamped CHANGELOG.md.")

    # Import after the bump so build_release sees the new __version__.
    import importlib

    import kanjire
    importlib.reload(kanjire)
    import build_release
    importlib.reload(build_release)

    notes = build_release.notes_from_changelog(new_s)
    rc = build_release.build(force=True, publish=not args.no_publish, notes=notes)
    if rc == 0 and not args.no_publish:
        print(f"\n✓ Released v{new_s}. Friends get it on their next launch.")
    elif rc == 0:
        print(f"\n✓ Built v{new_s} locally (not published). "
              f"Run with publish when ready.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
