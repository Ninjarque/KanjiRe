"""Offline tests for the auto-updater: versioning, signing, integrity, staging.

No network and no frozen build required — the GitHub fetch is monkeypatched and
the swap/script logic is exercised against temp dirs.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from kanjire.update import applier, checker, config, verify

# A throwaway keypair generated for the tests (NOT the production key).
from nacl.signing import SigningKey

_TEST_KEY = SigningKey.generate()
TEST_PRIVATE_HEX = bytes(_TEST_KEY).hex()
TEST_PUBLIC_HEX = bytes(_TEST_KEY.verify_key).hex()


# --- version comparison ------------------------------------------------- #
def test_parse_version():
    assert checker.parse_version("0.2.0") == (0, 2, 0)
    assert checker.parse_version("1.2.3-rc1") == (1, 2, 3)


@pytest.mark.parametrize("remote,local,expect", [
    ("0.2.0", "0.1.0", True),
    ("0.1.1", "0.1.0", True),
    ("0.1.0", "0.1.0", False),
    ("0.1.0", "0.2.0", False),
    ("1.0.0", "0.9.9", True),
    # Double-digit components: a *string* comparison would say "0.10.0" is
    # older than "0.9.0" and strand everyone on 0.9.x forever.
    ("0.10.0", "0.9.0", True),
    ("0.9.0", "0.10.0", False),
    ("0.11.1", "0.11.0", True),
    ("0.12.0", "0.11.1", True),
    ("1.0.0", "0.12.0", True),
])
def test_is_newer(remote, local, expect):
    assert checker.is_newer(remote, local) is expect


# --- signing / verification --------------------------------------------- #
def _manifest(**over):
    base = {
        "version": "0.2.0",
        "notes": "hi",
        # Legacy top-level fields kept for back-compat with 0.1.x (Windows) clients.
        "url": "https://h/KanjiRe-0.2.0-windows.zip",
        "sha256": "ab" * 32,
        "size": 123,
        # New multi-platform map that current clients read.
        "platforms": {
            "windows": {"url": "https://h/KanjiRe-0.2.0-windows.zip", "sha256": "ab" * 32, "size": 123},
            "linux": {"url": "https://h/KanjiRe-0.2.0-linux.tar.gz", "sha256": "cd" * 32, "size": 456},
        },
    }
    base.update(over)
    return base


def test_sign_verify_roundtrip():
    signed = verify.sign_manifest(_manifest(), TEST_PRIVATE_HEX)
    assert "signature" in signed
    assert verify.verify_manifest(signed, TEST_PUBLIC_HEX) is True


def test_verify_rejects_tampered_field():
    signed = verify.sign_manifest(_manifest(), TEST_PRIVATE_HEX)
    signed["version"] = "9.9.9"  # tamper after signing
    assert verify.verify_manifest(signed, TEST_PUBLIC_HEX) is False


def test_verify_rejects_wrong_key():
    signed = verify.sign_manifest(_manifest(), TEST_PRIVATE_HEX)
    other_pub = bytes(SigningKey.generate().verify_key).hex()
    assert verify.verify_manifest(signed, other_pub) is False


def test_verify_rejects_missing_signature():
    assert verify.verify_manifest(_manifest(), TEST_PUBLIC_HEX) is False


# --- integrity helpers -------------------------------------------------- #
def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    import hashlib
    assert verify.sha256_file(p) == hashlib.sha256(b"hello").hexdigest()


def test_safe_extract_ok(tmp_path):
    zp = tmp_path / "ok.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("KanjiRe/file.txt", "data")
    dest = tmp_path / "out"
    verify.safe_extract(zp, dest)
    assert (dest / "KanjiRe" / "file.txt").read_text() == "data"


def test_safe_extract_rejects_zip_slip(tmp_path):
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    with pytest.raises(ValueError):
        verify.safe_extract(zp, tmp_path / "out")


def test_safe_extract_tar_ok_and_perms(tmp_path):
    import io
    import tarfile
    tp = tmp_path / "ok.tar.gz"
    with tarfile.open(tp, "w:gz") as tf:
        data = b"#!/bin/sh\necho hi\n"
        info = tarfile.TarInfo("KanjiRe/KanjiRe")
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))
    dest = tmp_path / "out"
    verify.safe_extract_tar(tp, dest)
    out = dest / "KanjiRe" / "KanjiRe"
    assert out.read_bytes().startswith(b"#!/bin/sh")
    if os.name != "nt":  # exec bit is meaningful on POSIX
        assert os.access(out, os.X_OK), "tar extraction lost the executable bit"


def test_safe_extract_tar_rejects_slip(tmp_path):
    import io
    import tarfile
    tp = tmp_path / "evil.tar.gz"
    with tarfile.open(tp, "w:gz") as tf:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"bad"))
    with pytest.raises(ValueError):
        verify.safe_extract_tar(tp, tmp_path / "out")


def test_extract_archive_dispatch(tmp_path):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("k/f.txt", "x")
    verify.extract_archive(z, tmp_path / "z")
    assert (tmp_path / "z" / "k" / "f.txt").exists()
    with pytest.raises(ValueError):
        verify.extract_archive(tmp_path / "a.rar", tmp_path / "r")


# --- check_for_update (network mocked) ---------------------------------- #
@pytest.fixture
def baked_key(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_KEY_HEX", TEST_PUBLIC_HEX)


def _patch_manifest(monkeypatch, manifest):
    monkeypatch.setattr(checker, "fetch_manifest", lambda *a, **k: manifest)


def test_check_disabled_without_key(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_KEY_HEX", "")
    assert checker.check_for_update("0.1.0") is None


def test_check_returns_info_for_newer(monkeypatch, baked_key):
    signed = verify.sign_manifest(_manifest(version="0.2.0"), TEST_PRIVATE_HEX)
    _patch_manifest(monkeypatch, signed)
    info = checker.check_for_update("0.1.0")
    assert info is not None and info.version == "0.2.0"


def test_check_none_when_not_newer(monkeypatch, baked_key):
    signed = verify.sign_manifest(_manifest(version="0.1.0"), TEST_PRIVATE_HEX)
    _patch_manifest(monkeypatch, signed)
    assert checker.check_for_update("0.1.0") is None


def test_check_rejects_bad_signature(monkeypatch, baked_key):
    signed = verify.sign_manifest(_manifest(version="0.2.0"), TEST_PRIVATE_HEX)
    signed["version"] = "0.3.0"  # tamper
    _patch_manifest(monkeypatch, signed)
    assert checker.check_for_update("0.1.0") is None


def test_check_rejects_non_https_url(monkeypatch, baked_key):
    bad = _manifest(version="0.2.0", platforms={
        "windows": {"url": "http://evil/x.zip", "sha256": "ab" * 32, "size": 1},
        "linux": {"url": "http://evil/x.tar.gz", "sha256": "cd" * 32, "size": 1},
        "macos": {"url": "http://evil/x.tar.gz", "sha256": "ef" * 32, "size": 1},
    })
    _patch_manifest(monkeypatch, verify.sign_manifest(bad, TEST_PRIVATE_HEX))
    assert checker.check_for_update("0.1.0") is None


# --- multi-platform selection ------------------------------------------- #
def test_check_picks_current_platform(monkeypatch, baked_key):
    signed = verify.sign_manifest(_manifest(version="0.2.0"), TEST_PRIVATE_HEX)
    _patch_manifest(monkeypatch, signed)
    monkeypatch.setattr(checker, "current_platform", lambda: "linux")
    info = checker.check_for_update("0.1.0")
    assert info and info.url.endswith("linux.tar.gz") and info.sha256 == "cd" * 32
    monkeypatch.setattr(checker, "current_platform", lambda: "windows")
    info = checker.check_for_update("0.1.0")
    assert info and info.url.endswith("windows.zip") and info.sha256 == "ab" * 32


def test_check_none_when_no_build_for_os(monkeypatch, baked_key):
    # platforms map present but missing our OS → no update for us.
    m = _manifest(version="0.2.0", platforms={
        "windows": {"url": "https://h/w.zip", "sha256": "ab" * 32, "size": 1}})
    _patch_manifest(monkeypatch, verify.sign_manifest(m, TEST_PRIVATE_HEX))
    monkeypatch.setattr(checker, "current_platform", lambda: "linux")
    assert checker.check_for_update("0.1.0") is None


def test_legacy_manifest_windows_only(monkeypatch, baked_key):
    # Old-style manifest (no platforms map) = Windows; a Linux client must skip.
    legacy = {"version": "0.2.0", "notes": "x",
              "url": "https://h/w.zip", "sha256": "ab" * 32, "size": 1}
    _patch_manifest(monkeypatch, verify.sign_manifest(legacy, TEST_PRIVATE_HEX))
    monkeypatch.setattr(checker, "current_platform", lambda: "windows")
    assert checker.check_for_update("0.1.0") is not None
    monkeypatch.setattr(checker, "current_platform", lambda: "linux")
    assert checker.check_for_update("0.1.0") is None


def test_check_survives_network_error(monkeypatch, baked_key):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(checker, "fetch_manifest", boom)
    assert checker.check_for_update("0.1.0") is None


# --- swap-script generation --------------------------------------------- #
def test_swap_script_current_os(tmp_path):
    script = applier._swap_script(tmp_path)
    text = script.read_text(encoding="ascii")
    if os.name == "nt":
        assert script.suffix == ".bat"
        assert ":rollback" in text
        assert "tasklist" in text          # waits for the app to exit
        assert 'del "%~f0"' in text        # self-deletes
    else:
        assert script.suffix == ".sh"
        assert "kill -0" in text           # waits for the app to exit
        assert 'mv "$BACKUP" "$INSTALL"' in text  # rollback
        assert 'rm -f "$0"' in text        # self-deletes
        assert os.access(script, os.X_OK)  # chmod +x


def test_posix_swap_template_shape():
    # The POSIX swap body is fixed text; assert its safety-critical bits exist
    # regardless of which OS the tests run on.
    t = applier._SWAP_SH
    assert "kill -0" in t and "setsid" in t
    assert 'mv "$INSTALL" "$BACKUP"' in t and 'mv "$BACKUP" "$INSTALL"' in t


def test_can_self_update_writable(tmp_path):
    assert applier.can_self_update(tmp_path / "KanjiRe") is True


# --- the swap, actually executed ---------------------------------------- #
def _fake_app(dirpath, tag: str, marker):
    """A stand-in 'KanjiRe executable': on launch it stamps *tag* into marker.

    On Windows it must be a .vbs, not a .bat: `start "" file.bat` runs it via
    `cmd /K`, which leaves a console window sitting open forever. The real exe
    is a GUI binary, so this is purely a test-rig concern - but a test that
    litters the desktop with terminals is its own kind of bug.
    """
    if os.name == "nt":
        exe = dirpath / "KanjiRe.vbs"
        exe.write_text(
            'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
            f'Set f = fso.CreateTextFile("{marker}", True)\r\n'
            f'f.Write "{tag}"\r\nf.Close\r\n',
            encoding="ascii")
    else:
        exe = dirpath / "KanjiRe"
        exe.write_text(f'#!/bin/sh\nprintf %s "{tag}" > "{marker}"\n', encoding="ascii",
                       newline="\n")
        exe.chmod(0o755)
    return exe


@pytest.mark.skipif(os.name == "nt", reason="POSIX swap helper")
def test_posix_swap_does_not_wait_for_a_stuck_app_forever():
    """The helper used to wait on the app's pid with an unbounded loop.

    If the window closed but the process lingered (a stuck GL teardown, a
    non-daemon thread), it waited for eternity and the update silently never
    applied - "it just closes and does nothing". It must give the app a while,
    then insist, and swap regardless: renaming a directory is safe on POSIX even
    with the old process still alive.
    """
    t = applier._SWAP_SH
    assert "while kill -0 \"$PID\" 2>/dev/null; do sleep 0.5; done" not in t, \
        "the unbounded wait loop is back"
    assert "kill -9" in t and "app still alive" in t
    # And the relaunch can't depend on setsid existing everywhere.
    assert "nohup" in t and "command -v setsid" in t


def test_swap_replaces_the_install_and_relaunches(tmp_path, monkeypatch):
    """Run the real swap helper end-to-end against a throwaway install.

    This is the test that was missing: everything below the swap was unit-tested,
    the swap script itself never once ran, and it didn't work. It is executed
    here under the condition that actually broke it - the app's *working
    directory is the install folder* (what you get launching from Explorer),
    which on Windows is an open handle that makes renaming the folder fail.
    """
    import subprocess
    import time

    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(applier, "_staging_dir", lambda: staging)

    install = tmp_path / "KanjiRe"
    install.mkdir()
    (install / "version.txt").write_text("old", encoding="ascii")
    marker = tmp_path / "relaunched.txt"
    _fake_app(install, "old", marker)

    new_bundle = tmp_path / ".kanjire-update-new" / "KanjiRe"
    new_bundle.mkdir(parents=True)
    (new_bundle / "version.txt").write_text("new", encoding="ascii")
    _fake_app(new_bundle, "new", marker)
    exe = install / ("KanjiRe.vbs" if os.name == "nt" else "KanjiRe")

    # The "running app": a process whose cwd is INSIDE the install directory.
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                           cwd=str(install))
    try:
        applier.apply_and_restart(new_bundle, target=install, pid=app.pid, exe=exe)
        app.terminate()
        app.wait(timeout=20)

        deadline = time.time() + 45
        while time.time() < deadline:
            if marker.exists() and (install / "version.txt").read_text() == "new":
                break
            time.sleep(0.25)
    finally:
        if app.poll() is None:
            app.kill()

    log = (staging / "update.log")
    detail = log.read_text(errors="replace") if log.exists() else "(no log)"
    assert (install / "version.txt").read_text(encoding="ascii") == "new", (
        f"the swap did not replace the install: {detail}"
    )
    assert marker.exists() and marker.read_text(encoding="ascii").strip() == "new", (
        f"the app was not relaunched from the NEW bundle: {detail}"
    )
    assert not (tmp_path / "KanjiRe.old").exists(), "backup left behind on success"
