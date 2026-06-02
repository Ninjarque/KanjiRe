"""Bundle KanjiRe into a shareable Windows ``.zip`` with a working ``.exe``.

Produces a *play-only* distribution — the heavy NLP stack
(fugashi / jamdict / wordfreq) is deliberately left out because friends just
want to launch the game and play with the bundled JLPT + Wikipedia decks.
The "+ Import file…" / "+ Paste text…" buttons detect the missing stack at
runtime and hide themselves cleanly.

The output ends up at::

    dist/KanjiRe-windows.zip
    dist/KanjiRe/KanjiRe.exe   (the unpacked folder; one of these inside the zip)

Run::

    python scripts/build_release.py            # build
    python scripts/build_release.py --force    # blow away dist/ + build/ first
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from kanjire import __version__
from kanjire.paths import DATA_DIR, PACKAGE_DIR, PROJECT_ROOT
from kanjire.update import config as update_config
from kanjire.update import verify as update_verify

NAME = "KanjiRe"

#: Where gen_update_key.py stashes the private signing key (never in the repo).
PRIVATE_KEY_PATH = Path.home() / ".kanjire_keys" / "update_ed25519.hex"


# The Windows console is cp1252 by default, which can't encode the arrows /
# check-marks used in the progress logs (crashes the build at the zip step).
# Force UTF-8 on stdout/stderr so the script runs autonomously without needing
# PYTHONIOENCODING=utf-8 set in the environment.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def _check_prereqs() -> int:
    """Refuse to build if the prebuilt data files aren't there."""
    db = DATA_DIR / "kanjire.db"
    if not db.exists():
        _log(f"ERROR: {db} not found. Run scripts/setup_data.py first.")
        return 1
    fonts_dir = PACKAGE_DIR / "fonts"
    if not list(fonts_dir.glob("*.ttf")):
        _log(f"ERROR: no fonts in {fonts_dir}. Run scripts/fetch_fonts.py.")
        return 1
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _log("ERROR: PyInstaller not installed. Run:  pip install pyinstaller")
        return 1
    return 0


def _add_data_args() -> list[str]:
    """``--add-data SRC{sep}DEST`` flags for everything we need bundled."""
    sep = os.pathsep
    args: list[str] = []
    db = DATA_DIR / "kanjire.db"
    args += ["--add-data", f"{db}{sep}kanjire/data"]
    glosses = DATA_DIR / "glosses.db"
    if glosses.exists():
        args += ["--add-data", f"{glosses}{sep}kanjire/data"]
    fonts_dir = PACKAGE_DIR / "fonts"
    for f in sorted(fonts_dir.iterdir()):
        if f.is_file():
            args += ["--add-data", f"{f}{sep}kanjire/fonts"]
    return args


def _hidden_imports() -> list[str]:
    """Modules PyInstaller occasionally misses for pyglet/pyttsx3 on Windows."""
    return [
        "pyglet.media.codecs.wave_codec",
        "pyglet.media.codecs.wmf",
        "pyglet.media.drivers.directsound",
        "pyttsx3.drivers.sapi5",
        "comtypes.gen",
    ]


def _zip_release(folder: Path, out: Path) -> None:
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                fp = Path(root) / fn
                # Inside the zip, paths look like "KanjiRe/<path>".
                arc = Path(folder.name) / fp.relative_to(folder)
                zf.write(fp, arc)


def notes_from_changelog(version: str = __version__) -> str:
    """Extract the bullet list under ``## <version>`` from CHANGELOG.md.

    Returns "" if there's no CHANGELOG or no matching section, so callers can
    fall back to a generic note.
    """
    path = PROJECT_ROOT / "CHANGELOG.md"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("## "):
            if capturing:
                break  # reached the next version section
            # Match "## 0.2.0" or "## 0.2.0 — date".
            heading = line[3:].strip()
            capturing = heading == version or heading.startswith(version + " ")
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _release_tag() -> str:
    return f"v{__version__}"


def _zip_url(zip_name: str) -> str:
    return (
        f"https://github.com/{update_config.REPO_OWNER}/{update_config.REPO_NAME}"
        f"/releases/download/{_release_tag()}/{zip_name}"
    )


