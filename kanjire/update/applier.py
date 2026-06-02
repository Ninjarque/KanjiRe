"""Download, verify, and apply an update, then relaunch the app.

Windows can't overwrite the running ``KanjiRe.exe`` / ``_internal`` files while
the process holds them, so applying an update is a two-process dance:

1. We download the new zip to ``%LOCALAPPDATA%/KanjiRe/updates`` and verify its
   SHA-256 against the (already signature-verified) manifest.
2. We extract it next to the current install — *same volume*, so the final
   swap is an instant rename rather than a slow cross-drive copy.
3. We launch a detached helper ``.bat`` that waits for this process to exit,
   renames the old folder aside as a backup, moves the new folder into place,
   relaunches the exe, and deletes the backup. **If the move fails it rolls the
   backup back**, so a half-applied update can never brick the install.

Everything is driven from explicit paths so the risky bits are unit-testable
without actually being a frozen build.
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from kanjire import __version__
from kanjire.update import config, verify
from kanjire.update.checker import UpdateInfo

#: Subfolder name the release zip extracts to (matches build_release's zip layout).
_BUNDLE_DIRNAME = "KanjiRe"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def install_dir() -> Path:
    """Folder that contains the running ``KanjiRe.exe`` (the swap target)."""
    return Path(sys.executable).resolve().parent


def _staging_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) / "KanjiRe" / "updates" if base else Path.home() / ".kanjire" / "updates"
    root.mkdir(parents=True, exist_ok=True)
    return root


def can_self_update(target: Path | None = None) -> bool:
    """True if we can actually rename *target*'s folder in place.

    Installs under ``C:\\Program Files`` (or any read-only location) fail the
    write test; callers should tell the player to move the folder somewhere
    writable rather than silently failing.
    """
    target = target or install_dir()
    parent = target.parent
    probe = parent / ".kanjire_write_test"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def download(
    info: UpdateInfo,
    dest: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    timeout: int | None = None,
) -> Path:
    """Download ``info.url`` to *dest* and verify its SHA-256. Returns *dest*.

    Raises :class:`ValueError` on a non-HTTPS URL or a hash mismatch, so a
    corrupted or tampered download never reaches the extract/swap stage.
    """
    if not info.url.lower().startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {info.url!r}")
    timeout = config.HTTP_TIMEOUT if timeout is None else timeout
    req = urllib.request.Request(
        info.url, headers={"User-Agent": f"KanjiRe/{__version__}"}
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or info.size or 0)
        done = 0
        with open(tmp, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    got = verify.sha256_file(tmp)
    if got.lower() != info.sha256.lower():
        tmp.unlink(missing_ok=True)
        raise ValueError(f"sha256 mismatch: expected {info.sha256}, got {got}")
    os.replace(tmp, dest)
    return dest


def stage(
    info: UpdateInfo,
    *,
    target: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download + verify + extract next to the install. Returns the new bundle dir.

    The returned path is the extracted ``KanjiRe/`` folder, ready to be moved
    into place by :func:`apply_and_restart`.
    """
    target = target or install_dir()
    zip_path = _staging_dir() / f"KanjiRe-{info.version}.zip"
    download(info, zip_path, progress=progress)

    # Extract to a sibling of the install so the final swap is a same-volume
    # rename. A fresh, cleaned folder each time avoids stale leftovers.
    extract_root = target.parent / ".kanjire-update-new"
    import shutil

    if extract_root.exists():
        shutil.rmtree(extract_root, ignore_errors=True)
    verify.safe_extract(zip_path, extract_root)
    new_bundle = extract_root / _BUNDLE_DIRNAME
    if not (new_bundle / Path(sys.executable).name).exists() and not new_bundle.is_dir():
        raise ValueError(f"unexpected archive layout: {new_bundle} missing")
    return new_bundle


def _swap_script(staging: Path) -> Path:
    """Write the self-deleting swap/rollback ``.bat`` and return its path.

    Arguments (all quoted by the caller): %1 pid, %2 install dir, %3 new bundle
    dir, %4 exe to relaunch.
    """
    bat = staging / "kanjire_swap.bat"
    bat.write_text(
        r"""@echo off
setlocal
set "PID=%~1"
set "INSTALL=%~2"
set "NEWDIR=%~3"
set "EXE=%~4"
set "BACKUP=%INSTALL%.old"

rem --- wait for the running app to exit ---
:waitloop
tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto waitloop
)

if exist "%BACKUP%" rmdir /s /q "%BACKUP%"

rem --- back up current install (same-volume rename) ---
move "%INSTALL%" "%BACKUP%" >NUL 2>&1
if errorlevel 1 goto fail

rem --- move the new bundle into place ---
move "%NEWDIR%" "%INSTALL%" >NUL 2>&1
if errorlevel 1 goto rollback

rem --- success: relaunch + drop the backup ---
start "" "%EXE%"
rmdir /s /q "%BACKUP%" >NUL 2>&1
goto cleanup

:rollback
if exist "%INSTALL%" rmdir /s /q "%INSTALL%" >NUL 2>&1
move "%BACKUP%" "%INSTALL%" >NUL 2>&1
start "" "%EXE%"
goto cleanup

:fail
rem couldn't even back up; just relaunch what's still there
start "" "%EXE%"

:cleanup
rem best-effort: remove the extract scratch dir
for %%I in ("%NEWDIR%\..") do rmdir /s /q "%%~fI" >NUL 2>&1
endlocal
del "%~f0"
""",
        encoding="ascii",
    )
    return bat


def apply_and_restart(new_bundle: Path, *, target: Path | None = None) -> None:
    """Launch the detached swap helper and ask the caller to exit immediately.

    The caller (the app) must close its window / DB handles and exit right
    after this returns so the helper can take the file lock and swap folders.
    """
    target = target or install_dir()
    exe = target / Path(sys.executable).name
    bat = _swap_script(_staging_dir())
    # Detach fully so the helper outlives this process.
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    subprocess.Popen(
        ["cmd", "/c", str(bat), str(os.getpid()), str(target), str(new_bundle), str(exe)],
        creationflags=flags,
        close_fds=True,
    )
