"""Download, verify, and apply an update, then relaunch the app.

Windows can't overwrite the running ``KanjiRe.exe`` / ``_internal`` files while
the process holds them, so applying an update is a two-process dance:

1. We download the new zip to ``%LOCALAPPDATA%/KanjiRe/updates`` and verify its
   SHA-256 against the (already signature-verified) manifest.
2. We extract it next to the current install - *same volume*, so the final
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
from kanjire.update import checker, config, verify
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
    # Same certifi fallback as the manifest fetch: no point detecting an update
    # we then can't download.
    with checker.urlopen(req, timeout=timeout) as resp:
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
    # Match the artifact's archive type (Windows .zip / Linux .tar.gz) so the
    # extractor and on-disk perms come out right.
    suffix = ".tar.gz" if info.url.lower().endswith((".tar.gz", ".tgz")) else ".zip"
    archive = _staging_dir() / f"KanjiRe-{info.version}{suffix}"
    download(info, archive, progress=progress)

    # Extract to a sibling of the install so the final swap is a same-volume
    # rename. A fresh, cleaned folder each time avoids stale leftovers.
    extract_root = target.parent / ".kanjire-update-new"
    import shutil

    if extract_root.exists():
        shutil.rmtree(extract_root, ignore_errors=True)
    verify.extract_archive(archive, extract_root)
    new_bundle = extract_root / _BUNDLE_DIRNAME
    if not (new_bundle / Path(sys.executable).name).exists() and not new_bundle.is_dir():
        raise ValueError(f"unexpected archive layout: {new_bundle} missing")
    return new_bundle


_SWAP_SH = r"""#!/usr/bin/env bash
# Args: $1 pid  $2 install dir  $3 new bundle dir  $4 exe to relaunch.
PID="$1"; INSTALL="$2"; NEWDIR="$3"; EXE="$4"; BACKUP="${INSTALL}.old"
LOG="$(dirname "$0")/update.log"

# Never stand inside the directory we're about to rename.
cd / 2>/dev/null

# --- wait for the running app to exit ---
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done

rm -rf "$BACKUP"

# --- back up current install (same-volume rename) ---
if ! mv "$INSTALL" "$BACKUP" 2>>"$LOG"; then
    # couldn't even back up; relaunch what's still there
    echo "FAILED: could not rename $INSTALL aside; update not applied" >>"$LOG"
    setsid "$EXE" >/dev/null 2>&1 < /dev/null &
    exit 1
fi

# --- move the new bundle into place; roll back on failure ---
if mv "$NEWDIR" "$INSTALL" 2>>"$LOG"; then
    rm -rf "$BACKUP"
    echo "OK: updated $INSTALL" >>"$LOG"
else
    echo "FAILED: could not move $NEWDIR into place; rolled back" >>"$LOG"
    rm -rf "$INSTALL"; mv "$BACKUP" "$INSTALL"
fi

# --- relaunch detached + clean up scratch and self ---
setsid "$EXE" >/dev/null 2>&1 < /dev/null &
rm -rf "$(dirname "$NEWDIR")"
rm -f "$0"
"""


def _swap_script(staging: Path) -> Path:
    """Write the self-deleting swap/rollback helper for the current OS.

    Arguments (all quoted by the caller): pid, install dir, new bundle dir,
    exe to relaunch. Windows gets a ``.bat``; POSIX gets a ``.sh``.
    """
    if os.name != "nt":
        sh = staging / "kanjire_swap.sh"
        sh.write_text(_SWAP_SH, encoding="ascii", newline="\n")
        sh.chmod(0o755)
        return sh
    bat = staging / "kanjire_swap.bat"
    bat.write_text(
        r"""@echo off
setlocal enabledelayedexpansion
set "PID=%~1"
set "INSTALL=%~2"
set "NEWDIR=%~3"
set "EXE=%~4"
set "BACKUP=%INSTALL%.old"
set "LOG=%~dp0update.log"

