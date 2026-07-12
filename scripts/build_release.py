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
import tarfile
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
    for sidecar in ("glosses.db", "kanjidata.db", "sentences.db"):
        path = DATA_DIR / sidecar
        if path.exists():
            args += ["--add-data", f"{path}{sep}kanjire/data"]
    fonts_dir = PACKAGE_DIR / "fonts"
    for f in sorted(fonts_dir.iterdir()):
        if f.is_file():
            args += ["--add-data", f"{f}{sep}kanjire/fonts"]
    return args


def platform_tag() -> str:
    """OS key used in artifact names + the manifest's ``platforms`` map."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


#: Archive format per platform. Linux/mac use tar.gz so the launcher keeps its
#: executable bit (a plain zip would strip it).
_ARTIFACT_EXT = {"windows": "zip", "linux": "tar.gz", "macos": "tar.gz"}


def artifact_name(tag: str | None = None) -> str:
    tag = tag or platform_tag()
    return f"{NAME}-{__version__}-{tag}.{_ARTIFACT_EXT.get(tag, 'zip')}"


def _bundle_lib_args() -> list[str]:
    """``--add-binary`` flags for native libs pyglet ``dlopen``s at runtime.

    On Linux, pyglet loads ``libGLU.so`` via ctypes; minimal systems may lack
    it, so we bundle a copy (renamed to the bare ``libGLU.so`` soname pyglet
    tries). Sources come from ``KANJIRE_BUNDLE_LIBS`` (set by the WSL build,
    colon-separated) or are auto-discovered on a normal Linux build host.
    Windows needs none of this.
    """
    if os.name == "nt":
        return []
    candidates: list[str] = [
        p for p in os.environ.get("KANJIRE_BUNDLE_LIBS", "").split(os.pathsep) if p
    ]
    if not any("libGLU" in c for c in candidates):
        for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib",
                  "/lib/x86_64-linux-gnu"):
            hits = sorted(Path(d).glob("libGLU.so*")) if Path(d).is_dir() else []
            if hits:
                candidates.append(str(hits[0]))
                break

    staging = PROJECT_ROOT / "dist" / "_bundle_libs"
    args: list[str] = []
    for src in candidates:
        srcp = Path(src)
        if not srcp.exists():
            continue
        staging.mkdir(parents=True, exist_ok=True)
        bare = srcp.name.split(".so")[0] + ".so"  # libGLU.so.1.3.1 -> libGLU.so
        dst = staging / bare
        shutil.copy2(srcp, dst)
        args += ["--add-binary", f"{dst}{os.pathsep}."]
        _log(f"  bundling native lib: {dst.name}  (from {srcp})")
    return args


def _hidden_imports() -> list[str]:
    """Per-OS modules PyInstaller occasionally misses for pyglet/TTS."""
    # fsrs is imported inside a try/except (soft dependency), which some
    # PyInstaller analyses skip - name it explicitly so the scheduler ships.
    common = ["pyglet.media.codecs.wave", "fsrs"]
    if os.name == "nt":
        return common + [
            "pyglet.media.codecs.wmf",
            "pyglet.media.drivers.directsound",
            "comtypes.gen",
        ]
    # Linux/macOS: pulse/openal audio drivers; no SAPI/comtypes/WMF (Windows).
    return common + [
        "pyglet.media.drivers.openal",
        "pyglet.media.drivers.pulse",
        "pyglet.media.drivers.silent",
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


def _targz_release(folder: Path, out: Path) -> None:
    """tar.gz the bundle, preserving Unix perms (the launcher's +x bit)."""
    if out.exists():
        out.unlink()
    with tarfile.open(out, "w:gz") as tf:
        tf.add(folder, arcname=folder.name)  # recursive; modes preserved


def _package(src: Path, tag: str) -> Path:
    out = (PROJECT_ROOT / "dist") / artifact_name(tag)
    if tag == "windows":
        _zip_release(src, out)
    else:
        _targz_release(src, out)
    return out


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


def asset_url(name: str) -> str:
    return (
        f"https://github.com/{update_config.REPO_OWNER}/{update_config.REPO_NAME}"
        f"/releases/download/{_release_tag()}/{name}"
    )


def build_combined_manifest(artifacts: dict[str, Path], notes: str) -> dict:
    """Assemble the unsigned multi-platform ``latest.json``.

    *artifacts* maps platform tag → built artifact path. The result carries a
    ``platforms`` map for current clients **and** top-level ``url``/``sha256``
    mirroring the Windows asset, so already-shipped 0.1.x (Windows-only)
    clients keep updating.
    """
    platforms: dict[str, dict] = {}
    for tag, path in artifacts.items():
        platforms[tag] = {
            "url": asset_url(path.name),
            "sha256": update_verify.sha256_file(path),
            "size": path.stat().st_size,
        }
    manifest = {
        "version": __version__,
        "notes": notes,
        "pubdate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }
    if "windows" in platforms:  # back-compat for legacy single-platform clients
        manifest.update({k: platforms["windows"][k] for k in ("url", "sha256", "size")})
    return manifest


def sign_manifest_to_file(manifest: dict) -> Path | None:
    """Sign *manifest* and write ``dist/latest.json``. None on error."""
    if not update_config.PUBLIC_KEY_HEX.strip():
        _log("ERROR: no public key baked in. Run:  python scripts/gen_update_key.py")
        return None
    if not PRIVATE_KEY_PATH.exists():
        _log(f"ERROR: private signing key not found at {PRIVATE_KEY_PATH}.")
        _log("       Run:  python scripts/gen_update_key.py")
        return None
    private_hex = PRIVATE_KEY_PATH.read_text(encoding="ascii").strip()
    signed = update_verify.sign_manifest(manifest, private_hex)
    # Sanity-check our own signature against the baked-in public key before we
    # ever upload it — a mismatch means the keypair is out of sync.
    if not update_verify.verify_manifest(signed, update_config.PUBLIC_KEY_HEX):
        _log("ERROR: signed manifest fails verification against the baked-in "
             "public key. Did you regenerate keys without rebuilding config.py?")
        return None
    out = (PROJECT_ROOT / "dist") / update_config.MANIFEST_ASSET
    out.write_text(json.dumps(signed, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"  signed manifest → {out}  (platforms: {', '.join(manifest['platforms'])})")
    return out


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def publish_assets(asset_paths: list[Path], manifest_path: Path, notes: str) -> int:
    """Create (or update) the GitHub Release and upload all assets + manifest."""
    if not _gh_available():
        _log("ERROR: the GitHub CLI ('gh') is not on PATH. Install it with:")
        _log("       winget install GitHub.cli   (then: gh auth login)")
        return 1
    repo = f"{update_config.REPO_OWNER}/{update_config.REPO_NAME}"
    tag = _release_tag()
    assets = [str(p) for p in asset_paths] + [str(manifest_path)]

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
    _log("  gh " + " ".join(cmd[1:]))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        _log("gh failed — check 'gh auth status' and that the repo exists.")
        return rc
    _log("")
    _log(f"✓ Published {tag} to https://github.com/{repo}/releases/latest")
    return 0


def build_artifact(force: bool = False) -> Path | None:
    """Build + package the bundle for the *current* OS. Returns its path."""
    if _check_prereqs():
        return None

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
        "--clean",
        *_add_data_args(),
        *_bundle_lib_args(),
    ]
    if os.name == "nt":
        cmd.append("--windowed")  # suppress the console window (Windows only)
    for mod in _hidden_imports():
        cmd += ["--hidden-import", mod]
    # PyNaCl ships a compiled libsodium extension + cffi backend used by the
    # updater's signature check; --collect-all grabs the binary reliably.
    cmd += ["--collect-all", "nacl", "--hidden-import", "_cffi_backend"]
    # fsrs's optional optimizer drags in torch (gigabytes) through imports
    # that only matter for parameter training; the shipped scheduler never
    # touches it. Excluding keeps the bundle small AND working.
    for mod in ("fsrs.optimizer", "torch", "pandas", "tqdm"):
        cmd += ["--exclude-module", mod]

    _log(f"Running PyInstaller ({platform_tag()})...")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        _log("PyInstaller failed.")
        return None

    src = dist / NAME
    if not src.exists():
        _log(f"ERROR: expected output folder {src} not found.")
        return None

    tag = platform_tag()
    out = _package(src, tag)
    size_mb = out.stat().st_size / 1_048_576
    # Sanity guard: a healthy bundle is ~30-80 MB. Anything huge means the
    # dependency graph pulled in something monstrous (torch once made a 5 GB
    # zip) - fail here, not at the GitHub upload.
    if size_mb > 300:
        _log(f"ERROR: {out.name} is {size_mb:.0f} MB - the bundle picked up "
             "an unwanted heavyweight dependency. Aborting.")
        return None
    _log(f"✓ {tag} artifact: {out.name}  ({size_mb:.1f} MB)")
    return out


def build(force: bool = False, publish: bool = False, notes: str = "") -> int:
    """Build the current-OS artifact and optionally publish it single-platform.

    For a full cross-platform release (Windows + Linux), use
    ``scripts/release.py`` instead — it builds both and signs one manifest.
    """
    art = build_artifact(force=force)
    if art is None:
        return 1
    _log("")
    _log(f"Built {art.name} for {platform_tag()}.")
    if publish:
        _log("Publishing (single-platform) to GitHub Releases…")
        manifest = build_combined_manifest({platform_tag(): art}, notes)
        mpath = sign_manifest_to_file(manifest)
        if mpath is None:
            return 1
        return publish_assets([art], mpath, notes)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="wipe dist/ and build/ before building")
    p.add_argument("--artifact-only", action="store_true",
                   help="just build the current-OS bundle and print its path "
                        "(used by release.py to build the Linux bundle in WSL)")
    p.add_argument("--publish", action="store_true",
                   help="sign latest.json and upload it + this OS's bundle "
                        "(single-platform; prefer release.py for cross-platform)")
    p.add_argument("--notes", default="",
                   help="release notes shown to players in the update banner")
    p.add_argument("--notes-from-changelog", action="store_true",
                   help=f"use the CHANGELOG.md section for the current version "
                        f"({__version__}) as the release notes")
    args = p.parse_args(argv)
    if args.artifact_only:
        art = build_artifact(force=args.force)
        if art is None:
            return 1
        print(f"ARTIFACT={art}")
        return 0
    notes = args.notes
    if not notes and args.notes_from_changelog:
        notes = notes_from_changelog()
    return build(force=args.force, publish=args.publish, notes=notes)


if __name__ == "__main__":
    sys.exit(main())
