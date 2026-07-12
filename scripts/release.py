"""One-command cross-platform release: bump → build Win + Linux → sign → publish.

Run from the Windows dev machine. During development, jot player-facing bullets
under ``## [Unreleased]`` in ``CHANGELOG.md``; when the change is fully
validated::

    python scripts/release.py patch    # 0.1.0 -> 0.1.1  (bug fixes, polish)
    python scripts/release.py minor    # 0.1.x -> 0.2.0  (new feature / mode)
    python scripts/release.py major    # 0.x   -> 1.0.0  (deliberate milestone)

It bumps ``__version__``, dates the CHANGELOG section, builds the **Windows**
bundle here and the **Linux** bundle inside WSL, assembles one Ed25519-signed
manifest covering both, and publishes everything to GitHub Releases.

    --no-publish   build + sign locally, don't upload (no gh needed)
    --skip-linux   Windows-only this time (e.g. WSL unavailable)
    --dry-run      show the planned bump + notes, change nothing
    --rebuild      re-release the CURRENT version (no bump/stamp) — use to
                   resume after a failed build/upload
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import re
import shutil
import subprocess
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
WSL_DISTRO = "Ubuntu-24.04"


# ---- version + changelog bumping -------------------------------------- #
def _read_version() -> tuple[int, int, int]:
    m = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"',
                  INIT_PATH.read_text(encoding="utf-8"))
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
    body, capturing = [], False
    for line in CHANGELOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if capturing:
                break
            capturing = line.strip() == "## [Unreleased]"
            continue
        if capturing:
            body.append(line)
    return "\n".join(body).strip()


def _stamp_changelog(new: str, today: str) -> None:
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


# ---- Linux build via WSL ---------------------------------------------- #
def _wsl_path(win_path: Path) -> str:
    """``M:\\Japanese\\KanjiRe`` → ``/mnt/m/Japanese/KanjiRe``."""
    p = Path(win_path)
    drive = p.drive.rstrip(":").lower()
    rest = p.as_posix()[len(p.drive):]
    return f"/mnt/{drive}{rest}"


def build_linux_via_wsl(linux_artifact_name: str) -> Path | None:
    """Build the Linux tar.gz inside WSL; return its Windows-side path."""
    if shutil.which("wsl") is None:
        print("ERROR: 'wsl' not found — can't build the Linux bundle.")
        return None
    # The build recipe lives in scripts/build_linux.sh (bootstraps pip --user,
    # installs deps, stages+bundles libGLU.so, builds the tar.gz). We invoke it
    # by WSL path; subprocess passes argv straight to wsl.exe (no shell mangling).
    repo = _wsl_path(PROJECT_ROOT)
    script = _wsl_path(PROJECT_ROOT / "scripts" / "build_linux.sh")
    print(f"Building Linux bundle in WSL ({WSL_DISTRO})… (first run installs deps)")
    rc = subprocess.run(["wsl", "-d", WSL_DISTRO, "--", "bash", script, repo]).returncode
    if rc != 0:
        print("WSL Linux build failed.")
        return None
    out = PROJECT_ROOT / "dist" / linux_artifact_name
    if not out.exists():
        print(f"ERROR: expected Linux artifact not found: {out}")
        return None
    print(f"✓ Linux artifact: {out.name}")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("level", choices=("patch", "minor", "major"), nargs="?")
    p.add_argument("--no-publish", action="store_true",
                   help="build + sign locally but don't upload")
    p.add_argument("--skip-linux", action="store_true",
                   help="build Windows only this release")
    p.add_argument("--dry-run", action="store_true",
                   help="print the planned bump + notes, change nothing")
    p.add_argument("--rebuild", action="store_true",
                   help="re-release the current version without bumping "
                        "(resume a failed release)")
    args = p.parse_args(argv)
    if not args.rebuild and args.level is None:
        p.error("level is required unless --rebuild is given")

    cur = _read_version()
    cur_s = ".".join(map(str, cur))
    today = date.today().isoformat()

    if args.rebuild:
        new_s = cur_s
        print(f"Version: {cur_s}  (rebuild — no bump)")
        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0
    else:
        new_s = ".".join(map(str, _bump(cur, args.level)))
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

    # Reload so build_release sees the new __version__.
    import importlib

    import kanjire
    importlib.reload(kanjire)
    import build_release
    importlib.reload(build_release)

    notes = build_release.notes_from_changelog(new_s)

    # 1) Windows bundle (native, clean build).
    win_art = build_release.build_artifact(force=True)
    if win_art is None:
        return 1
    artifacts = {"windows": win_art}

    # 2) Linux bundle (WSL). Must run AFTER the Windows zip exists (shared dist/).
    if not args.skip_linux:
        linux_name = build_release.artifact_name("linux")
        linux_art = build_linux_via_wsl(linux_name)
        if linux_art is None:
            print("Linux build failed — aborting so we don't ship a half release.")
            return 1
        artifacts["linux"] = linux_art

    # 3) One signed manifest covering every platform built.
    manifest = build_release.build_combined_manifest(artifacts, notes)
    mpath = build_release.sign_manifest_to_file(manifest)
    if mpath is None:
        return 1

    # 4) Publish (or stop after signing).
    if args.no_publish:
        print(f"\n✓ Built + signed v{new_s} ({', '.join(artifacts)}) — NOT published.")
        return 0
    rc = build_release.publish_assets(list(artifacts.values()), mpath, notes)
    if rc == 0:
        plats = ", ".join(artifacts)
        print(f"\n✓ Released v{new_s} for {plats}. Friends get it on next launch.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