def _build_manifest(zip_path: Path, notes: str) -> dict:
    """Assemble the unsigned ``latest.json`` for this build."""
    return {
        "version": __version__,
        "url": _zip_url(zip_path.name),
        "sha256": update_verify.sha256_file(zip_path),
        "size": zip_path.stat().st_size,
        "notes": notes,
        "pubdate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _sign_and_write_manifest(zip_path: Path, notes: str) -> Path | None:
    """Build, sign, and write ``latest.json`` next to the zip. None on error."""
    if not update_config.PUBLIC_KEY_HEX.strip():
        _log("ERROR: no public key baked in. Run:  python scripts/gen_update_key.py")
        return None
    if not PRIVATE_KEY_PATH.exists():
        _log(f"ERROR: private signing key not found at {PRIVATE_KEY_PATH}.")
        _log("       Run:  python scripts/gen_update_key.py")
        return None
    private_hex = PRIVATE_KEY_PATH.read_text(encoding="ascii").strip()
    manifest = _build_manifest(zip_path, notes)
    signed = update_verify.sign_manifest(manifest, private_hex)
    # Sanity-check our own signature against the baked-in public key before we
    # ever upload it — a mismatch means the keypair is out of sync.
    if not update_verify.verify_manifest(signed, update_config.PUBLIC_KEY_HEX):
        _log("ERROR: signed manifest fails verification against the baked-in "
             "public key. Did you regenerate keys without rebuilding config.py?")
        return None
    out = zip_path.parent / update_config.MANIFEST_ASSET
    out.write_text(json.dumps(signed, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"  signed manifest → {out}")
    return out


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _publish(zip_path: Path, manifest_path: Path, notes: str) -> int:
    """Create (or update) the GitHub Release and upload the zip + manifest."""
    if not _gh_available():
        _log("ERROR: the GitHub CLI ('gh') is not on PATH. Install it with:")
        _log("       winget install GitHub.cli   (then: gh auth login)")
        return 1
    repo = f"{update_config.REPO_OWNER}/{update_config.REPO_NAME}"
    tag = _release_tag()
    assets = [str(zip_path), str(manifest_path)]

    # Does the release already exist? If so, re-upload assets with --clobber;
    # otherwise create it fresh.
    exists = subprocess.run(
        ["gh", "release", "view", tag, "-R", repo],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0

    if exists:
        _log(f"Release {tag} exists — uploading assets (--clobber)…")
        cmd = ["gh", "release", "upload", tag, *assets, "--clobber", "-R", repo]
    else:
        _log(f"Creating release {tag}…")
        cmd = ["gh", "release", "create", tag, *assets,
               "-R", repo, "--title", f"{NAME} {__version__}",
               "--notes", notes or f"{NAME} {__version__}"]
    _log("  " + " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        _log("gh failed — check 'gh auth status' and that the repo exists.")
        return rc
    _log("")
    _log(f"✓ Published {tag} to https://github.com/{repo}/releases/latest")
    _log("  Friends' apps will pick it up on their next launch.")
    return 0


def build(force: bool = False, publish: bool = False, notes: str = "") -> int:
    rc = _check_prereqs()
    if rc:
        return rc

    dist = PROJECT_ROOT / "dist"
    build_dir = PROJECT_ROOT / "build"
    if force:
        for d in (dist, build_dir):
            if d.exists():
                _log(f"  removing {d}")
                shutil.rmtree(d, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "main.py",
        "--name", NAME,
        "--noconfirm",
        "--onedir",
        "--windowed",          # no console window
        "--clean",
        *_add_data_args(),
    ]
    for mod in _hidden_imports():
        cmd += ["--hidden-import", mod]
    # PyNaCl ships a compiled libsodium extension + cffi backend used by the
    # updater's signature check; --collect-all grabs the binary reliably.
    cmd += ["--collect-all", "nacl", "--hidden-import", "_cffi_backend"]

    _log("Running PyInstaller...")
    _log("  " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        _log("PyInstaller failed.")
        return result.returncode

    src = dist / NAME
    if not src.exists():
        _log(f"ERROR: expected output folder {src} not found.")
        return 1

    out_zip = dist / f"{NAME}-{__version__}-windows.zip"
    _log(f"Zipping {src.name}/ → {out_zip.name}")
    _zip_release(src, out_zip)

    size_mb = out_zip.stat().st_size / 1_048_576
    _log("")
    _log(f"✓ Release built: {out_zip}")
    _log(f"  Size: {size_mb:.1f} MB")
    _log("")
    _log("Share with friends:")
    _log(f"  1. Send them  {out_zip.name}")
    _log(f"  2. They unzip and double-click  KanjiRe/{NAME}.exe")
    _log("")
    _log("Notes:")
    _log("  - Japanese TTS (Haruka) needs the Windows Japanese language pack;")
    _log("    without it the game still works, speech just no-ops.")
    _log("  - Stats / settings save to  %APPDATA%\\KanjiRe\\")

    if publish:
        _log("")
        _log("Publishing to GitHub Releases…")
        manifest_path = _sign_and_write_manifest(out_zip, notes)
        if manifest_path is None:
            return 1
        return _publish(out_zip, manifest_path, notes)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="wipe dist/ and build/ before building")
    p.add_argument("--publish", action="store_true",
                   help="sign latest.json and upload it + the zip as a GitHub Release")
    p.add_argument("--notes", default="",
                   help="release notes shown to players in the update banner")
    p.add_argument("--notes-from-changelog", action="store_true",
                   help=f"use the CHANGELOG.md section for the current version "
                        f"({__version__}) as the release notes")
    args = p.parse_args(argv)
    notes = args.notes
    if not notes and args.notes_from_changelog:
        notes = notes_from_changelog()
    return build(force=args.force, publish=args.publish, notes=notes)


if __name__ == "__main__":
    sys.exit(main())