rem A process's current directory is an OPEN HANDLE on Windows: standing inside
rem the install folder would make the rename below fail. Move to this script's
rem own folder (the staging dir) before touching anything.
cd /d "%~dp0"
echo swap start: pid=%PID% install="%INSTALL%" new="%NEWDIR%">>"%LOG%"

if exist "%BACKUP%" rmdir /s /q "%BACKUP%"

rem --- wait for the app to let go, by RETRYING THE RENAME ---
rem Do not poll the pid: `tasklist | find` and `timeout` both need a console,
rem and this helper is launched DETACHED (no console) - the wait loop that did
rem so never terminated, so the update silently never applied. Renaming the
rem folder fails while the old exe is still mapped, and succeeds the moment it
rem isn't, which is exactly the condition we care about. `ping` is the sleep
rem that works without a console.
set /a TRIES=0
:retry
move "%INSTALL%" "%BACKUP%" >>"%LOG%" 2>&1
if not errorlevel 1 goto backedup
set /a TRIES+=1
if !TRIES! GEQ 120 goto fail
ping -n 2 127.0.0.1 >NUL
goto retry

:backedup
rem --- move the new bundle into place ---
move "%NEWDIR%" "%INSTALL%" >>"%LOG%" 2>&1
if errorlevel 1 goto rollback

rem --- success: relaunch + drop the backup ---
echo OK: updated "%INSTALL%">>"%LOG%"
start "" /b "%EXE%"
rmdir /s /q "%BACKUP%" >NUL 2>&1
goto cleanup

:rollback
echo FAILED: could not move "%NEWDIR%" into place; rolled back>>"%LOG%"
if exist "%INSTALL%" rmdir /s /q "%INSTALL%" >NUL 2>&1
move "%BACKUP%" "%INSTALL%" >NUL 2>&1
start "" /b "%EXE%"
goto cleanup

:fail
rem couldn't even back up; just relaunch what's still there
echo FAILED: could not rename "%INSTALL%" aside; update not applied>>"%LOG%"
start "" /b "%EXE%"

:cleanup
rem best-effort: remove the extract scratch dir
for %%I in ("%NEWDIR%\..") do rmdir /s /q "%%~fI" >NUL 2>&1
endlocal
del "%~f0"
""",
        encoding="ascii",
    )
    return bat


def apply_and_restart(
    new_bundle: Path,
    *,
    target: Path | None = None,
    pid: int | None = None,
    exe: Path | None = None,
) -> None:
    """Launch the detached swap helper and ask the caller to exit immediately.

    The caller (the app) must close its window / DB handles and exit right
    after this returns so the helper can take the file lock and swap folders.

    ``pid``/``exe`` exist so the swap can be driven end-to-end by a test
    against a throwaway install; the app never passes them.
    """
    target = target or install_dir()
    exe = exe or (target / Path(sys.executable).name)
    staging = _staging_dir()
    script = _swap_script(staging)
    args = [str(pid or os.getpid()), str(target), str(new_bundle), str(exe)]
    # The helper MUST NOT run from inside the folder it is about to rename:
    # a process's working directory is an open handle on Windows, so inheriting
    # ours (the install dir, when launched from Explorer) made the very first
    # `move` fail with access-denied. The script then fell through to its
    # "couldn't back up" branch and relaunched the OLD build - the update looked
    # like it applied and silently didn't.
    cwd = str(staging)
    # Detach fully so the helper outlives this process and can swap the folder.
    if os.name == "nt":
        # CREATE_NO_WINDOW as well as DETACHED_PROCESS: detaching alone still
        # let cmd.exe allocate its own console, so applying an update flashed a
        # blank black window at the player.
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP
                 | 0x00000008          # DETACHED_PROCESS
                 | subprocess.CREATE_NO_WINDOW)
        subprocess.Popen(
            ["cmd", "/c", str(script), *args],
            creationflags=flags, close_fds=True, cwd=cwd,
        )
    else:
        subprocess.Popen(
            ["/bin/bash", str(script), *args],
            start_new_session=True, close_fds=True, cwd=cwd,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
